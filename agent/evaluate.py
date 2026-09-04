"""Evaluate twenty questions against Fabric MCP, recordings, or explicit local fixtures.

Python 3.11+. Import has no network/authentication side effects. The dry-run branch
reads and validates input artifacts only: no credential, socket, report or cache writes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

# Direct-script execution must disable caches BEFORE importing local/optional modules.
# For module execution use Python's -B switch, which also covers package discovery.
sys.dont_write_bytecode = True

# Support both `python -m agent.evaluate` and `python agent/evaluate.py`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.evaluation import (  # noqa: E402
    ROOT,
    Bundle,
    Question,
    compile_prompt,
    digest,
    fixture_answer,
    grade,
    load_bundle,
    parse_json,
    read_questions,
)
from agent.runtime import (  # noqa: E402
    EvaluationError,
    Identity,
    device_prompt,
    guid,
    query_live,
    validate_claims,
)

BLOCKER = "BLOCKED_PREVIEW_RAW_GRAPH_SECURITY_NOT_CERTIFIED"


@dataclass(frozen=True)
class Settings:
    """Non-secret evaluator settings; deliberately excludes deployment credentials."""

    data_dir: Path
    identity: Identity | None
    workspace_id: str | None
    agent_id: str | None

    @property
    def endpoint(self) -> str:
        """Return only the documented runtime route, never a management API route."""
        if not self.workspace_id or not self.agent_id:
            raise EvaluationError("LIVE_REQUIRES_WORKSPACE_AND_AGENT_IDS")
        return (f"https://api.fabric.microsoft.com/v1/mcp/workspaces/{self.workspace_id}"
                f"/dataagents/{self.agent_id}/agent")


def settings(args: argparse.Namespace, *, live: bool) -> Settings:
    """Load selected dotenv without interpolation; process environment wins."""
    from dotenv import dotenv_values

    config = Path(args.config) if args.config else ROOT / ".env"
    if args.config and not config.is_file():
        raise EvaluationError("CONFIG_FILE_NOT_FOUND")
    env = {**(dotenv_values(config, interpolate=False) if config.is_file() else {}), **os.environ}

    def value(key: str) -> str:
        """Read only a named setting, not a deployment-secret fallback."""
        return str(env.get(key) or "").strip()

    tenant = value("HARBOR_TENANT_ID")
    user_tenant = value("HARBOR_USER_TENANT_ID")
    if user_tenant and user_tenant.lower() != tenant.lower():
        raise EvaluationError("HARBOR_USER_TENANT_MUST_MATCH_HARBOR_TENANT")
    identity = None
    identity_values = [tenant, value("HARBOR_PARTNER_OBJECT_ID"), value("HARBOR_USER_CLIENT_ID")]
    identity_keys = ("HARBOR_TENANT_ID", "HARBOR_PARTNER_OBJECT_ID", "HARBOR_USER_CLIENT_ID")
    if live or all(identity_values):
        identity = Identity(*(guid(v, key) for v, key in zip(identity_values, (
            "HARBOR_TENANT_ID", "HARBOR_PARTNER_OBJECT_ID", "HARBOR_USER_CLIENT_ID"), strict=True)))
    else:
        # Offline planning accepts incomplete live setup but validates supplied IDs.
        for supplied, key in zip(identity_values, identity_keys, strict=True):
            if supplied:
                guid(supplied, key)
    workspace = args.workspace_id or value("HARBOR_WORKSPACE_ID")
    preferred_agent, legacy_agent = value("HARBOR_AGENT_ID"), value("HARBOR_DATA_AGENT_ID")
    if not args.agent_id and preferred_agent and legacy_agent and preferred_agent.lower() != legacy_agent.lower():
        raise EvaluationError("CONFLICTING_HARBOR_AGENT_ID_ALIASES")
    agent = args.agent_id or preferred_agent or legacy_agent
    if live or workspace:
        workspace = guid(workspace, "HARBOR_WORKSPACE_ID")
    if live or agent:
        agent = guid(agent, "HARBOR_AGENT_ID")
    data = Path(args.data_dir or value("DATA_DIR") or "data").expanduser()
    if not data.is_absolute():
        data = ROOT / data
    return Settings(data.resolve(), identity, workspace or None, agent or None)


def recorded(path: Path, questions: list[Question], repetitions: int) -> dict[tuple[int, str], dict[str, Any]]:
    """Read separately sourced recordings; never label them live or count readiness."""
    rows: dict[tuple[int, str], dict[str, Any]] = {}
    ids = {q.id for q in questions}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = parse_json(line)
        if not isinstance(row, dict) or set(row) - {"repetition", "question_id", "response", "error"}:
            raise EvaluationError("INVALID_RECORDING_FIELDS")
        repetition, question_id = row.get("repetition"), row.get("question_id")
        if (type(repetition) is not int or repetition not in range(1, repetitions + 1)
                or not isinstance(question_id, str) or question_id not in ids):
            raise EvaluationError("INVALID_RECORDING_KEY")
        if not isinstance(row.get("response", ""), str) or ("error" in row and not isinstance(row["error"], str)):
            raise EvaluationError("INVALID_RECORDING_TEXT")
        key = repetition, question_id
        if key in rows:
            raise EvaluationError("DUPLICATE_RECORDING")
        rows[key] = row
    if set(rows) != {(rep, q.id) for rep in range(1, repetitions + 1) for q in questions}:
        raise EvaluationError("RECORDINGS_MUST_COVER_EVERY_QUESTION_AND_REPETITION")
    return rows


def report_base(bundle: Bundle, cfg: Settings, source: str, questions_path: Path,
                instructions_path: Path, repetitions: int) -> dict[str, Any]:
    """Build a sanitized provenance header; no tokens, secrets or answer bodies."""
    return {
        "source": source, "started_at": datetime.now(UTC).isoformat(),
        "snapshot": bundle.expected["as_of"], "seed": bundle.manifest["seed"],
        "hashes": {**bundle.hashes, "instructions": digest(instructions_path.read_bytes()),
                   "questions": digest(questions_path.read_bytes()),
                   "evaluator": {p.name: digest(p.read_bytes()) for p in
                                 sorted((ROOT / "agent").glob("*.py"))}},
        "workspace_id": cfg.workspace_id, "agent_id": cfg.agent_id,
        "instructions_binding": "LOCAL_HASH_ONLY_NOT_VERIFIED_DEPLOYED",
        "identity": {"verified": False}, "repetitions": repetitions,
        "results": [], "all_passed": False, "three_consecutive_live_rehearsals": False,
        "live_readiness": False, "graph_security_certification": BLOCKER,
        "portal_word_proof": "NOT_PERFORMED", "errors": [],
    }


async def execute(args: argparse.Namespace, cfg: Settings, bundle: Bundle,
                  questions: list[Question], report: dict[str, Any],
                  recordings: dict[tuple[int, str], dict[str, Any]]) -> None:
    """Run complete consecutive sweeps, retaining every error as a failure."""
    from agent.runtime import SCOPE

    credential: Any = None
    if report["source"] == "LIVE_MCP":
        from azure.identity import DeviceCodeCredential

        if cfg.identity is None:
            raise EvaluationError("DELEGATED_IDENTITY_REQUIRED")
        # No DefaultAzureCredential, client secret, SP fallback, or persistent cache.
        credential = DeviceCodeCredential(tenant_id=cfg.identity.tenant_id,
                                          client_id=cfg.identity.client_id,
                                          prompt_callback=device_prompt, timeout=600)
    try:
        for repetition in range(1, args.repetitions + 1):
            history: dict[str, tuple[str, str]] = {}
            for question in questions:
                started = datetime.now(UTC).isoformat()
                clock = time.monotonic()
                response = ""
                failures: list[str] = []
                try:
                    prompt = compile_prompt(question, bundle, history)
                    if report["source"] == "OFFLINE_NOT_AGENT":
                        response = fixture_answer(bundle, question)
                    elif report["source"] == "RECORDED_NOT_LIVE":
                        row = recordings[repetition, question.id]
                        if row.get("error"):
                            raise EvaluationError("RECORDED_TRANSPORT_ERROR")
                        response = row.get("response", "")
                    else:
                        if cfg.identity is None:
                            raise EvaluationError("DELEGATED_IDENTITY_REQUIRED")
                        token = credential.get_token(SCOPE)
                        verified = validate_claims(token.token, cfg.identity)
                        report["identity"] = {**verified, "verified": True,
                                              "claims_check": "INTROSPECTION_NOT_CUSTOM_JWT_VERIFICATION"}
                        response = await query_live(cfg.endpoint, token.token, prompt, args.timeout)
                        # No token value is ever added to report, prompts, or exception diagnostics.
                        del token
                    failures = grade(bundle, question, response)
                    if not failures and question.id in ("q1", "q4"):
                        # Store the authored question, not the context envelope (no recursive replay).
                        history[question.id] = question.prompt, response
                except EvaluationError as exc:
                    failures = [str(exc)]
                except Exception:
                    # SDK errors can embed URLs, response bodies, or auth material. Do not log them.
                    failures = ["AUTH_TRANSPORT_OR_RESPONSE_FAILURE"]
                report["results"].append({
                    "repetition": repetition, "question_id": question.id,
                    "passed": not failures, "failures": failures,
                    "started_at": started, "finished_at": datetime.now(UTC).isoformat(),
                    "elapsed_seconds": round(time.monotonic() - clock, 3),
                    "response_sha256": digest(response.encode("utf-8")),
                    "response_characters": len(response),
                })
                if any(f.startswith("TOKEN_") for f in failures):
                    report["errors"].append("IDENTITY_VALIDATION_ABORTED_RUN")
                    return
    finally:
        if credential is not None:
            credential.close()


def finish_report(report: dict[str, Any], expected_count: int) -> None:
    """Separate functional rehearsal success from blocked live security certification."""
    rows = report["results"]
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["all_passed"] = bool(rows) and len(rows) == expected_count and not report["errors"] and all(
        row["passed"] for row in rows)
    report["three_consecutive_live_rehearsals"] = (
        report["source"] == "LIVE_MCP" and report["identity"].get("verified", False)
        and report["repetitions"] >= 3 and report["all_passed"])
    # Even successful delegated MCP tests cannot certify bypass/raw-graph security.
    report["live_readiness"] = False


def write_report(report: dict[str, Any]) -> None:
    """Write only sanitized metadata and diagnostic codes under the ignored reports directory."""
    directory = ROOT / "reports"
    directory.mkdir(exist_ok=True)
    stem = "agent-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    (directory / (stem + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# Agent evaluation", "", f"Source: **{report['source']}**",
             f"All responses passed: **{report['all_passed']}**",
             f"Three consecutive live sweeps: **{report['three_consecutive_live_rehearsals']}**",
             f"Security certification: **{BLOCKER}**", "",
             "No portal/Word proof. Local instruction hash is not proof of deployed instructions.",
             "No raw answers, tokens, device codes, exception bodies or secrets are stored.", "",
             "## Provenance", "", "```json",
             json.dumps({k: v for k, v in report.items() if k != "results"}, indent=2), "```", "",
             "| Run | Question | Pass | Seconds | Failures |", "|---|---|---|---|---|"]
    lines.extend(f"| {r['repetition']} | {r['question_id']} | {r['passed']} | "
                 f"{r['elapsed_seconds']} | {', '.join(r['failures'])} |" for r in report["results"])
    (directory / (stem + ".md")).write_text("\n".join(lines) + "\n", encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    """Expose mutually exclusive execution modes and bounded rehearsal settings."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", help="Selected dotenv; process environment overrides it")
    p.add_argument("--workspace-id")
    p.add_argument("--agent-id")
    p.add_argument("--data-dir")
    p.add_argument("--repetitions", type=int, default=3)
    p.add_argument("--repetitions3", action="store_const", const=3, dest="repetitions",
                   help="Shorthand for --repetitions 3")
    p.add_argument("--timeout", type=float, default=120.0)
    modes = p.add_mutually_exclusive_group()
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--offline-self-test", action="store_true")
    modes.add_argument("--responses-jsonl", type=Path)
    return p


def main(argv: list[str] | None = None) -> int:
    """Return nonzero for any failed/missing response; dry-run never writes reports."""
    args = parser().parse_args(argv)
    if not 1 <= args.repetitions <= 20 or not math.isfinite(args.timeout) or not 0 < args.timeout <= 3600:
        print("INVALID_REPETITIONS_OR_TIMEOUT", file=sys.stderr)
        return 2
    # Suppress third-party HTTP/auth debug output; the device callback remains stdout.
    for name in ("azure", "httpx", "httpcore", "mcp"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    report: dict[str, Any] | None = None
    try:
        source = ("OFFLINE_NOT_AGENT" if args.offline_self_test else
                  "RECORDED_NOT_LIVE" if args.responses_jsonl else "LIVE_MCP")
        cfg = settings(args, live=source == "LIVE_MCP" and not args.dry_run)
        questions_path = ROOT / "agent/example_questions.jsonl"
        instructions_path = ROOT / "agent/agent_instructions.md"
        questions = read_questions(questions_path)
        bundle = load_bundle(cfg.data_dir)
        if not instructions_path.read_text(encoding="utf-8").strip():
            raise EvaluationError("EMPTY_AGENT_INSTRUCTIONS")
        if args.dry_run:
            # Compile context structure using placeholders, never a generated oracle answer.
            plan = [{"id": q.id, "context": list(q.context), "expect": q.expect,
                     "prompt_sha256": digest(compile_prompt(q, bundle, {
                         "q1": (questions[0].prompt, "<preceding passed live answer>"),
                         "q4": (questions[3].prompt, "<preceding passed live answer>"),
                     }).encode("utf-8"))} for q in questions]
            print(json.dumps({"source": "DRY_RUN_NO_AUTH_NO_NETWORK_NO_WRITES", "plan": plan,
                              "snapshot": bundle.expected["as_of"], "seed": bundle.manifest["seed"],
                              "hashes": {**bundle.hashes, "instructions": digest(instructions_path.read_bytes()),
                                         "questions": digest(questions_path.read_bytes())},
                              "repetitions": args.repetitions,
                              "live_configuration_complete": bool(cfg.identity and cfg.agent_id and cfg.workspace_id),
                              "live_readiness": False, "security": BLOCKER}, indent=2))
            return 0
        report = report_base(bundle, cfg, source, questions_path, instructions_path, args.repetitions)
        recordings = recorded(args.responses_jsonl, questions, args.repetitions) if args.responses_jsonl else {}
        if args.responses_jsonl:
            report["hashes"]["recorded_responses"] = digest(args.responses_jsonl.read_bytes())
        asyncio.run(execute(args, cfg, bundle, questions, report, recordings))
    except EvaluationError as exc:
        if report is None:
            print(str(exc), file=sys.stderr)
            return 2
        report["errors"].append(str(exc))
    except Exception:
        if report is None:
            print("INPUT_CONTRACT_OR_DEPENDENCY_FAILURE", file=sys.stderr)
            return 2
        report["errors"].append("INPUT_AUTH_OR_TRANSPORT_FAILURE")
    if report is None:
        return 2
    finish_report(report, 20 * args.repetitions)
    try:
        write_report(report)
    except OSError:
        print("REPORT_WRITE_FAILURE", file=sys.stderr)
        return 2
    print(f"{report['source']}: {'PASS' if report['all_passed'] else 'FAIL'}; {BLOCKER}")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    # Avoid module bytecode writes on direct script invocation, including dry-run.
    sys.dont_write_bytecode = True
    raise SystemExit(main())
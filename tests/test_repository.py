"""Offline repository contracts: no cloud, Spark execution, renderer, or agent claims."""

from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlsplit

import nbformat
import pytest
import yaml
from generators.common import SPECS

from fabric.ontology_adapter import load_contract, relationship_projections, required_gold_tables

ROOT = Path(__file__).resolve().parents[1]
DOCS = (
    "README.md", "docs/01-architecture.md", "docs/03-deployment-guide.md",
    "docs/05-reuse-for-your-own-domain.md", "docs/img/README.md",
)
REQUIRED_PATHS = (
    "LICENSE", "pyproject.toml", "requirements.txt", ".env.example",
    *DOCS, "docs/02-ontology-design.md", "docs/04-demo-script.md",
    "docs/img/architecture.mmd", "docs/img/architecture.svg",
    "docs/img/leakage.mmd", "docs/img/leakage.svg",
    "data/manifest.json", "data/schema.json", "data/expected_answers.json",
    "data-generator/generate.py", "data-generator/README.md",
    "data-generator/config/firms.yaml", "data-generator/config/clients_matters.yaml",
    "data-generator/config/simulation.yaml", "data-generator/generators/common.py",
    "data-generator/generators/validation.py", "data-generator/generators/security.py",
    "data-generator/generators/expected.py", "data-generator/generators/storage.py",
    "data-generator/tests/test_contract.py", "data-generator/tests/test_finance.py",
    "data-generator/tests/test_generation.py", "data-generator/tests/test_security.py",
    "data-generator/tests/test_ontology_sources.py", "data-generator/tests/test_invariants.py",
    "fabric/config.py", "fabric/deploy.py", "fabric/fabric_client.py", "fabric/state.py",
    "fabric/ontology_adapter.py", "fabric/ontology/ontology.yaml",
    "fabric/ontology/relationships.yaml", "fabric/ontology/measures.yaml",
    "fabric/ontology/security.yaml", "fabric/notebooks/01_bronze_to_silver.ipynb",
    "fabric/notebooks/02_silver_to_gold.ipynb",
    "fabric/steps/01_create_workspaces.py", "fabric/steps/02_create_lakehouses.py",
    "fabric/steps/03_load_bronze.py", "fabric/steps/04_run_transformations.py",
    "fabric/steps/05_create_external_share.py", "fabric/steps/06_create_shortcuts.py",
    "fabric/steps/07_deploy_ontology.py", "fabric/steps/08_create_data_agent.py",
    "fabric/steps/99_teardown.py", "agent/__init__.py", "agent/agent_instructions.md",
    "agent/example_questions.jsonl", "agent/evaluate.py", "agent/evaluation.py",
    "agent/runtime.py", "scripts/setup.ps1", "scripts/setup.sh",
    "scripts/run_all.ps1", "scripts/run_all.sh", "scripts/validate_environment.py",
    "tests/test_evaluation.py", "tests/test_fabric_client.py",
    "tests/test_fabric_deployment.py", "tests/test_fabric_integration.py",
    "tests/test_ontology.py", "tests/test_repository.py",
)


def test_required_repository_paths() -> None:
    """Require exact deliverables; missing integration files are failures, not skips."""
    missing = [path for path in REQUIRED_PATHS if not (ROOT / path).is_file()]
    assert not missing, f"Missing required repository files: {missing}"


@pytest.mark.parametrize("relative", DOCS)
def test_documentation_relative_links_resolve(relative: str) -> None:
    path = ROOT / relative
    text = re.sub(r"```.*?```", "", path.read_text(encoding="utf-8"), flags=re.S)
    for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)\)", text):
        url = urlsplit(target)
        if url.scheme or not url.path:
            continue
        destination = (path.parent / unquote(url.path)).resolve()
        assert destination.is_relative_to(ROOT), (relative, target)
        assert destination.exists(), (relative, target)


@pytest.mark.parametrize("name", ("01_bronze_to_silver", "02_silver_to_gold"))
def test_notebook_nbformat_and_every_code_cell_ast(name: str) -> None:
    """Validate original bytes without repairing notebook metadata or running Spark."""
    path = ROOT / "fabric/notebooks" / f"{name}.ipynb"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document.get("nbformat") == 4, f"{path.name}: missing nbformat=4"
    nbformat.validate(document, version=4)
    cells = [cell for cell in document["cells"] if cell["cell_type"] == "code"]
    assert cells
    trees = [ast.parse(cell["source"] if isinstance(cell["source"], str)
                       else "".join(cell["source"]), filename=f"{path.name}:cell{index}")
             for index, cell in enumerate(cells, 1)]
    assert any(isinstance(node, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "CONFIG_JSON" for t in node.targets)
               and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
               for node in trees[0].body)


def test_yaml_projections_use_actual_source_schema() -> None:
    """Check current limited expression vocabulary, not general Spark SQL semantics."""
    schema = json.loads((ROOT / "data/schema.json").read_text(encoding="utf-8"))["tables"]
    columns = {table: {field["name"] for field in spec["spark_schema"]["fields"]}
               for table, spec in schema.items()}
    source_columns = {table: {field.split(":")[0] for field in spec.split()}
                      for table, spec in SPECS.items()}
    assert columns == source_columns, "Generated schema must match the actual generator contract"
    contract = load_contract(ROOT / "fabric/ontology/ontology.yaml")
    assert len(contract["entities"]) == 14
    assert "MatterAccess" in {entity["name"] for entity in contract["entities"]}
    assert len(contract["measures"]) == 7
    sql_words = {"cast", "as", "decimal", "case", "when", "then", "else", "end",
                 "concat", "year", "quarter", "date_format", "is", "not", "null", "and", "or"}

    def references(expression: str) -> set[str]:
        without_literals = re.sub(r"'(?:''|[^'])*'", "", expression)
        return {word for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", without_literals)
                if word.lower() not in sql_words}

    projected = {table: set(names) for table, names in columns.items()}
    for entity in contract["entities"]:
        table = entity["table"]
        assert table in columns
        for prop in entity["properties"].values():
            used = references(prop["expression"]) if "expression" in prop else {prop["column"]}
            assert used <= columns[table], (entity["name"], used - columns[table])
            projected[table].add(prop["column"])
    for table, names in contract["gold"]["new_source_fields"].items():
        assert set(names) <= columns[table], (table, set(names) - columns[table])
    for relation in contract["relationships"]:
        available = projected[relation["table"]]
        assert {relation["source_column"], relation["target_column"]} <= available
        if relation.get("filter"):
            assert references(relation["filter"]) <= available
    for edge in relationship_projections(contract):
        assert set(edge["columns"]) <= projected[edge["source_table"]]
        projected[edge["table"]] = set(edge["columns"])
    assert set(required_gold_tables(contract)) <= set(projected)


def test_readme_demo_card_matches_current_expected_answers() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    expected = json.loads((ROOT / "data/expected_answers.json").read_text(encoding="utf-8"))
    harbor = expected["firms"]["harbor"]
    assert expected["as_of"] in readme
    money = [harbor["q1"]["unbilled_time_cents"], harbor["q1"]["unbilled_standard_time_cents"]]
    money.extend(value for key, value in harbor["q4"].items() if key.endswith("_cents"))
    for row in harbor["q2"]["over_budget_matters"]:
        assert row["matter_name"] in readme
        money.extend(row[key] for key in ("fee_actual_cents", "fee_budget_cents", "fee_variance_cents"))
    august = harbor["q3"]["last_month"]["currencies"]["USD"]
    money.extend((august["issued_net_time_cents"], august["standard_time_denominator_cents"],
                  august["by_matter"]["harbor_m008"]["stage_reductions_cents"]["proforma"],
                  august["by_matter"]["harbor_m009"]["stage_reductions_cents"]["invoice"]))
    for cents in money:
        assert f"USD {Decimal(cents) / 100:,.2f}" in readme
    for period in ("last_month", "preceding_month"):
        ratio = harbor["q3"][period]["currencies"]["USD"]["issued_prebill_realization"]
        assert f"{Decimal(ratio) * 100:.4f}%" in readme
    change = abs(Decimal(harbor["q3"]["issued_realization_change"]["USD"])) * 100
    assert f"{change:.4f} percentage points" in readme


@pytest.mark.parametrize("name", ("architecture", "leakage"))
def test_rendered_diagrams_are_accessible_source_companions(name: str) -> None:
    source = (ROOT / "docs/img" / f"{name}.mmd").read_text(encoding="utf-8")
    root = ET.parse(ROOT / "docs/img" / f"{name}.svg").getroot()
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.get("viewBox")
    for marker, tag in (("accTitle", "title"), ("accDescr", "desc")):
        expected = re.search(rf"^\s*{marker}: (.+)$", source, flags=re.M)
        element = root.find(f"svg:{tag}", ns)
        assert expected and element is not None
        assert element.text == expected.group(1)
    assert root.find(".//svg:script", ns) is None


def test_reuse_yaml_sketch_has_entity_shape() -> None:
    text = (ROOT / "docs/05-reuse-for-your-own-domain.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, flags=re.S)
    assert len(blocks) == 1
    entity = yaml.safe_load(blocks[0])
    assert entity["name"] == "ServiceContract"
    assert entity["key"] in entity["properties"]
    assert entity["display_name"] in entity["properties"]
    assert entity["properties"]["labor_budget_cents"]["type"] == "long"


def test_source_verify_markers_have_guide_entries() -> None:
    guide = (ROOT / "docs/03-deployment-guide.md").read_text(encoding="utf-8")
    assert "## Verify before running" in guide
    marker = "# " + "VERIFY:"
    for directory in ("fabric", "agent", "scripts", "data-generator"):
        for path in (ROOT / directory).rglob("*"):
            if path.suffix not in {".py", ".yaml", ".ps1", ".sh", ".ipynb"}:
                continue
            if marker in path.read_text(encoding="utf-8"):
                assert path.relative_to(ROOT).as_posix() in guide
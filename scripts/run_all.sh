#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
args=(--dry-run)
while (($#)); do
  case "$1" in
    --dry-run) shift ;;
    --single-tenant-simulation|--allow-shared-consumer-tenant) args+=("$1"); shift ;;
    --config)
      [[ $# -ge 2 ]] || { printf '%s\n' '--config requires a path' >&2; exit 2; }
      args+=("$1" "$2"); shift 2 ;;
    *) printf 'Unsupported option: %s (this wrapper never deploys live)\n' "$1" >&2; exit 2 ;;
  esac
done
[[ -x .venv/bin/python ]] || { printf '%s\n' 'Run bash scripts/setup.sh first.' >&2; exit 2; }
.venv/bin/python data-generator/generate.py --output data
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python agent/evaluate.py --offline-self-test --repetitions 3
.venv/bin/python fabric/deploy.py "${args[@]}"
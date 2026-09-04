"""Offline contracts only: no Spark session, Fabric execution, or SQL emulation.

Symbolic columns exercise the notebook's actual projection-construction statements
against checked-in Parquet schemas. They do not evaluate SQL or prove cast results.
"""

from __future__ import annotations

import ast
import base64
import copy
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import nbformat
import pyarrow.parquet as pq
import pytest

from fabric.ontology_adapter import load_contract, required_gold_tables
from fabric.steps.step04_notebooks import notebook_definition

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = tuple(sorted((ROOT / "fabric/notebooks").glob("*.ipynb")))
SCHEMAS = json.loads((ROOT / "data/schema.json").read_text(encoding="utf-8"))["tables"]
CONTRACT = load_contract(ROOT / "fabric/ontology/ontology.yaml")
SQL_WORDS = {"cast", "as", "decimal", "case", "when", "then", "else", "end",
             "concat", "year", "quarter", "date_format", "is", "not", "null", "and", "or"}


def source(cell: dict) -> str:
    value = cell["source"]
    return value if isinstance(value, str) else "".join(value)


def document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def transform_tree(path: Path) -> ast.Module:
    return ast.parse(source(document(path)["cells"][2]))


def assigned(tree: ast.AST, name: str) -> ast.Assign:
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == name
                       for target in node.targets)]
    assert len(matches) == 1, name
    return matches[0]


def references(expression: str) -> set[str]:
    """Recognize only this contract's SQL vocabulary, not arbitrary Spark SQL."""
    text = re.sub(r"'(?:''|[^'])*'", "", expression)
    return {word for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", text)
            if word.lower() not in SQL_WORDS}


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.stem)
def test_templates_are_valid_three_cell_unexecuted_notebooks(path: Path) -> None:
    assert len(NOTEBOOKS) == 2
    notebook = document(path)
    assert notebook["nbformat"] == 4
    nbformat.validate(copy.deepcopy(notebook))
    assert [cell["cell_type"] for cell in notebook["cells"]] == ["markdown", "code", "code"]
    ids = [cell["id"] for cell in notebook["cells"]]
    assert len(set(ids)) == 3
    assert "No Fabric execution has been performed" in source(notebook["cells"][0])
    for index, cell in enumerate(notebook["cells"][1:], 2):
        assert cell["execution_count"] is None
        assert cell["outputs"] == []
        compile(source(cell), f"{path.name}:cell{index}", "exec", ast.PyCF_ONLY_AST)
    parameter = assigned(ast.parse(source(notebook["cells"][1])), "CONFIG_JSON")
    assert json.loads(ast.literal_eval(parameter.value)) == {}


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.stem)
@pytest.mark.parametrize("firm", ["harbor", "kestrel"])
def test_deployment_payload_parameters_round_trip_without_execution(path: Path, firm: str) -> None:
    original = path.read_bytes()
    config = {
        "source_workspace_id": "11111111-1111-4111-8111-111111111111",
        "target_workspace_id": "11111111-1111-4111-8111-111111111111",
        "source_lakehouse_id": "22222222-2222-4222-8222-222222222222",
        "target_lakehouse_id": "33333333-3333-4333-8333-333333333333",
        "firm_slug": firm, "tables": sorted(SCHEMAS), "schemas": SCHEMAS,
        "ontology": CONTRACT, "as_of": "2026-09-04",
        "escaping_probe": "quotes: '\"; slash: \\; newline:\n雪\nraise RuntimeError('not code')",
    }
    before = copy.deepcopy(config)
    definition = notebook_definition(path, config)
    assert definition["format"] == "ipynb"
    assert len(definition["parts"]) == 1
    part = definition["parts"][0]
    assert part["path"] == "notebook-content.ipynb"
    assert part["payloadType"] == "InlineBase64"
    rendered = json.loads(base64.b64decode(part["payload"], validate=True))
    nbformat.validate(copy.deepcopy(rendered))
    assert len(rendered["cells"]) == 3
    code = [cell for cell in rendered["cells"] if cell["cell_type"] == "code"]
    assert [cell for cell in rendered["cells"]
            if "parameters" in cell["metadata"].get("tags", [])] == [code[0]]
    tree = ast.parse(source(code[0]))
    assert len(tree.body) == 1
    assert json.loads(ast.literal_eval(assigned(tree, "CONFIG_JSON").value)) == config
    assert "lakehouse" not in rendered["metadata"].get("dependencies", {})
    for cell in code:
        ast.parse(source(cell))
        assert cell["outputs"] == [] and cell["execution_count"] is None
    assert source(code[1]) == source(document(path)["cells"][2])
    assert config == before
    assert path.read_bytes() == original


@dataclass(frozen=True)
class SymbolicColumn:
    expression: str
    name: str | None = None
    spark_type: str | None = None

    def cast(self, spark_type: str) -> SymbolicColumn:
        return replace(self, spark_type=spark_type)

    def alias(self, name: str) -> SymbolicColumn:
        return replace(self, name=name)


class ProjectionRecorder:
    """Record construction only; deliberately no Spark/DataFrame/SQL execution."""

    def __init__(self, columns: list[str]):
        self.columns = set(columns)

    def col(self, name: str) -> SymbolicColumn:
        assert name in self.columns, f"Missing source column: {name}"
        return SymbolicColumn(f"`{name}`", name)

    def expr(self, expression: str) -> SymbolicColumn:
        missing = references(expression) - self.columns
        assert not missing, f"Missing source columns: {missing}"
        return SymbolicColumn(expression)


def projection_statements() -> ast.Module:
    tree = transform_tree(ROOT / "fabric/notebooks/02_silver_to_gold.ipynb")
    extension = [node for node in ast.walk(tree) if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Call)
                 and ast.unparse(node.value.func) == "selected.extend"]
    assert len(extension) == 1
    return ast.Module(body=[assigned(tree, "replaced"), assigned(tree, "selected"), *extension],
                      type_ignores=[])


def project(columns: list[str], properties: dict) -> list[SymbolicColumn]:
    scope = {"F": ProjectionRecorder(columns), "raw": SimpleNamespace(columns=columns),
             "properties": properties}
    # Execute only the three construction statements; no imports, Spark, reads or writes.
    exec(compile(projection_statements(), "<symbolic projection only>", "exec"), scope)
    return scope["selected"]


@pytest.mark.parametrize("firm", ["harbor", "kestrel"])
def test_every_gold_property_constructs_against_actual_parquet_columns(firm: str) -> None:
    entities = {entity["table"]: entity for entity in CONTRACT["entities"]}
    projected = {}
    for table, spec in SCHEMAS.items():
        columns = pq.read_schema(ROOT / "data" / firm / f"{table}.parquet").names
        assert set(columns) == {field["name"] for field in spec["spark_schema"]["fields"]}
        properties = entities.get(table, {}).get("properties", {})
        selected = project(columns, properties)
        names = [column.name for column in selected]
        assert len(names) == len(set(names)), (table, names)
        assert set(columns) <= set(names), table
        by_name = {column.name: column for column in selected}
        for prop in properties.values():
            assert by_name[prop["column"]] == SymbolicColumn(
                prop.get("expression", f"`{prop['column']}`"), prop["column"], prop["type"])
        if table in entities:
            entity = entities[table]
            assert entity["properties"][entity["key"]]["column"] in by_name
        projected[table] = set(names)
    for relation in CONTRACT["relationships"]:
        columns = {"firm_id", relation["source_column"], relation["target_column"]}
        assert columns <= projected[relation["table"]]
        if relation.get("filter"):
            assert references(relation["filter"]) <= projected[relation["table"]]
            projected[relation["edge_table"]] = columns
    assert set(required_gold_tables(CONTRACT)) <= set(projected)


def test_projection_replaces_colliding_alias_using_original_source() -> None:
    properties = {"amount": {"column": "amount", "expression": "amount_cents / 100",
                             "type": "decimal(18,2)"}}
    selected = project(["firm_id", "amount", "amount_cents"], properties)
    assert [column.name for column in selected] == ["firm_id", "amount_cents", "amount"]
    assert selected[-1].expression == "amount_cents / 100"
    with pytest.raises(AssertionError, match="Missing source columns"):
        project(["firm_id", "amount"], properties)


def test_firm_fk_is_skipped_before_parent_projection() -> None:
    tree = transform_tree(ROOT / "fabric/notebooks/01_bronze_to_silver.ipynb")
    parent = assigned(tree, "parent")
    fk_loop = next(node for node in ast.walk(tree) if isinstance(node, ast.For)
                   and isinstance(node.target, ast.Name) and node.target.id == "fk")
    skip = next(node for node in fk_loop.body if isinstance(node, ast.If)
                and ast.unparse(node.test) == "column == 'firm_id'")
    assert len(skip.body) == 1 and isinstance(skip.body[0], ast.Continue)
    assert skip.lineno < parent.lineno


def test_required_gold_values_checked_after_cast_before_publish() -> None:
    tree = transform_tree(ROOT / "fabric/notebooks/02_silver_to_gold.ipynb")
    table_loop = next(node for node in tree.body if isinstance(node, ast.For)
                      and ast.unparse(node.iter) == "config['tables']")
    required = assigned(table_loop, "required_columns")
    assert "if not field['nullable']" in ast.unparse(required.value)
    check = next(node for node in ast.walk(tree) if isinstance(node, ast.For)
                 and ast.unparse(node.iter) == "sorted(required_columns)")
    assert "frame.where(F.col(column).isNull()).limit(1).count()" in ast.unparse(check)
    assert any(isinstance(node, ast.Raise) for node in ast.walk(check))
    assert assigned(table_loop, "frame").lineno < required.lineno < check.lineno
    assert "required_columns.add(properties[entity['key']]['column'])" in ast.unparse(tree)
    for spec in SCHEMAS.values():
        scope = {"spec": spec}
        exec(compile(ast.Module(body=[required], type_ignores=[]), "<required columns>", "exec"), scope)
        assert scope["required_columns"] == {
            field["name"] for field in spec["spark_schema"]["fields"] if not field["nullable"]}
    cash = assigned(tree, "cash")
    assert ast.unparse(cash.value) == "F.sum(F.col('amount_cents').cast('decimal(38,0)')).alias('cash_cents')"
    assert ast.unparse(assigned(tree, "source_cash").value) == "raw.groupBy('firm_id', 'currency').agg(cash)"
    assert ast.unparse(assigned(tree, "gold_cash").value) == "frame.groupBy('firm_id', 'currency').agg(cash)"
    reconciliation = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                          and "source_cash.exceptAll" in ast.unparse(node.test))
    assert "gold_cash.exceptAll(source_cash)" in ast.unparse(reconciliation.test)
    assert any(isinstance(node, ast.Raise) for node in reconciliation.body)
    writes = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute) and node.func.attr == "save"]
    assert writes and all(check.lineno < reconciliation.lineno < node.lineno for node in writes)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda path: path.stem)
def test_runtime_guards_and_no_authorization_operations(path: Path) -> None:
    tree = transform_tree(path)
    text = ast.unparse(tree)
    assert "spark.conf.set('spark.sql.ansi.enabled', 'true')" in text
    assert "config['source_workspace_id'] != config['target_workspace_id']" in text
    assert "if source == target:" in text
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports <= {"datetime", "time", "uuid", "pyspark.sql", "pyspark.sql.types"}
    assert {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names} == {"json", "re"}
    assert not any(isinstance(node, ast.Call) and ast.unparse(node.func) in
                   {"spark.sql", "exec", "eval", "__import__"} for node in ast.walk(tree))
    if path.stem.startswith("02"):
        assert "'authorization_installed': False" in text
        assert "administrator-only" in source(document(path)["cells"][0])
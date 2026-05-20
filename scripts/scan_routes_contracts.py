#!/usr/bin/env python3
"""Scan source-level contracts that constrain api/routes.py refactors."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_PY = REPO_ROOT / "api" / "routes.py"
HANDLERS_DIR = REPO_ROOT / "api" / "routes_handlers"
TESTS_DIR = REPO_ROOT / "tests"
REPORT_PATH = REPO_ROOT / "api" / "routes-handlers-contract.md"
HANDLER_RE = re.compile(r"\bdef\s+(_handle_[A-Za-z0-9_]+)\s*\(")
FUNCTION_REF_RE = re.compile(r"\b(_handle_[A-Za-z0-9_]+|_run_cron_tracked|_cron_job_subprocess_main)\b")
ROUTE_SOURCE_MARKERS = (
    "api/routes.py",
    '"api" / "routes.py"',
    "'api' / 'routes.py'",
    '"api" / "routes.py"',
    "joinpath(\"api/routes.py\")",
    "joinpath('api/routes.py')",
    "open(\"api/routes.py\"",
    "open('api/routes.py'",
    "ROUTES_PY",
    "ROUTES_SRC",
)


@dataclass
class FunctionSource:
    name: str
    path: Path
    source: str
    lineno: int
    end_lineno: int


@dataclass
class Contract:
    kind: str
    value: str
    file: str
    line: int


@dataclass
class Rating:
    function: str
    location: str
    status: str
    reasons: list[str] = field(default_factory=list)


class TestContractVisitor(ast.NodeVisitor):
    def __init__(self, rel_path: str, source: str) -> None:
        self.rel_path = rel_path
        self.source = source
        self.route_source_test = any(marker in source for marker in ROUTE_SOURCE_MARKERS)
        self.routes_path_names: set[str] = set()
        self.routes_file_names: set[str] = set()
        self.routes_source_names: set[str] = set()
        self.scope_depth = 0
        self.contracts: list[Contract] = []
        self.inspect_getsource_funcs: set[str] = set()
        self.ast_extract_funcs: set[str] = set()
        self.def_literal_funcs: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped_body(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped_body(node)

    def _visit_scoped_body(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        saved_files = set(self.routes_file_names)
        saved_sources = set(self.routes_source_names)
        saved_paths = set(self.routes_path_names)
        self.scope_depth += 1
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self.scope_depth -= 1
            self.routes_file_names = saved_files
            self.routes_source_names = saved_sources
            self.routes_path_names = saved_paths

    def visit_Assign(self, node: ast.Assign) -> None:
        source_kind = self._route_assignment_kind(node.value)
        if source_kind:
            for target in node.targets:
                for name in _target_names(target):
                    if source_kind == "path":
                        self.routes_path_names.add(name)
                    elif source_kind == "source":
                        self.routes_source_names.add(name)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        added_files: set[str] = set()
        for item in node.items:
            if isinstance(item.context_expr, ast.Call) and _call_name(item.context_expr.func) == "open":
                if item.context_expr.args and _node_mentions_routes_path(item.context_expr.args[0], self.routes_path_names):
                    if isinstance(item.optional_vars, ast.Name):
                        self.routes_file_names.add(item.optional_vars.id)
                        added_files.add(item.optional_vars.id)
        try:
            for stmt in node.body:
                self.visit(stmt)
        finally:
            self.routes_file_names.difference_update(added_files)

    def add(self, kind: str, value: str, node: ast.AST) -> None:
        self.contracts.append(
            Contract(kind, value, self.rel_path, getattr(node, "lineno", 1))
        )

    def visit_Call(self, node: ast.Call) -> None:
        call_name = _call_name(node.func)
        if call_name == "inspect.getsource" and node.args:
            func_name = _attribute_or_name(node.args[0])
            if func_name:
                self.inspect_getsource_funcs.add(func_name)
                self.add("inspect_getsource", func_name, node)
        elif call_name in {"ast.parse", "parse"} and node.args and self._node_references_routes_source(node.args[0]):
            self.add("ast_parse_routes_source", "ast.parse(routes.py)", node)

        for arg in node.args:
            text = _literal_text(arg)
            if not text:
                continue
            if self._call_references_routes_source(node) and ("def _handle_" in text or "\ndef _handle_" in text):
                for func_name in HANDLER_RE.findall(text):
                    self.def_literal_funcs.add(func_name)
                    self.add("def_literal", func_name, node)
            if call_name and call_name.endswith(("index", "find")) and self._call_references_routes_source(node):
                for func_name in FUNCTION_REF_RE.findall(text):
                    self.add("source_position_reference", func_name, node)
                    if "def " in text:
                        self.def_literal_funcs.add(func_name)
            if call_name in {"_get_function_source", "_extract_handler"}:
                for func_name in FUNCTION_REF_RE.findall(text):
                    self.ast_extract_funcs.add(func_name)
                    self.add("function_source_extract", func_name, node)
            if call_name in {"re.search", "re.match"} and self._regex_call_targets_routes_source(node):
                self._add_code_literal(text, node, kind="regex_literal")

        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        for text in self._routes_source_in_literals(node.test):
            self._add_code_literal(text, node, kind="assert_literal")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        self.generic_visit(node)

    def _add_code_literal(self, text: str, node: ast.AST, *, kind: str) -> None:
        if not _is_routes_code_literal(text):
            return
        self.add(kind, text, node)
        for func_name in FUNCTION_REF_RE.findall(text):
            if "def " in text:
                self.def_literal_funcs.add(func_name)

    def _route_assignment_kind(self, value: ast.AST) -> str:
        if _node_mentions_routes_path(value, self.routes_path_names):
            if _node_reads_text(value, self.routes_path_names, self.routes_file_names):
                return "source"
            return "path"
        if _node_reads_text(value, self.routes_path_names, self.routes_file_names):
            return "source"
        return ""

    def _node_references_routes_source(self, node: ast.AST) -> bool:
        names = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
        return bool(names & self.routes_source_names)

    def _call_references_routes_source(self, node: ast.Call) -> bool:
        return self._node_references_routes_source(node.func) or any(
            self._node_references_routes_source(arg) for arg in node.args
        )

    def _regex_call_targets_routes_source(self, node: ast.Call) -> bool:
        if len(node.args) >= 2 and self._node_references_routes_source(node.args[1]):
            return True
        return any(
            kw.arg in {"string", "src", "source"} and self._node_references_routes_source(kw.value)
            for kw in node.keywords
        )

    def _routes_source_in_literals(self, node: ast.AST) -> list[str]:
        if isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.Or):
                return []
            values: list[str] = []
            for value in node.values:
                values.extend(self._routes_source_in_literals(value))
            return values
        if isinstance(node, ast.Compare):
            values = []
            left_text = _literal_text(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                if isinstance(op, ast.In) and left_text and self._node_references_routes_source(comparator):
                    values.append(left_text)
            return values
        return []


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _attribute_or_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _target_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in node.elts:
            names.extend(_target_names(elt))
        return names
    return []


def _node_mentions_routes_path(node: ast.AST, routes_path_names: set[str]) -> bool:
    text = ast.unparse(node) if hasattr(ast, "unparse") else ""
    if "api/routes.py" in text or ("routes.py" in text and "api" in text):
        return True
    literal_parts = [
        child.value.replace("\\", "/")
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    ]
    if "routes.py" in literal_parts and "api" in literal_parts:
        return True
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            normalized = child.value.replace("\\", "/")
            if normalized == "api/routes.py" or normalized.endswith("/api/routes.py"):
                return True
            if normalized == "routes.py" and "api" in text:
                return True
        elif isinstance(child, ast.Name) and child.id in routes_path_names:
            return True
    return False


def _node_reads_text(
    node: ast.AST,
    routes_path_names: set[str],
    routes_file_names: set[str],
) -> bool:
    if isinstance(node, ast.Call):
        call_name = _call_name(node.func)
        if call_name == "read_text" or call_name.endswith(".read_text"):
            return _node_mentions_routes_path(node.func, routes_path_names)
        if (call_name == "read" or call_name.endswith(".read")) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id in routes_file_names:
                return True
        if node.args and _node_mentions_routes_path(node, routes_path_names):
            return True
    return False


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
        return "".join(parts) if parts else None
    return None


def _literal_strings(node: ast.AST) -> list[str]:
    values: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            values.append(child.value)
        elif isinstance(child, ast.JoinedStr):
            text = _literal_text(child)
            if text:
                values.append(text)
    return values


def _is_routes_code_literal(text: str) -> bool:
    if len(text) < 6:
        return False
    if re.match(r"""^["'](?:GET|POST|PATCH|DELETE|PUT|HEAD|OPTIONS)\s+/.+["']$""", text):
        return False
    if re.match(r"^(GET|POST|PATCH|DELETE|PUT|HEAD|OPTIONS)\s+/", text):
        return False
    code_markers = (
        "def ",
        "parsed.path",
        "/api/",
        "/session/",
        "_handle_",
        "_approval_",
        "_clear_stale_stream_state",
        "queue.",
        "SESSION_INDEX_FILE",
        "active_stream_id",
        "pending_user_message",
        "pending_attachments",
        "pending_started_at",
        "platform='webui'",
        'platform="webui"',
        "provider_model_ids",
        "resolve_runtime_provider_with_anthropic_env_lock",
        "_CLIENT_DISCONNECT_ERRORS",
        "safe_resolve(",
        "get_session(",
        "import_cli_session(",
    )
    return any(marker in text for marker in code_markers)


def _parse_python(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(f"warning: failed to parse {path}: {exc}", file=sys.stderr)
        return None


def _top_level_functions(path: Path) -> dict[str, FunctionSource]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    out: dict[str, FunctionSource] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_handle_"):
            continue
        end_lineno = getattr(node, "end_lineno", node.lineno)
        func_source = "\n".join(lines[node.lineno - 1 : end_lineno])
        out[node.name] = FunctionSource(
            node.name,
            path,
            func_source,
            node.lineno,
            end_lineno,
        )
    return out


def _collect_route_functions() -> tuple[dict[str, FunctionSource], dict[str, FunctionSource]]:
    routes_functions = _top_level_functions(ROUTES_PY)
    handler_functions: dict[str, FunctionSource] = {}
    if HANDLERS_DIR.exists():
        for path in sorted(HANDLERS_DIR.glob("*.py")):
            if path.name == "__init__.py":
                continue
            handler_functions.update(_top_level_functions(path))
    return routes_functions, handler_functions


def _collect_contracts() -> list[Contract]:
    contracts: list[Contract] = []
    if not TESTS_DIR.exists():
        return contracts
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        rel_path = str(path.relative_to(REPO_ROOT))
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = _parse_python(path)
        if tree is None:
            continue
        visitor = TestContractVisitor(rel_path, source)
        visitor.visit(tree)
        for func_name in sorted(visitor.inspect_getsource_funcs):
            contracts.append(Contract("inspect_getsource_function", func_name, rel_path, 1))
        for func_name in sorted(visitor.ast_extract_funcs):
            contracts.append(Contract("ast_extracted_function", func_name, rel_path, 1))
        for func_name in sorted(visitor.def_literal_funcs):
            contracts.append(Contract("def_literal_function", func_name, rel_path, 1))
        contracts.extend(visitor.contracts)
    return _dedupe_contracts(contracts)


def _dedupe_contracts(contracts: list[Contract]) -> list[Contract]:
    seen: set[tuple[str, str, str, int]] = set()
    out: list[Contract] = []
    for item in contracts:
        key = (item.kind, item.value, item.file, item.line)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _classify(
    routes_functions: dict[str, FunctionSource],
    handler_functions: dict[str, FunctionSource],
    contracts: list[Contract],
) -> list[Rating]:
    function_names = sorted(set(routes_functions) | set(handler_functions))
    def_locked = {
        item.value
        for item in contracts
        if item.kind in {
            "def_literal",
            "def_literal_function",
            "ast_extracted_function",
            "function_source_extract",
        }
        and item.value.startswith("_handle_")
    }
    getsource_locked = {
        item.value
        for item in contracts
        if item.kind in {"inspect_getsource", "inspect_getsource_function"}
        and item.value.startswith("_handle_")
    }
    literals = [
        item
        for item in contracts
        if item.kind in {"assert_literal", "regex_literal"}
        and len(item.value) >= 6
    ]

    ratings: list[Rating] = []
    for name in function_names:
        func = routes_functions.get(name) or handler_functions[name]
        moved = name not in routes_functions
        reasons: list[str] = []
        status = "green"
        if name in def_locked:
            status = "red"
            reasons.append("routes.py source tests lock the physical function definition")
        elif name in getsource_locked:
            status = "yellow"
            reasons.append("inspect.getsource(routes.%s) is used by tests" % name)

        matching_literals = [
            item
            for item in literals
            if item.value in func.source and len(item.value) <= 240
        ]
        if matching_literals and status == "green":
            status = "yellow"
        for item in matching_literals[:6]:
            reasons.append(
                f"body contains source literal from {item.file}:{item.line}: {item.value!r}"
            )
        if len(matching_literals) > 6:
            reasons.append(f"body contains {len(matching_literals) - 6} more source literals")

        if moved and status == "red":
            reasons.append("currently moved into routes_handlers; this will break routes.py source tests")
        location = str(func.path.relative_to(REPO_ROOT))
        ratings.append(Rating(name, location, status, reasons))
    return ratings


def _moved_handlers() -> dict[str, FunctionSource]:
    moved: dict[str, FunctionSource] = {}
    if not HANDLERS_DIR.exists():
        return moved
    for path in sorted(HANDLERS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        moved.update(_top_level_functions(path))
    return moved


def _check_contracts(ratings: list[Rating], contracts: list[Contract]) -> list[str]:
    routes_src = ROUTES_PY.read_text(encoding="utf-8")
    errors: list[str] = []
    ratings_by_name = {rating.function: rating for rating in ratings}
    for rating in ratings:
        if rating.status == "red" and rating.location != "api/routes.py":
            errors.append(f"{rating.function}: red-locked function is not physically defined in api/routes.py")

    for item in contracts:
        if item.kind == "regex_literal" and _is_routes_code_literal(item.value):
            try:
                matched = re.search(item.value, routes_src, re.DOTALL) is not None
            except re.error as exc:
                errors.append(
                    f"{item.file}:{item.line}: invalid captured regex {item.value!r}: {exc}"
                )
                continue
            if not matched:
                errors.append(
                    f"{item.file}:{item.line}: routes.py does not match locked regex {item.value!r}"
                )
        elif item.kind == "assert_literal" and _is_routes_code_literal(item.value):
            if item.value not in routes_src:
                errors.append(
                    f"{item.file}:{item.line}: routes.py is missing locked literal {item.value!r}"
                )
        if item.kind in {"def_literal", "def_literal_function"} and item.value.startswith("_handle_"):
            if f"def {item.value}(" not in routes_src:
                errors.append(
                    f"{item.file}:{item.line}: routes.py is missing locked definition def {item.value}("
                )

    for name in sorted(_moved_handlers()):
        if f"def {name}(" in routes_src:
            continue
        if f" {name}" not in routes_src and f"\n    {name}," not in routes_src:
            errors.append(f"{name}: moved handler is not re-exported from api.routes")
        full_name_pattern = re.compile(rf"routes_handlers\.[A-Za-z0-9_]+\.{re.escape(name)}\(")
        if full_name_pattern.search(routes_src):
            errors.append(f"{name}: dispatcher must call the short api.routes binding, not routes_handlers.*.{name}")
        if ratings_by_name.get(name) and ratings_by_name[name].status == "red":
            errors.append(f"{name}: moved handler is rated red")
    return sorted(set(errors))


def _write_report(
    ratings: list[Rating],
    contracts: list[Contract],
    errors: list[str] | None = None,
) -> None:
    counts = {status: 0 for status in ("green", "yellow", "red")}
    for rating in ratings:
        counts[rating.status] = counts.get(rating.status, 0) + 1

    machine = {
        "counts": counts,
        "ratings": [
            {
                "function": rating.function,
                "location": rating.location,
                "status": rating.status,
                "reasons": rating.reasons,
            }
            for rating in ratings
        ],
        "contracts": [
            {
                "kind": item.kind,
                "value": item.value,
                "file": item.file,
                "line": item.line,
            }
            for item in contracts
        ],
        "errors": errors or [],
    }

    lines: list[str] = [
        "# routes handlers contract scan",
        "",
        "> Generated by `python scripts/scan_routes_contracts.py`.",
        "",
        "## Machine readable",
        "",
        "```json",
        json.dumps(machine, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Summary",
        "",
        f"- green: {counts.get('green', 0)}",
        f"- yellow: {counts.get('yellow', 0)}",
        f"- red: {counts.get('red', 0)}",
        "",
        "## Ratings",
        "",
        "| status | function | location | reason |",
        "|---|---|---|---|",
    ]
    for rating in ratings:
        reason = "; ".join(rating.reasons) if rating.reasons else "no source-level contract detected"
        lines.append(
            f"| {rating.status} | `{rating.function}` | `{rating.location}` | {reason.replace('|', '\\|')} |"
        )
    if errors:
        lines.extend(["", "## Check errors", ""])
        for error in errors:
            lines.append(f"- {error}")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when current routes.py violates locked contracts")
    parser.add_argument("--no-report", action="store_true", help="do not write api/routes-handlers-contract.md")
    args = parser.parse_args(argv)

    routes_functions, handler_functions = _collect_route_functions()
    contracts = _collect_contracts()
    ratings = _classify(routes_functions, handler_functions, contracts)
    errors = _check_contracts(ratings, contracts) if args.check else []
    if not args.no_report:
        _write_report(ratings, contracts, errors)

    counts = {status: 0 for status in ("green", "yellow", "red")}
    for rating in ratings:
        counts[rating.status] = counts.get(rating.status, 0) + 1
    print(
        "routes contract scan: "
        f"green={counts.get('green', 0)} "
        f"yellow={counts.get('yellow', 0)} "
        f"red={counts.get('red', 0)}"
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

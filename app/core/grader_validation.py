from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterable
from inspect import signature
from pathlib import Path


class GraderValidationError(ValueError):
    pass


def load_allowed_imports(path: Path) -> set[str]:
    data = tomllib.loads(path.read_text())
    allowed = data.get("allowed_modules", [])
    if not isinstance(allowed, list):
        raise GraderValidationError("allowed_modules must be a list")
    return {str(module) for module in allowed}


def _extract_top_level_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def expected_grader_function_name(problem_key: str) -> str:
    sanitized = re.sub(r"[^0-9a-zA-Z_]", "_", problem_key)
    return f"grade_{sanitized}"


def validate_grader_source(source_code: str, problem_key: str, allowed_modules: Iterable[str]) -> str:
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        raise GraderValidationError(f"Syntax error in grader source: {exc}") from exc

    imported = _extract_top_level_modules(tree)
    allowed = set(allowed_modules)
    disallowed = sorted(module for module in imported if module not in allowed)
    if disallowed:
        joined = ", ".join(disallowed)
        raise GraderValidationError(f"Disallowed imports: {joined}")

    expected_name = expected_grader_function_name(problem_key)
    fn_defs = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    fn = next((node for node in fn_defs if node.name == expected_name), None)
    if fn is None:
        raise GraderValidationError(
            f"Expected a function named `{expected_name}(submission, callback)`"
        )

    arg_names = [arg.arg for arg in fn.args.args]
    if arg_names[:2] != ["submission", "callback"]:
        raise GraderValidationError(
            f"Function `{expected_name}` must accept first args `(submission, callback)`"
        )

    return expected_name


def load_grader_callable(source_code: str, fn_name: str):
    namespace: dict[str, object] = {}
    exec(compile(source_code, "grader_source", "exec"), namespace, namespace)
    fn = namespace.get(fn_name)
    if fn is None or not callable(fn):
        raise GraderValidationError(f"Function `{fn_name}` was not found after execution")

    sig = signature(fn)
    params = list(sig.parameters)
    if len(params) < 2:
        raise GraderValidationError(
            f"Function `{fn_name}` must accept `(submission, callback)`"
        )
    return fn

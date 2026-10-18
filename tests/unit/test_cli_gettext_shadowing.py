"""A function may not both assign to ``_`` and call the gettext ``_()``.

``cnequity`` modules alias gettext as ``_``. A throwaway ``_`` in tuple
unpacking marks the name local for the whole function, so a later ``_()``
call raises ``UnboundLocalError`` at runtime — a path no type checker or
catalog check executes. Found live when a scope-validation error crashed
instead of printing its message (``backfill_cmds._override_scope``,
``quality_cmds._report_outstanding_keys``).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cnequity import i18n

PACKAGE = Path(i18n.__file__).resolve().parent


def _gettext_shadowing(fn) -> bool:
    assigns = loads = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == "_":
            if isinstance(node.ctx, ast.Store):
                assigns = True
            else:
                loads = True
    return assigns and loads


@pytest.mark.parametrize(
    "path",
    sorted(PACKAGE.rglob("*.py"), key=lambda p: p.as_posix()),
    ids=lambda p: p.name,
)
def test_no_function_assigns_and_calls_gettext_underscore(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        f"{node.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _gettext_shadowing(node)
    ]
    assert offenders == [], "rename the throwaway `_` — it shadows the gettext alias"

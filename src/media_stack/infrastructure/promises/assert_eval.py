"""Sandboxed evaluator for promise ``assert:`` expressions.

Single auditable site for the dynamically-evaluated YAML expressions.
Both the orchestrator's dispatcher and the legacy ``probe_promises``
CLI go through this module so any future security review only has
one place to inspect.

Design notes:

* YAML ``|`` block scalars in promises.yaml produce multi-line
  strings — Python's expression evaluator only accepts a single
  expression, so we collapse newlines to spaces.

* Generator / comprehension expressions (``all(... for x in ...)``)
  use their OWN scope and can't see names passed via the locals
  dict — only globals. So the response/data name has to live in the
  globals dict, not locals. Without this, every probe using
  ``all()`` / ``any()`` over the response fails with
  ``name 'data' is not defined``.

* The builtins dict is a tight allowlist — operators write probe
  asserts, so they trust this code with the YAML registry, but the
  scope is still narrowed defensively.
"""

from __future__ import annotations

from typing import Any, Mapping


_SAFE_BUILTINS = {
    "isinstance": isinstance, "any": any, "all": all, "len": len,
    "set": set, "dict": dict, "list": list, "tuple": tuple,
    "bool": bool, "str": str, "int": int, "float": float,
    "sorted": sorted, "min": min, "max": max,
}


def evaluate(expr: str, scope: Mapping[str, Any]) -> tuple[bool, str]:
    """Evaluate the assert expression. Returns ``(ok, detail)``.

    ``ok=True`` means the expression returned truthy. ``ok=False``
    with detail starting ``assert eval error:`` means the expression
    raised; otherwise it returned a falsy value.
    """
    import builtins as _b
    expr = (expr or "").strip().replace("\n", " ")
    if not expr:
        return (False, "empty assert expression")
    globals_dict = {"__builtins__": _SAFE_BUILTINS, **dict(scope)}
    try:
        ok = bool(_b.eval(expr, globals_dict))  # noqa: S307
    except Exception as exc:  # noqa: BLE001 - surface as detail
        return (False, f"assert eval error: {exc}")
    if not ok:
        return (False, "assert returned False")
    return (True, "ok")


__all__ = ["evaluate"]

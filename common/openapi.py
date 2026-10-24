"""Name the two money types in the OpenAPI document (ADR 0008).

Every amount field is described inline as a string with a pattern and a marker (``x-money-type``). This
post-processing hook replaces each marked schema by a reference to a shared component, ``Amount`` or
``SignedAmount``, so a generated client has one type for each instead of an anonymous string per field. A
component is added only when something uses it.
"""

from typing import Any

from .money import AMOUNT_PATTERN, SIGNED_AMOUNT_PATTERN

MARKER = "x-money-type"
_KEPT_ON_THE_REFERENCE = {"type", "format", "pattern", MARKER}

COMPONENTS: dict[str, dict[str, Any]] = {
    "Amount": {
        "type": "string",
        "format": "decimal",
        "pattern": AMOUNT_PATTERN,
        "description": (
            "A money amount, not negative: a decimal string with exactly the number of digits of its currency "
            "(`12.50` for USD, `12.500` for KWD, `1200` for JPY), a point as the separator, no grouping, no sign, "
            "at most 15 digits in all. The `currency` field next to it says which currency."
        ),
    },
    "SignedAmount": {
        "type": "string",
        "format": "decimal",
        "pattern": SIGNED_AMOUNT_PATTERN,
        "description": (
            "A computed money amount that can be negative (a balance, a day total): the format of `Amount` with a "
            "leading `-` when it is below zero. Zero is never written as `-0`."
        ),
    },
}


def _replace(node: Any, used: set[str]) -> None:
    if isinstance(node, dict):
        name = node.get(MARKER)
        if name in COMPONENTS:
            used.add(name)
            extras = {k: v for k, v in node.items() if k not in _KEPT_ON_THE_REFERENCE}
            reference = {"$ref": f"#/components/schemas/{name}"}
            node.clear()
            if extras:
                node["allOf"] = [reference]
                node.update(extras)
            else:
                node.update(reference)
            return
        for child in node.values():
            _replace(child, used)
    elif isinstance(node, list):
        for child in node:
            _replace(child, used)


def name_money_types(
    result: dict[str, Any], generator: Any, request: Any, public: bool
) -> dict[str, Any]:
    """The drf-spectacular post-processing hook (``SPECTACULAR_SETTINGS["POSTPROCESSING_HOOKS"]``)."""
    used: set[str] = set()
    _replace(result, used)
    if used:
        schemas = result.setdefault("components", {}).setdefault("schemas", {})
        for name in sorted(used):
            schemas[name] = COMPONENTS[name]
    return result

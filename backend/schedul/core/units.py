"""Splitting units out of house-format field names.

House convention puts the unit in brackets at the end of a field name, so
``Supply Airflow (l/s)`` renders as ``Supply Airflow`` on the header row and
``l/s`` on the unit row beneath it. Some trailing brackets are not units and
must stay in the header text.

Vendored from ``build_project.py`` unchanged in behaviour; the sample files were
generated with these exact rules and must keep matching.
"""

from __future__ import annotations

# Parenthetical suffixes that are NOT units and must stay in the header text.
NOT_A_UNIT: frozenset[str] = frozenset({"BS EN 1886", "Initials", "n"})

# Stored plain (JSON-safe, typeable), rendered pretty.
UNIT_PRETTY: dict[str, str] = {"degC": "°C", "m2": "m²", "m3": "m³"}

UNIT_PLAIN: dict[str, str] = {v: k for k, v in UNIT_PRETTY.items()}


def split_unit(field: str) -> tuple[str, str]:
    """Split ``'Supply Airflow (l/s)'`` into ``('Supply Airflow', 'l/s')``.

    Returns ``(field, "")`` when the trailing bracket is not a unit. Handles
    nested parentheses, so ``'Specific Fan Power (W/(l/s))'`` yields
    ``('Specific Fan Power', 'W/(l/s)')`` rather than splitting at the inner
    bracket.
    """
    if not field.endswith(")"):
        return field, ""
    depth = 0
    for i in range(len(field) - 1, -1, -1):
        if field[i] == ")":
            depth += 1
        elif field[i] == "(":
            depth -= 1
            if depth == 0:
                inner = field[i + 1 : -1]
                if inner in NOT_A_UNIT:
                    return field, ""
                return field[:i].strip(), UNIT_PRETTY.get(inner, inner)
    return field, ""


def pretty_unit(unit: str) -> str:
    """Render a stored unit for display: ``degC`` -> ``°C``."""
    return UNIT_PRETTY.get(unit, unit)


def plain_unit(unit: str) -> str:
    """Normalise a displayed unit back to storage form: ``°C`` -> ``degC``."""
    return UNIT_PLAIN.get(unit, unit)


def join_unit(name: str, unit: str) -> str:
    """Recombine a name and unit into the legacy single-string field name.

    Inverse of :func:`split_unit` for the round-trip test against v1 schemas.
    """
    if not unit:
        return name
    return f"{name} ({plain_unit(unit)})"

"""Adoption of v1 data.

v1 kept every equipment type in one ``schema.json`` and gave the file a
``number`` field the builder never read. Migration splits it into per-type
catalogue entries at version 1, drops the dead field, and assigns each type its
volume -- which under the new model follows the equipment type rather than the
project (SPEC.md 5.2).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalogue import ScheduleType, from_legacy, to_legacy

__all__ = ["VOLUME_BY_CODE", "import_schema", "round_trip_matches"]

#: Volume by equipment type. v1 hardcoded ``5_6`` on the whole project, which is
#: why every sample file carries it; ventilation equipment should have been
#: ``5_7``. Assigned here as the default a firm can override per type.
VOLUME_BY_CODE: dict[str, str] = {
    "MVHR": "5.7",       # Ventilation
    "AHU": "5.7",        # Ventilation
    "SUPGRILLE": "5.7",  # Ventilation
    "EXTGRILLE": "5.7",  # Ventilation
    "FCU": "5.6",        # Heating and cooling
    "PUMP": "5.6",       # Heating and cooling
    "RAD": "5.6",        # Heating and cooling
    "EWH": "5.3",        # Domestic services
    "RADPANEL": "5.6",   # Heating and cooling
}


def import_schema(path: str | Path, *, default_volume: str = "5.6") -> list[ScheduleType]:
    """Read a v1 ``schema.json`` and return one schedule type per equipment type."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    types: list[ScheduleType] = []
    for entry in data.get("equipment_types", []):
        code = str(entry.get("code", "")).strip().upper()
        types.append(from_legacy(entry, volume=VOLUME_BY_CODE.get(code, default_volume)))
    return types


def round_trip_matches(path: str | Path) -> tuple[bool, list[str]]:
    """Phase 1 checkpoint: every v1 type survives the conversion unchanged.

    Converts each equipment type to a catalogue entry and back to the v1
    three-list shape, and compares. Returns ``(ok, differences)``.

    The dead ``number`` field is excluded from the comparison: dropping it is
    the point of the migration, not a loss.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    differences: list[str] = []

    for entry in data.get("equipment_types", []):
        original: dict[str, Any] = {
            k: v for k, v in entry.items() if k not in ("number",)
        }
        rebuilt = to_legacy(from_legacy(entry))

        for key in ("code", "title", "short"):
            if original.get(key, "") != rebuilt.get(key, ""):
                differences.append(
                    f"{entry.get('code')}: {key} {original.get(key)!r} != {rebuilt.get(key)!r}"
                )

        for key in ("instance_fields", "type_fields", "derived_fields"):
            was = [list(row) for row in original.get(key, [])]
            now = [list(row) for row in rebuilt.get(key, [])]
            if was != now:
                for i, (a, b) in enumerate(zip(was, now)):
                    if a != b:
                        differences.append(f"{entry.get('code')}: {key}[{i}] {a!r} != {b!r}")
                if len(was) != len(now):
                    differences.append(
                        f"{entry.get('code')}: {key} length {len(was)} != {len(now)}"
                    )

    return (not differences), differences

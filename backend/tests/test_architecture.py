"""The rule that keeps the domain portable.

SPEC.md section 3: ``core/`` must not import anything from the UI layer,
enforced by walking the AST of every file in ``core/``. The tkinter shell is
gone, but the rule matters more now, not less: it is what let the front end
change from a desktop GUI to a web app without touching the domain logic, and
what will let it change again.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "schedul" / "core"

#: Layers core/ must never reach into. Everything here either binds core to a
#: transport (web), a store (database), or a toolkit (tkinter).
FORBIDDEN_PREFIXES = (
    "schedul.api",
    "schedul.db",
    "schedul.export",
    "tkinter",
    "fastapi",
    "starlette",
    "sqlalchemy",
    "pydantic",
)


def core_modules() -> list[Path]:
    return sorted(p for p in CORE.rglob("*.py"))


def imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, stays inside core/
                continue
            if node.module:
                names.append(node.module)
    return names


def test_core_modules_were_found():
    assert core_modules(), "no core modules to check; the path is probably wrong"


@pytest.mark.parametrize("path", core_modules(), ids=lambda p: p.name)
def test_core_does_not_import_the_outer_layers(path: Path):
    offending = [
        name
        for name in imported_names(path)
        for prefix in FORBIDDEN_PREFIXES
        if name == prefix or name.startswith(prefix + ".")
    ]
    assert not offending, (
        f"{path.name} imports {offending}. core/ holds the domain logic and must "
        f"stay drivable without a web server, a database or a UI toolkit."
    )


@pytest.mark.parametrize("path", core_modules(), ids=lambda p: p.name)
def test_core_modules_parse_and_are_importable(path: Path):
    module = "schedul.core." + path.relative_to(CORE).with_suffix("").as_posix().replace("/", ".")
    module = module.removesuffix(".__init__")
    __import__(module)


def test_the_whole_domain_is_drivable_without_any_outer_import():
    """The practical version of the rule: build a document number end to end
    using nothing but core."""
    import sys

    before = set(sys.modules)

    from schedul.core.house import DEFAULT_NAMING
    from schedul.core.migrate import import_schema
    from schedul.core.naming import NamingScheme, ResolutionContext, volume_context

    repo = Path(__file__).resolve().parents[2]
    types = import_schema(repo / "vendor" / "schema.json")
    scheme = NamingScheme.from_dict(DEFAULT_NAMING)
    ahu = next(t for t in types if t.code == "AHU")

    docnum = scheme.document_number(
        ResolutionContext(
            project={"project_number": "CM4220"},
            building={"building": "HQ049"},
            type=volume_context(ahu.volume, scheme),
            schedule={"number": 11},
        )
    )
    assert docnum == "CM4220-BOV-5_7-HQ049-SC-M-00000011-G00300-XX-XX"

    newly_imported = set(sys.modules) - before
    assert not [m for m in newly_imported if m.split(".")[0] in ("tkinter", "fastapi", "sqlalchemy")]

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def legacy_schema(repo_root: Path) -> Path:
    return repo_root / "vendor" / "schema.json"


@pytest.fixture(scope="session")
def sample_schedules(repo_root: Path) -> Path:
    return repo_root / "vendor" / "reference_files" / "schedules"


@pytest.fixture(scope="session")
def catalogue_types(legacy_schema: Path):
    from schedul.core.migrate import import_schema

    return import_schema(legacy_schema)

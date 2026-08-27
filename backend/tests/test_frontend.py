"""The browser-side rules, run through Node.

The grid's selection model, its keyboard rules and its block-paste planner are
the parts of the front end that have to be exactly right, so they live as plain
modules under ``frontend/js/grid/`` with no DOM in them and are tested directly.

Running them from pytest is deliberate: one command runs the whole suite, and a
broken keyboard rule fails the build rather than waiting to be noticed by hand.
Node is not a build dependency of the app -- there is still no build step -- so
these skip themselves when it is not installed, the same way the tests that need
LibreOffice do.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
TESTS = FRONTEND / "tests"

node = shutil.which("node")
needs_node = pytest.mark.skipif(node is None, reason="Node is not installed")


def test_the_frontend_tests_exist():
    assert sorted(p.name for p in TESTS.glob("*.test.mjs")), "no frontend tests found"


@needs_node
@pytest.mark.parametrize(
    "path", sorted(TESTS.glob("*.test.mjs")), ids=lambda p: p.stem
)
def test_frontend_module(path: Path):
    result = subprocess.run(
        [node, "--test", str(path)],
        capture_output=True, text=True, timeout=120, check=False,
        cwd=str(FRONTEND.parent),
    )
    assert result.returncode == 0, result.stdout + result.stderr

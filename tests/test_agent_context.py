"""Agent context files must delegate, not duplicate.

CLAUDE.md is the single source of truth for architecture and domain guidance.
GEMINI.md (Gemini CLI) imports it. Any future per-agent context file should do
the same.

The temptation is to copy CLAUDE.md, because each tool reads its own filename.
Resist it: every serious defect this repo has recorded came from two copies of
one thing drifting -- the two `resolve_model()` implementations (now guarded by
test_shared_source_drift.py), a second CLI that left seven commands
undiscoverable, and a guide that accumulated 14 references to a deleted module
path. A duplicated context file is the worst of the set, because nothing fails
when it goes stale; it just quietly teaches the wrong thing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CANONICAL = Path("CLAUDE.md")
DELEGATING = [Path("GEMINI.md")]

# A pointer file needs room to say *why* it points, and to repeat the one or two
# things a skimming reader must not miss. It does not need room for a copy of
# the architecture. CLAUDE.md is ~540 lines; this is a small fraction of it.
MAX_LINES = 120


def test_the_canonical_file_exists():
    """Guards every test below: if CLAUDE.md moved, they are all vacuous."""
    assert CANONICAL.exists()


@pytest.mark.parametrize("path", DELEGATING, ids=lambda p: p.name)
def test_the_delegating_file_imports_the_canonical_one(path: Path):
    text = path.read_text()
    assert "@CLAUDE.md" in text, (
        f"{path} must import CLAUDE.md (Gemini CLI's `@path` syntax) rather than restating it."
    )


@pytest.mark.parametrize("path", DELEGATING, ids=lambda p: p.name)
def test_the_delegating_file_has_not_grown_a_copy(path: Path):
    lines = len(path.read_text().splitlines())
    assert lines <= MAX_LINES, (
        f"{path} is {lines} lines against a {MAX_LINES} ceiling. It is a pointer; "
        f"guidance belongs in CLAUDE.md, or every future edit has to be made "
        f"twice and will not be."
    )


@pytest.mark.parametrize("path", DELEGATING, ids=lambda p: p.name)
def test_the_delegating_file_does_not_restate_canonical_sections(path: Path):
    """Section headings are the cheap signal that a copy has started.

    Catches the realistic failure -- someone pastes in "## Architecture" or
    "### Model Registry" to make the file self-contained -- without forbidding
    a pointer file from having headings of its own.
    """
    canonical_headings = {
        line.strip()
        for line in CANONICAL.read_text().splitlines()
        if line.startswith(("## ", "### "))
    }
    delegating_headings = {
        line.strip() for line in path.read_text().splitlines() if line.startswith(("## ", "### "))
    }
    shared = canonical_headings & delegating_headings
    assert not shared, f"{path} restates CLAUDE.md sections {sorted(shared)}. Import, do not copy."


def test_the_heading_check_can_actually_fire(tmp_path):
    """A guard never seen firing is not known to work."""
    canonical_headings = [
        line.strip() for line in CANONICAL.read_text().splitlines() if line.startswith("## ")
    ]
    assert canonical_headings, "CLAUDE.md has no `## ` headings; the check is vacuous"

    planted = tmp_path / "FAKE.md"
    planted.write_text(f"# Fake\n\n@CLAUDE.md\n\n{canonical_headings[0]}\n\nA copy.\n")
    with pytest.raises(AssertionError, match=re.escape("restates CLAUDE.md sections")):
        test_the_delegating_file_does_not_restate_canonical_sections(planted)

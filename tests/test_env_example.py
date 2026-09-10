"""`.env.example` is the only environment contract we can actually test.

`.env` is gitignored, so nothing can check a real one. This file is what a new clone
copies, so a hazard documented here is a hazard avoided everywhere.

Two invariants, both from 2026-09-09:

- **`GOOGLE_GENAI_USE_ENTERPRISE` must not be set.** `google-genai` reads it as a second,
  independent way of saying "use Vertex", so any API-key client that inherits it gets
  `401 API keys are not supported by this API` *even with `GOOGLE_GENAI_USE_VERTEXAI`
  unset*. Nothing in `wrangler/` reads it, so it buys nothing and costs a whole debugging
  session -- it cost three failed PaperBanana runs and a wrong diagnosis before being
  found.
- **`GOOGLE_API_KEY` must be listed.** It is the sole feed for `PAPERBANANA_API_KEY`, and
  without it every report silently renders matplotlib instead of PaperBanana.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


def _assignments(path: Path) -> dict[str, str]:
    """Uncommented `KEY=value` lines only.

    Comments are excluded deliberately: this repo has twice been bitten by a guard that
    matched the prose explaining a hazard rather than the directive causing it, most
    recently in `Dockerfile.pipeline`. The warning text is supposed to name the variable.
    """
    out = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if m := re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", stripped):
            out[m.group(1)] = m.group(2)
    return out


def test_use_enterprise_is_not_set():
    assert "GOOGLE_GENAI_USE_ENTERPRISE" not in _assignments(ENV_EXAMPLE), (
        "GOOGLE_GENAI_USE_ENTERPRISE is set in .env.example. Nothing in wrangler/ reads "
        "it, and google-genai treats it as 'use Vertex' -- every child process inherits "
        "it and any API-key client then fails with 401, even with USE_VERTEXAI unset. "
        "See docs/notes/repo-traps.md."
    )


def _code_string_constants(source: str) -> set[str]:
    """String literals that are *code*, excluding comments and docstrings.

    Comments never reach the AST, and docstrings are skipped explicitly. Written this
    way because the first version of the test below matched `charts.py`'s comment
    explaining the hazard -- the exact prose-vs-code trap this repo has hit twice before
    (`Dockerfile.pipeline`, `tests/test_models.py`). Prose may name the variable; only
    code counts.
    """
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    }


def test_nothing_in_wrangler_reads_use_enterprise():
    """The variable is only safe to omit while no code depends on it.

    If someone makes it load-bearing, dropping it from .env becomes a silent behaviour
    change rather than a cleanup -- so that turn of events should fail here, loudly,
    rather than be discovered in a campaign.
    """
    pkg = ENV_EXAMPLE.parent / "wrangler"
    readers = [
        str(py.relative_to(pkg.parent))
        for py in pkg.rglob("*.py")
        if "GOOGLE_GENAI_USE_ENTERPRISE" in _code_string_constants(py.read_text())
    ]
    assert not readers, (
        f"{readers} now reference GOOGLE_GENAI_USE_ENTERPRISE in code. It is documented "
        "as read-by-nothing and omitted from .env.example on that basis; if it has "
        "become load-bearing, update both."
    )


def test_google_api_key_is_listed():
    """Absent, PaperBanana degrades to matplotlib and only says so in a log line."""
    assert "GOOGLE_API_KEY" in _assignments(ENV_EXAMPLE), (
        "GOOGLE_API_KEY is missing from .env.example. It is the only feed for "
        "PAPERBANANA_API_KEY (core/config.py), which reporting/charts.py hands to the "
        "PaperBanana subprocess; without it every chart silently falls back to matplotlib."
    )


def test_the_hazard_is_still_explained():
    """The guard above only stops the variable coming back; the comment says why.

    A bare assertion with no rationale in the file it guards is how a future reader
    "fixes" the test by adding the variable.
    """
    assert "GOOGLE_GENAI_USE_ENTERPRISE" in ENV_EXAMPLE.read_text(), (
        "The explanatory comment naming GOOGLE_GENAI_USE_ENTERPRISE was removed from "
        ".env.example. Keep it: the point is to warn, not merely to omit."
    )

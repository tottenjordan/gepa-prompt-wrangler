"""A region string may appear in exactly one place in `wrangler/`.

This is the ninth guard in the "no literal X outside its registry" family, alongside
model ids (`test_models.py`), the Vertex client (`test_clients.py`), the SDK's private
surface, the image pins, the hand-synced source pairs, `manifest.pairs`, prompt tool
names, and `.env.example`.

**Why a region literal is not a style problem here.** CLAUDE.md's location rule is that
`core/models.py:model_location()` decides, and it decides differently per family:

| family | location |
| --- | --- |
| Gemini 2.x | `GCP_REGION` |
| Gemini 3.x | `global` |
| Claude, all versions | `global` |

Gemini 3.x and Claude are **not servable from a region**. A stray `"us-central1"` on one
of those paths does not degrade, it fails outright with

    Publisher Model .../locations/us-central1/publishers/anthropic/models/claude-sonnet-4-6
    is not servable in region us-central1

and the repo has already been bitten by the mirror image of this — `GOOGLE_CLOUD_LOCATION`
is process-wide, but one process routes across five tiers at once, so no single value is
right for all of them.

**`"global"` is deliberately allowed.** It is the *correct* endpoint for two of the three
families, not a hardcoded region — a guard that flagged it would be pointing at the ten
call sites that are right and away from the one kind that is wrong. `GLOBAL_LOCATION` in
`models.py` exists for the same reason and is not what this file polices.

So the rule is narrow: **a regional literal (`<geo>-<place><digit>`) may appear only where
`FALLBACK_REGION` is defined.** Everything else reaches a region through `GCP_REGION`,
`FALLBACK_REGION`, or `model_location()`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "wrangler"

# GCP regional endpoints: us-central1, europe-west4, asia-northeast1, us-east5, ...
# Deliberately does not match "global", which is not a region.
REGION_RE = re.compile(r"^(us|europe|asia|australia|northamerica|southamerica|me|africa)-[a-z]+\d$")

# The single definition site. Not a blanket file exemption -- only the constant.
DEFINITION_SITE = ("core/models.py", "FALLBACK_REGION")


def _string_constants(path: Path) -> list[tuple[int, str]]:
    """String literals that are code, excluding comments and docstrings.

    Comments never reach the AST. Docstrings are skipped explicitly, because prose that
    *names* a region while explaining the rule is exactly what this guard must not fire
    on -- the same prose-vs-code trap that has caught `Dockerfile.pipeline`,
    `test_models.py`, and `test_env_example.py` in this repo.
    """
    tree = ast.parse(path.read_text())
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
    return [
        (n.lineno, n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def _is_definition(path: Path, lineno: int) -> bool:
    rel, const = DEFINITION_SITE
    if path.as_posix().replace(PKG.as_posix() + "/", "") != rel:
        return False
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == const for t in node.targets
        ):
            return node.lineno <= lineno <= (node.end_lineno or node.lineno)
    return False


def _violations() -> list[str]:
    out = []
    for py in sorted(PKG.rglob("*.py")):
        for lineno, value in _string_constants(py):
            if REGION_RE.match(value) and not _is_definition(py, lineno):
                rel = py.as_posix().replace(PKG.parent.as_posix() + "/", "")
                out.append(f"{rel}:{lineno}: {value!r}")
    return out


def test_no_region_literal_outside_the_definition():
    assert not _violations(), (
        "Regional literals outside core/models.py:FALLBACK_REGION:\n  "
        + "\n  ".join(_violations())
        + "\n\nReach a region through GCP_REGION, FALLBACK_REGION, or model_location(). "
        "Gemini 3.x and Claude are not servable from a region — a hardcoded one fails "
        "outright rather than degrading."
    )


def test_global_is_not_flagged():
    """The guard must not point at the ten call sites that are correct.

    `"global"` is the required endpoint for Gemini 3.x and Claude. Flagging it would
    invert the rule.
    """
    assert not REGION_RE.match("global")
    assert not REGION_RE.match("GLOBAL_LOCATION")


def test_the_regex_recognises_real_regions():
    for region in ("us-central1", "us-east5", "europe-west4", "asia-northeast1"):
        assert REGION_RE.match(region), region


def test_prose_naming_a_region_is_not_a_violation():
    """A docstring or comment explaining the rule has to be able to name the value."""
    import tempfile

    src = '''"""A docstring mentioning us-central1."""
# a comment mentioning us-central1
X = "global"
'''
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(src)
        path = Path(f.name)
    try:
        assert not [v for _, v in _string_constants(path) if REGION_RE.match(v)]
    finally:
        path.unlink()


def test_the_definition_site_itself_is_exempt():
    models = PKG / "core" / "models.py"
    hits = [(ln, v) for ln, v in _string_constants(models) if REGION_RE.match(v)]
    assert hits, "expected FALLBACK_REGION to hold a regional literal"
    assert all(_is_definition(models, ln) for ln, _ in hits), (
        "core/models.py has a regional literal outside the FALLBACK_REGION assignment"
    )

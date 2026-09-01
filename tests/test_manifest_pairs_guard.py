"""Guard: `manifest.pairs` is the raw, undiscriminated list -- it still has the
opus pair `enabled: false` was added to stop. `Manifest.enabled_pairs` is what
a caller choosing what to run must read instead (see CLAUDE.md's "Read
manifest.enabled_pairs, never manifest.pairs" rule and
docs/analysis/2026-09-01-opus-serving-failure.md). Two call sites read the raw
list anyway -- `Experiment.create` and `wrangler/orchestration/runner.py` --
and neither the local `wrangler deploy manifest.yaml` path nor
`WranglerPipeline` skipped the disabled pair.

This walks the AST rather than grepping, the same shape as
tests/test_models.py's literal-model-id guard: comments and docstrings that
narrate the history above (like this one) don't trip it, only code does.

Like that guard, this is a heuristic, not a type checker, and it has real
blind spots: it only recognizes `.pairs` on the bare name `manifest`, a
variable assigned straight from `PairFactory.load(...)`, or an attribute
chain ending in `.manifest`. It will miss a same-scope alias
(`m = manifest; m.pairs`), a directly chained call
(`PairFactory.load(path).pairs`), and a parameter named anything other than
`manifest` (`def helper(m): return m.pairs`) -- none of those match the
patterns above, and there is no data-flow analysis backing this up. It
catches every real manifest.pairs site in this codebase today, checked by
hand, but a determined or careless rewrite can still get a raw `.pairs` read
past it.
"""

import ast
from pathlib import Path

# (relative path, line number) -> reason a raw `.pairs` read is correct there.
# Keep this small: anything not listed here must go through
# `Manifest.enabled_pairs`, or `manifest.get_pair()` for a single named pair.
PAIRS_READ_EXCEPTIONS: dict[tuple[str, int], str] = {
    ("wrangler/orchestration/experiment.py", 108): (
        "Experiment.create persists every declared pair, disabled ones "
        "included, with their enabled/disabled_reason fields -- dropping "
        "disabled pairs here instead broke `--pair <disabled-id>` on every "
        "stage function, which reconstructs the manifest from this config "
        "and calls _filter_pairs on it. pair_ids (below) filters enabled "
        "back out for anything that chooses what to run unfiltered."
    ),
    ("wrangler/orchestration/stages.py", 246): (
        "_filter_pairs() is what implements enabled_pairs' semantics for a "
        "named --pair override -- an explicitly-named disabled pair must "
        "still run, which enabled_pairs alone cannot produce because it has "
        "already dropped it. This is the one place allowed to see the full "
        "list so it can print why each disabled entry was skipped."
    ),
    ("wrangler/pipeline/deploy_pipeline.py", 383): (
        "Counts the total only, for the '(N disabled)' log line -- every "
        "selection above it already reads manifest.enabled_pairs."
    ),
    ("wrangler/cli.py", 537): (
        "dry-run reports what a sweep would skip and why, the same "
        "information _filter_pairs prints at run time -- it does not select "
        "what runs."
    ),
}


def _looks_like_pair_factory_load(value: ast.AST) -> bool:
    """True for `PairFactory.load(...)`, however the result gets named."""
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr == "load"
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "PairFactory"
    )


def _manifest_pairs_reads() -> list[tuple[str, int]]:
    """Yield (relative path, line) for every `.pairs` read on a manifest.

    A plain AST walk can't tell a `Manifest` from any other object with a
    `.pairs` attribute (there is no type inference here) -- `Analysis.pairs`
    in reporting/analyzer.py is a list of `PairAnalysis`, not agent-prompt
    pairs, and would false-positive on a bare "attr == 'pairs'" match. So this
    narrows to the shapes the manifest actually appears under in this
    codebase: the bare name `manifest` (the overwhelming convention), a local
    variable assigned straight from `PairFactory.load(...)`, or an attribute
    chain ending in `.manifest` (`self.manifest`, `exp.manifest`,
    `pipeline.manifest`).

    That narrowing is also this function's blind spot: it does no data-flow
    tracing past a single direct assignment, so `m = manifest` followed by
    `m.pairs`, a chained `PairFactory.load(path).pairs` with no intermediate
    name, or a function parameter named anything other than `manifest` (e.g.
    `def helper(m): return m.pairs`) all pass through undetected.
    """
    found: list[tuple[str, int]] = []

    for path in sorted(Path("wrangler").rglob("*.py")):
        rel = path.as_posix()
        if rel == "wrangler/core/factory.py":
            continue

        tree = ast.parse(path.read_text(), filename=rel)

        manifest_like = {"manifest"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and _looks_like_pair_factory_load(node.value):
                    manifest_like.add(target.id)

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Attribute) and node.attr == "pairs"):
                continue
            base = node.value
            is_manifest_name = isinstance(base, ast.Name) and base.id in manifest_like
            is_dot_manifest = isinstance(base, ast.Attribute) and base.attr == "manifest"
            if is_manifest_name or is_dot_manifest:
                found.append((rel, node.lineno))

    return found


def test_manifest_pairs_is_read_only_through_enabled_pairs_or_a_listed_exception():
    offenders = [
        f"{path}:{line}"
        for path, line in _manifest_pairs_reads()
        if (path, line) not in PAIRS_READ_EXCEPTIONS
    ]
    assert not offenders, (
        "Raw manifest.pairs read outside the registry of exceptions -- switch "
        "to manifest.enabled_pairs, or add a justified entry to "
        "PAIRS_READ_EXCEPTIONS in this file:\n" + "\n".join(offenders)
    )


def test_every_exception_still_names_a_real_pairs_read():
    """An exception whose site moved or was fixed should be deleted, not orphaned.

    A stale entry looks like coverage without being coverage -- the next
    regression at that (path, line) would pass silently.
    """
    live = set(_manifest_pairs_reads())
    stale = [key for key in PAIRS_READ_EXCEPTIONS if key not in live]
    assert not stale, f"Stale PAIRS_READ_EXCEPTIONS entries (no longer a .pairs read): {stale}"

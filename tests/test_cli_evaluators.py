"""The online-eval commands must be reachable from `wrangler`.

Seven commands lived behind a `COMMANDS` dict in
`wrangler/eval/online_evaluators.py` and a `python -m` entry point, and none
of them appeared in `wrangler --help`. `trace-health` in particular is the
diagnostic for the OTel span-drop failure in docs/notes/silent-failures.md #8
-- a tool nobody could find.

Worse, the guide documented the *pre-reorganisation* module path, so all 14 of
its command examples failed with `No module named wrangler.online_evaluators`.
A second entry point that nothing tests is how that happens.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from click.testing import CliRunner

from wrangler.cli import main

EXPECTED = {"list", "create", "verify", "trace-health", "prune", "delete", "cleanup"}


def test_the_module_still_declares_the_commands_we_expect():
    """Pins the source of truth so the next test cannot drift silently."""
    from wrangler.eval.online_evaluators import COMMANDS

    assert set(COMMANDS) == EXPECTED, (
        "COMMANDS changed. Update EXPECTED and the CLI group in the same commit "
        "-- the two drifting is what left seven commands undiscoverable."
    )


def test_the_evaluators_group_exposes_every_command():
    result = CliRunner().invoke(main, ["evaluators", "--help"])
    assert result.exit_code == 0, result.output
    missing = {c for c in EXPECTED if c not in result.output}
    assert not missing, f"{missing} exist in the module but not in `wrangler evaluators`"


def test_prune_is_dry_run_without_yes():
    """Matches `engines prune`. Deleting by default is how you lose an evaluator."""
    result = CliRunner().invoke(main, ["evaluators", "prune", "--help"])
    assert result.exit_code == 0, result.output
    assert "--yes" in result.output


def test_delete_requires_an_evaluator_id():
    """The module form printed a usage string instead; click should enforce it."""
    result = CliRunner().invoke(main, ["evaluators", "delete"])
    assert result.exit_code != 0


def test_the_guide_only_references_importable_modules():
    """docs/online_eval_guide.md pointed at a module deleted in the package
    reorganisation, so every documented command failed."""
    text = Path("docs/online_eval_guide.md").read_text()
    referenced = set(re.findall(r"python -m (wrangler[\w.]*)", text))
    bad = sorted(m for m in referenced if importlib.util.find_spec(m) is None)
    assert not bad, f"{bad} are documented but cannot be imported"


def test_no_doc_references_a_pre_reorganisation_module_path():
    """`wrangler.online_evaluators` and `wrangler.online_monitors` both moved
    under `wrangler.eval.`. Catch the names in prose too, not just in runnable
    commands -- three of the guide's stale references were prose, and prose is
    what a reader copies from.

    `docs/plans/` is excluded: a plan is a dated record of work, and one that
    describes this very bug has to be able to quote the broken path. Same
    prose-versus-code distinction that makes test_models.py walk the AST and
    the Dockerfile guards strip comments -- a document *about* a wrong name
    legitimately contains it.
    """
    targets = [
        p
        for p in [*Path("docs").rglob("*.md"), Path("README.md"), Path("CLAUDE.md")]
        if p.exists() and "docs/plans/" not in p.as_posix()
    ]
    assert targets, "found no docs to check; the glob is wrong"

    stale = [
        f"{path}: {name}"
        for path in targets
        for name in ("wrangler.online_evaluators", "wrangler.online_monitors")
        if name in path.read_text()
    ]
    assert not stale, f"stale module paths: {stale}"

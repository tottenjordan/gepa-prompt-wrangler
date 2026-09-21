"""`WranglerPipeline` is the legacy path, and reaching it must say so.

Task 5 of the 2026-09-21 audit asked whether this module is supported or dead. The evidence
says **neither** -- it is documented in README as usable, reachable from two CLI paths, and
functionally behind the supported path in ways a user cannot see:

| | `stages.py` (supported) | `runner.py` (legacy) |
| --- | --- | --- |
| health-gates deploys | 14 references | **0** |
| `score_repeats` | yes | **no** |
| `skip_optimize` (control arms) | yes | **no** |
| `forward_rationale` (patch 4b) | yes | **no** |

Its last six commits are all repo-wide sweeps -- lint, timezone-awareness, the registry
migration -- with no feature work since. Meanwhile `stages.py` gained all three knobs above.

**The health gate is the one that costs real money.** CLAUDE.md: roughly four in ten
deployments come up unable to serve, returning 200 with no inference, so an ungated deploy
hands the eval an engine that silently drops a third of its cases and the resulting delta
measures dropout rather than the prompt. `_deploy_pair` calls `deploy_agent_from_source`
directly with no gate and no reroll.

So it is deprecated rather than deleted: deletion would break a documented flag mid-cycle,
and nothing proves no one relies on it. A warning that **names the replacement** is the
honest middle.
"""

from __future__ import annotations

import inspect
from pathlib import Path

RUNNER = Path("wrangler/orchestration/runner.py")


class TestTheLegacyPathAnnouncesItself:
    def test_constructing_the_runner_warns(self, tmp_path, monkeypatch):
        """A user on the legacy path must be told, not left to infer it from a doc."""
        import warnings

        from wrangler.orchestration.runner import WranglerPipeline

        manifest = tmp_path / "m.yaml"
        manifest.write_text(
            "name: t\nagent_module: a\neval_data: e\n"
            "pairs:\n  - id: p\n    model: gemini-3.5-flash\n    system_prompt: s\n"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            WranglerPipeline(str(manifest))
        messages = [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]
        assert messages, "constructing WranglerPipeline emitted no DeprecationWarning"

    def test_the_warning_names_the_replacement_and_the_risk(self, tmp_path):
        """'Deprecated' alone tells a user nothing about what to do or what they lose."""
        import warnings

        from wrangler.orchestration.runner import WranglerPipeline

        manifest = tmp_path / "m.yaml"
        manifest.write_text(
            "name: t\nagent_module: a\neval_data: e\n"
            "pairs:\n  - id: p\n    model: gemini-3.5-flash\n    system_prompt: s\n"
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            WranglerPipeline(str(manifest))
        text = " ".join(str(w.message) for w in caught).lower()
        assert "health" in text, "the warning does not mention the missing health gate"
        assert "wrangler pipeline run" in text or "stage_" in text, (
            "the warning does not name what to use instead"
        )


class TestTheEvidenceStaysTrue:
    """If the runner ever catches up, this test should fail and the deprecation be revisited."""

    def test_it_still_lacks_a_health_gate(self):
        src = RUNNER.read_text()
        assert "gate_engine_health" not in src, (
            "runner.py now health-gates. That was the main reason it was deprecated -- "
            "re-read the Task 5 evidence before leaving the warning in place."
        )

    def test_it_still_ignores_the_knobs_the_supported_path_honours(self):
        """Checked on the AST, not the source text.

        The first version of this grepped the file, and then failed the moment the
        deprecation warning *named* the three knobs in its message -- a false positive
        produced by exactly the source-text brittleness this audit kept finding. Identifiers
        in real code are `ast.Name` / `ast.Attribute` / keyword nodes; a mention inside a
        string literal or comment is not handling.
        """
        import ast

        tree = ast.parse(RUNNER.read_text())
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                used.add(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                used.add(node.arg)

        for knob in ("score_repeats", "skip_optimize", "forward_rationale"):
            assert knob not in used, (
                f"runner.py now handles {knob!r} in code; the deprecation rationale is out "
                f"of date -- re-read the Task 5 evidence before leaving the warning in place"
            )

    def test_the_supported_path_does_gate(self):
        """Guards the comparison itself, so the table above cannot quietly invert."""
        assert "gate_engine_health" in Path("wrangler/orchestration/stages.py").read_text()


class TestItIsStillReachable:
    """Deprecated, not removed -- the CLI paths must keep working."""

    def test_the_cli_still_imports_it(self):
        from wrangler import cli

        assert "WranglerPipeline" in inspect.getsource(cli)

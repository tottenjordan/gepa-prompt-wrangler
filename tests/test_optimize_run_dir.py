"""GEPA's working directory is the only per-candidate record a run produces. Keep it.

The optimize stage artifact records one `(optimized_prompt, chars)` pair per arm. GEPA's
`run_dir` holds **every candidate** it considered, with per-candidate validation subscores:
14 candidates spanning 78 to 12,741 characters on the one surviving local run. That is the
difference between one data point per nine-hour stage and fourteen.

It was being deleted with the container on every pipeline run -- the same failure the MCP
log upload was added to fix, and for the same reason it was worth fixing.

**These tests are structural.** Whether the upload *works* needs a pipeline run; what can be
checked here is that the component still calls it, that the path both sides use is one
definition, and that a failure cannot take a nine-hour stage down with it.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from wrangler.optimize.optimizer import gepa_run_dir

COMPONENTS = Path(__file__).resolve().parents[1] / "wrangler" / "pipeline" / "components.py"


def _component_source(name: str) -> str:
    """The component's own source, by AST.

    Not a fixed-size slice. `test_deploy_health_gate.py` sliced 6000 characters and broke on
    2026-09-16 when six added lines pushed the asserted call past the boundary -- it then
    failed with a SyntaxError about the test's own substring. Several sibling tests still
    slice; this one does not.
    """
    src = COMPONENTS.read_text()
    module = ast.parse(src)
    fn = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(src, fn) or ""


class TestThePathIsOneDefinition:
    def test_it_matches_the_expression_it_replaced(self):
        """A changed path would silently write GEPA's state somewhere the upload never looks.

        This is the exact expression `optimize()` used before the helper existed.
        """
        for module_path in ("/app/agents/sonnet_opt", "agents/sonnet_opt", "sonnet_opt"):
            legacy = os.path.join("outputs", "gepa_runs", os.path.basename(module_path))
            assert str(gepa_run_dir(module_path)) == legacy, module_path

    def test_it_uses_the_module_basename_not_the_whole_path(self):
        assert gepa_run_dir("/app/agents/sonnet_opt").name == "sonnet_opt"

    def test_the_optimizer_calls_the_helper_rather_than_rebuilding_the_path(self):
        opt = (
            Path(__file__).resolve().parents[1] / "wrangler" / "optimize" / "optimizer.py"
        ).read_text()
        assert 'os.path.join("outputs", "gepa_runs"' not in opt, (
            "optimize() rebuilds the run_dir path inline again. Two definitions can drift, "
            "and the upload would then find an empty directory and log nothing useful."
        )
        assert "gepa_run_dir(" in opt


class TestTheComponentUploadsIt:
    def test_the_optimize_component_uploads_the_run_dir(self):
        body = _component_source("optimize_single_agent")
        assert "gepa_run_dir" in body, (
            "the optimize component no longer uploads GEPA's run_dir, so every candidate "
            "prompt and score is discarded with the container again"
        )
        assert "gepa_run/" in body, "the upload prefix changed; analysis tooling looks there"

    def test_it_imports_the_helper_inside_the_component_body(self):
        """KFP serializes each component in isolation, so a module-level import is absent."""
        fn = next(
            n
            for n in ast.parse(COMPONENTS.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == "optimize_single_agent"
        )
        inner = {
            a.name for node in ast.walk(fn) if isinstance(node, ast.ImportFrom) for a in node.names
        }
        assert "gepa_run_dir" in inner, (
            "gepa_run_dir must be imported inside the component body -- KFP serializes "
            "each component in isolation and a module-level import will not be there"
        )

    def test_the_upload_cannot_fail_the_stage(self):
        """A nine-hour optimize stage may not die because artifact shipping had a bad day."""
        body = _component_source("optimize_single_agent")
        start = body.index("gepa_run_dir")
        # Walk back to the enclosing try, forward to its handler.
        before = body[:start]
        assert before.rstrip().endswith("try:") or "try:" in before[-400:], (
            "the run_dir upload is not inside a try block"
        )
        after = body[start : start + 2500]
        assert "except Exception" in after, (
            "the run_dir upload has no broad handler, so a GCS hiccup could fail the stage"
        )

    def test_there_is_a_size_cap(self):
        """generated_best_outputs_valset/ grows with the eval set; 3.7 MB is not a promise."""
        body = _component_source("optimize_single_agent")
        why = (
            "the run_dir upload has no size cap, so a large eval set could ship "
            "unbounded data out of a nine-hour stage"
        )
        assert "budget" in body, why
        assert "1024 * 1024" in body, why


class TestTheArtifactShapeWeDependOn:
    """Pins what the analysis tooling reads, against the one surviving local run."""

    RUN = Path(__file__).resolve().parents[1] / "outputs" / "gepa_runs" / "sonnet_opt"

    def test_candidates_json_has_prompts_but_no_scores(self):
        """Worth pinning because it is the intuitive file to reach for and it is not enough.

        The scores are in gepa_state.bin. A reader that used candidates.json alone would
        silently be unable to correlate anything.
        """
        path = self.RUN / "candidates.json"
        if not path.exists():
            pytest.skip("no local GEPA run to check against")
        import json

        cands = json.loads(path.read_text())
        assert isinstance(cands, list)
        assert cands
        assert all(isinstance(c, dict) for c in cands)
        assert not any("score" in k.lower() for c in cands for k in c), (
            "candidates.json now carries scores; the analysis script can stop unpickling"
        )

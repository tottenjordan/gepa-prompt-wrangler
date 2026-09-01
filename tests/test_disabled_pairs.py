"""Tests for disabling a pair without deleting it from the manifest.

Opus cannot currently be deployed healthily: fifteen gated deploys across three
model versions and two prompts produced nothing above 50% reach, against a
concurrent control of four tiers at 93-100%
(docs/analysis/2026-09-01-opus-serving-failure.md). Any eval against it measures
dropout rather than the prompt.

Deleting the pair would lose the model id, the agent module and the reason.
Commenting it out loses the reason too, and a commented block rots. A parsed
`enabled: false` with a `disabled_reason` keeps the configuration honest and
makes re-enabling a one-line change.
"""

import yaml

from wrangler.core.factory import PairFactory


def _manifest(tmp_path, pairs):
    p = tmp_path / "m.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "name": "m",
                "agent_module": "agents/x",
                "eval_data": "eval.yaml",
                "pairs": pairs,
            }
        )
    )
    return str(p)


def _pair(pid="p1", **kw):
    base = {"id": pid, "model": "gemini-3.5-flash", "system_prompt": "hi"}
    base.update(kw)
    return base


class TestParsing:
    def test_a_pair_is_enabled_by_default(self, tmp_path):
        m = PairFactory.load(_manifest(tmp_path, [_pair()]))
        assert m.pairs[0].enabled is True
        assert m.pairs[0].disabled_reason == ""

    def test_enabled_false_is_parsed(self, tmp_path):
        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False)]))
        assert m.pairs[0].enabled is False

    def test_the_reason_travels_with_it(self, tmp_path):
        """A disabled pair with no reason is a mystery six months later."""
        m = PairFactory.load(
            _manifest(tmp_path, [_pair(enabled=False, disabled_reason="0-50% reach")])
        )
        assert m.pairs[0].disabled_reason == "0-50% reach"

    def test_a_disabled_pair_is_still_loaded(self, tmp_path):
        """It stays in the manifest so the config is not lost."""
        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False), _pair("p2")]))
        assert [p.id for p in m.pairs] == ["p1", "p2"]


class TestFiltering:
    def test_a_disabled_pair_is_skipped_by_a_sweep(self, tmp_path):
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False), _pair("p2")]))
        assert [p.id for p in _filter_pairs(m, None)] == ["p2"]

    def test_the_skip_says_why(self, tmp_path, capsys):
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(
            _manifest(tmp_path, [_pair(enabled=False, disabled_reason="cannot serve")])
        )
        _filter_pairs(m, None)
        out = capsys.readouterr().out
        assert "p1" in out
        assert "cannot serve" in out

    def test_naming_a_disabled_pair_explicitly_still_runs_it(self, tmp_path):
        """`--pair opus` is a deliberate act; the flag should not be silently ignored."""
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False)]))
        assert [p.id for p in _filter_pairs(m, "p1")] == ["p1"]

    def test_all_pairs_disabled_yields_nothing_rather_than_everything(self, tmp_path):
        from wrangler.orchestration.stages import _filter_pairs

        m = PairFactory.load(
            _manifest(tmp_path, [_pair(enabled=False), _pair("p2", enabled=False)])
        )
        assert _filter_pairs(m, None) == []


class TestOpusIsActuallyDisabled:
    """The point of the exercise. Named so re-enabling is a deliberate diff."""

    def test_the_example_manifest_disables_opus(self):
        m = PairFactory.load("examples/multi_model_agents/manifest.yaml")
        opus = [p for p in m.pairs if "opus" in p.id.lower()]
        assert opus, "premise: the example manifest still has an opus pair"
        for p in opus:
            assert p.enabled is False, f"{p.id} should be disabled"
            assert p.disabled_reason, f"{p.id} needs a reason"


class TestBothSweepPathsRespectIt:
    """Local and pipeline are separate code paths and only one filtered.

    `stage_deploy` went through `_filter_pairs`; `deploy_pipeline` read
    `manifest.pairs` directly, so `wrangler pipeline run` would have deployed
    and evaluated opus after it was disabled everywhere else. Same shape as the
    health gate covering one of three deploy paths.
    """

    def test_enabled_pairs_excludes_disabled(self, tmp_path):
        m = PairFactory.load(_manifest(tmp_path, [_pair(enabled=False), _pair("p2")]))
        assert [p.id for p in m.enabled_pairs] == ["p2"]
        assert [p.id for p in m.pairs] == ["p1", "p2"], "raw list still holds everything"

    def test_the_pipeline_path_uses_enabled_pairs(self):
        from pathlib import Path

        src = Path("wrangler/pipeline/deploy_pipeline.py").read_text()
        assert "manifest.enabled_pairs" in src
        # Any remaining bare `manifest.pairs` must only be counting the total
        # for the "N disabled" message, never selecting what to run.
        for line in src.splitlines():
            if "manifest.pairs" in line and "enabled_pairs" not in line:
                assert "len(" in line, f"pipeline selects from raw pairs: {line.strip()}"

    def test_the_local_path_uses_the_filter(self):
        from pathlib import Path

        src = Path("wrangler/orchestration/stages.py").read_text()
        i = src.index("def _filter_pairs")
        assert "pair.enabled" in src[i : i + 900]

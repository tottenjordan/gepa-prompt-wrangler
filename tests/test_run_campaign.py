"""Tests for the campaign runner's pairing rule.

Two pipelines run at once, and the rule that makes that safe is that they must
straddle both publishers: Anthropic and Google are separate Vertex quota pools,
so a Claude arm and a Gemini arm do not contend, while two Claude arms would
just race each other into 429s.

That rule is the only thing standing between "double throughput for free" and
"half the requests fail", so it is checked before anything is submitted rather
than discovered in a log.
"""

from pathlib import Path

import pytest

from scripts.run_campaign import CAMPAIGNS, run_campaign, validate


class TestPairingRule:
    @pytest.mark.parametrize("campaign", sorted(CAMPAIGNS))
    def test_every_batch_straddles_both_publishers(self, campaign):
        assert validate(CAMPAIGNS[campaign]) == []

    def test_a_same_publisher_batch_is_rejected(self):
        bad = [("manifests/c07-sonnet5_manifest.yaml", "manifests/c07-sonnet46_manifest.yaml")]
        problems = validate(bad)
        assert problems
        assert "quota" in problems[0].lower()

    def test_a_missing_manifest_is_reported_not_crashed(self):
        problems = validate([("manifests/nope.yaml", "manifests/c07-pro_manifest.yaml")])
        assert any("missing" in p for p in problems)

    def test_every_referenced_manifest_exists(self):
        for batches in CAMPAIGNS.values():
            for a, b in batches:
                assert Path(a).is_file(), a
                assert Path(b).is_file(), b


class TestDryRunByDefault:
    def test_dry_run_submits_nothing(self, tmp_path, monkeypatch):
        launched = []
        monkeypatch.setattr("scripts.run_campaign.launch", lambda m, d: launched.append(m))
        assert run_campaign("07", confirm=False, log_dir=tmp_path) == 0
        assert launched == []

    def test_a_bad_pairing_refuses_even_with_yes(self, tmp_path, monkeypatch):
        launched = []
        monkeypatch.setattr("scripts.run_campaign.launch", lambda m, d: launched.append(m))
        monkeypatch.setitem(
            CAMPAIGNS,
            "bad",
            [("manifests/c07-sonnet5_manifest.yaml", "manifests/c07-sonnet46_manifest.yaml")],
        )
        assert run_campaign("bad", confirm=True, log_dir=tmp_path) == 1
        assert launched == []


class TestStagger:
    def test_an_optimize_batch_staggers(self, tmp_path, monkeypatch):
        """Both arms judge with gemini-3.5-flash, so their optimize phases share quota."""
        naps, procs = [], []

        class _P:
            def wait(self):
                return 0

        monkeypatch.setattr("scripts.run_campaign.launch", lambda m, d: procs.append(m) or _P())
        run_campaign("07", confirm=True, log_dir=tmp_path, sleep_fn=naps.append)
        assert naps, "campaign 07 optimizes; it must stagger"
        assert all(n > 0 for n in naps)

    def test_an_eval_only_batch_does_not_stagger(self, tmp_path, monkeypatch):
        """Campaign 06 skips optimize, so there is no shared-judge collision to avoid."""
        naps, procs = [], []

        class _P:
            def wait(self):
                return 0

        monkeypatch.setattr("scripts.run_campaign.launch", lambda m, d: procs.append(m) or _P())
        run_campaign("06", confirm=True, log_dir=tmp_path, sleep_fn=naps.append)
        assert naps == []
        assert len(procs) == 6, "three batches of two arms"

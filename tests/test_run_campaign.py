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
        monkeypatch.setattr("scripts.run_campaign.submit", lambda m, d: launched.append(m))
        assert run_campaign("07", confirm=False, log_dir=tmp_path) == 0
        assert launched == []

    def test_a_bad_pairing_refuses_even_with_yes(self, tmp_path, monkeypatch):
        launched = []
        monkeypatch.setattr("scripts.run_campaign.submit", lambda m, d: launched.append(m))
        monkeypatch.setitem(
            CAMPAIGNS,
            "bad",
            [("manifests/c07-sonnet5_manifest.yaml", "manifests/c07-sonnet46_manifest.yaml")],
        )
        assert run_campaign("bad", confirm=True, log_dir=tmp_path) == 1
        assert launched == []


class TestBatchesActuallySerialise:
    """`job.submit()` is non-blocking, so waiting on the submission waits ~90s.

    An earlier version did exactly that: `proc.wait()` on the CLI subprocess.
    All six campaign-06 arms would have been submitted within minutes and run
    concurrently on Vertex -- three Claude and three Gemini at once, which is
    the same-publisher contention the pairing exists to prevent. The runner has
    to wait for the *job*, not the submission.
    """

    def test_the_next_batch_waits_for_the_previous_jobs(self, tmp_path, monkeypatch):
        order = []

        def _submit(manifest, _log_dir):
            order.append(("submit", Path(manifest).stem))
            return f"job-{len(order)}"

        def _wait(job_ids, sleep_fn=None, state_fn=None):
            order.append(("wait", tuple(job_ids)))
            return dict.fromkeys(job_ids, "PipelineState.PIPELINE_STATE_SUCCEEDED")

        monkeypatch.setattr("scripts.run_campaign.submit", _submit)
        monkeypatch.setattr("scripts.run_campaign.wait_for_jobs", _wait)
        run_campaign("06", confirm=True, log_dir=tmp_path, sleep_fn=lambda _s: None)

        kinds = [k for k, _ in order]
        # submit, submit, wait — three times over, never two waits in a row and
        # never a third submit before the first wait.
        assert kinds == ["submit", "submit", "wait"] * 3

    def test_a_lookup_failure_is_not_treated_as_finished(self):
        """Otherwise the next batch launches on top of a running one."""
        from scripts.run_campaign import wait_for_jobs

        states = iter(["UNKNOWN", "PipelineState.PIPELINE_STATE_SUCCEEDED"])
        out = wait_for_jobs(["j1"], sleep_fn=lambda _s: None, state_fn=lambda _j: next(states))
        assert "SUCCEEDED" in out["j1"]

    def test_a_failed_job_does_not_stop_the_campaign(self, tmp_path, monkeypatch):
        """A half-finished campaign that says which half beats one that stops silently."""
        monkeypatch.setattr("scripts.run_campaign.submit", lambda m, d: "j")
        monkeypatch.setattr(
            "scripts.run_campaign.wait_for_jobs",
            lambda ids, **kw: dict.fromkeys(ids, "PipelineState.PIPELINE_STATE_FAILED"),
        )
        assert run_campaign("07", confirm=True, log_dir=tmp_path, sleep_fn=lambda _s: None) == 0

    def test_every_terminal_state_ends_the_wait(self):
        from scripts.run_campaign import wait_for_jobs

        for state in (
            "PIPELINE_STATE_SUCCEEDED",
            "PIPELINE_STATE_FAILED",
            "PIPELINE_STATE_CANCELLED",
        ):
            out = wait_for_jobs(["j"], sleep_fn=lambda _s: None, state_fn=lambda _j, s=state: s)
            assert out["j"] == state


class TestStagger:
    def test_an_optimize_batch_staggers(self, tmp_path, monkeypatch):
        """Both arms judge with gemini-3.5-flash, so their optimize phases share quota."""
        naps, procs = [], []

        monkeypatch.setattr("scripts.run_campaign.submit", lambda m, d: procs.append(m) or "j")
        monkeypatch.setattr(
            "scripts.run_campaign.wait_for_jobs",
            lambda ids, **kw: dict.fromkeys(ids, "PIPELINE_STATE_SUCCEEDED"),
        )
        run_campaign("07", confirm=True, log_dir=tmp_path, sleep_fn=naps.append)
        assert naps, "campaign 07 optimizes; it must stagger"
        assert all(n > 0 for n in naps)

    def test_an_eval_only_batch_does_not_stagger(self, tmp_path, monkeypatch):
        """Campaign 06 skips optimize, so there is no shared-judge collision to avoid."""
        naps, procs = [], []

        monkeypatch.setattr("scripts.run_campaign.submit", lambda m, d: procs.append(m) or "j")
        monkeypatch.setattr(
            "scripts.run_campaign.wait_for_jobs",
            lambda ids, **kw: dict.fromkeys(ids, "PIPELINE_STATE_SUCCEEDED"),
        )
        run_campaign("06", confirm=True, log_dir=tmp_path, sleep_fn=naps.append)
        assert naps == []
        assert len(procs) == 6, "three batches of two arms"


class TestValidateThenRun:
    """The campaign is released only if the validation arm actually succeeded.

    Six copies of a broken run cost five hours and teach nothing, and the two
    new code paths here (the skip_optimize branch, the in-component health gate)
    have never met the real service.
    """

    def test_a_successful_arm_releases_the_campaign(self, tmp_path, monkeypatch):
        from scripts import validate_then_run as vtr

        released = []
        monkeypatch.setattr(vtr, "submit", lambda m, d: "job-1")
        monkeypatch.setattr(
            vtr, "wait_for_jobs", lambda ids: dict.fromkeys(ids, "PIPELINE_STATE_SUCCEEDED")
        )
        monkeypatch.setattr(
            vtr, "run_campaign", lambda c, confirm, log_dir: released.append(c) or 0
        )
        assert vtr.main("06", tmp_path) == 0
        assert released == ["06"]

    def test_a_failed_arm_holds_everything_back(self, tmp_path, monkeypatch):
        from scripts import validate_then_run as vtr

        released = []
        monkeypatch.setattr(vtr, "submit", lambda m, d: "job-1")
        monkeypatch.setattr(
            vtr, "wait_for_jobs", lambda ids: dict.fromkeys(ids, "PIPELINE_STATE_FAILED")
        )
        monkeypatch.setattr(
            vtr, "run_campaign", lambda c, confirm, log_dir: released.append(c) or 0
        )
        assert vtr.main("06", tmp_path) == 1
        assert released == []

    def test_an_unreadable_state_holds_everything_back(self, tmp_path, monkeypatch):
        """UNKNOWN is not success, and must not be read as one."""
        from scripts import validate_then_run as vtr

        released = []
        monkeypatch.setattr(vtr, "submit", lambda m, d: "job-1")
        monkeypatch.setattr(vtr, "wait_for_jobs", lambda ids: {})
        monkeypatch.setattr(
            vtr, "run_campaign", lambda c, confirm, log_dir: released.append(c) or 0
        )
        assert vtr.main("06", tmp_path) == 1
        assert released == []

    def test_a_submit_failure_holds_everything_back(self, tmp_path, monkeypatch):
        from scripts import validate_then_run as vtr

        released = []

        def _boom(_m, _d):
            raise RuntimeError("quota")

        monkeypatch.setattr(vtr, "submit", _boom)
        monkeypatch.setattr(
            vtr, "run_campaign", lambda c, confirm, log_dir: released.append(c) or 0
        )
        assert vtr.main("06", tmp_path) == 1
        assert released == []

    def test_the_validation_arm_is_the_cheapest_one(self):
        """It must be eval-only and num_runs=1, or it is not cheap validation."""
        from scripts.validate_then_run import VALIDATION_ARM
        from wrangler.core.factory import PairFactory

        m = PairFactory.load(VALIDATION_ARM["06"])
        assert m.pipeline.get("skip_optimize") is True
        assert m.pipeline.get("num_runs") == 1

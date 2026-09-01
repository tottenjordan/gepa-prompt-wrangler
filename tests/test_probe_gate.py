"""Tests for the deploy-time engine health gate.

Campaign 01 measured ten byte-identical engines at 0% to 100% reach, and showed
that redeploying in place redraws the rate. So a bad engine is both detectable
and fixable, and accepting one silently costs every downstream eval about a
third of its cases.

The gate is the cheapest large win available to this repo: ~60 one-line
requests after a deploy, against a coverage difference of roughly 69% to 99%.
"""

import pytest

from wrangler.tools import boot_probe


class TestGateDecision:
    """The threshold sits in the gap Campaign 01 measured.

    Six engines came in at 97-100%, two at 35-55%, two at 0-6%. Nothing landed
    between 56% and 97%, so a threshold of 0.8 separates the populations with
    room on both sides rather than splitting a continuum.
    """

    def test_a_healthy_engine_passes(self):
        assert boot_probe.gate_decision(reached=59, n=60, threshold=0.8)["passed"] is True

    def test_a_dead_engine_fails(self):
        assert boot_probe.gate_decision(reached=0, n=60, threshold=0.8)["passed"] is False

    def test_a_mid_range_engine_fails(self):
        """55% was a real observed engine. It must not pass."""
        assert boot_probe.gate_decision(reached=33, n=60, threshold=0.8)["passed"] is False

    def test_the_decision_uses_the_point_estimate_not_the_interval(self):
        """A lower bound below threshold on a genuinely good engine must not fail it.

        At n=60 and 90% reach the Wilson lower bound is ~0.80, so gating on the
        interval would reject healthy engines about as often as bad ones.
        """
        out = boot_probe.gate_decision(reached=54, n=60, threshold=0.8)
        assert out["rate"] == pytest.approx(0.9)
        assert out["passed"] is True
        assert out["ci_low"] < 0.9

    def test_exactly_at_the_threshold_passes(self):
        assert boot_probe.gate_decision(reached=48, n=60, threshold=0.8)["passed"] is True

    def test_no_attempts_cannot_pass(self):
        """No data is not a pass. The gate exists to catch silence."""
        assert boot_probe.gate_decision(reached=0, n=0, threshold=0.8)["passed"] is False

    def test_the_decision_carries_its_evidence(self):
        out = boot_probe.gate_decision(reached=59, n=60, threshold=0.8)
        for key in ("reached", "n", "rate", "ci_low", "ci_high", "threshold", "passed"):
            assert key in out, key


class TestGateReport:
    def test_a_failure_says_what_to_do(self):
        """Phase B showed redeploying redraws the rate; the message should say so."""
        lines = boot_probe.gate_report("eng1", boot_probe.gate_decision(0, 60, 0.8))
        text = " ".join(lines).lower()
        assert "eng1" in text
        assert "redeploy" in text

    def test_a_pass_is_quiet_but_states_the_rate(self):
        lines = boot_probe.gate_report("eng1", boot_probe.gate_decision(59, 60, 0.8))
        text = " ".join(lines)
        assert "98" in text or "0.98" in text
        assert "redeploy" not in text.lower()


class TestQuietMode:
    """gate_engine_health renders the verdict itself, so probe_engine must not.

    Without this every health line printed twice in the deploy output.
    """

    def test_probe_engine_accepts_quiet(self):
        import inspect

        from wrangler.tools.boot_probe import probe_engine

        assert "quiet" in inspect.signature(probe_engine).parameters

    def test_the_deploy_gate_probe_is_quiet(self):
        from pathlib import Path

        src = Path("wrangler/orchestration/stages.py").read_text()
        i = src.index("def _default_probe")
        assert "quiet=True" in src[i : i + 400]

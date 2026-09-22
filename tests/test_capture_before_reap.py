"""An engine's responses must outlive the engine.

Campaign 09's three engines were reaped on 2026-09-21 — correctly, by policy, labelled
`lifecycle: ephemeral`, with the project at 80 engines. On **2026-09-22** its control arm's
drift turned out to be a factor of **44** larger between eval sides than between two inference
passes minutes apart, which is a state change rather than noise. The measurement that would
have said whether the change was permanent or transient — a fresh capture from the same engine
— was impossible.

**That ordering is the general case.** Engines are reaped on a schedule; anomalies are
investigated afterwards, because a write-up is published before anyone knows which number will
turn out to be interesting.

The load-bearing test in this file is
`test_a_failed_capture_cancels_that_engines_deletion`. Everything else is detail: losing the
engine *and* its responses is the exact failure being prevented, so when the evidence cannot
be secured the engine has to survive.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from wrangler.tools import forensics as fx
from wrangler.tools.engines import execute_prune


def _row(eid="eng-1", campaign="09", name="gepa-c09-control"):
    labels = {"solution": "promp-wrangler", "lifecycle": "ephemeral"}
    if campaign:
        labels["campaign"] = campaign
    return {"id": eid, "display_name": name, "labels": labels, "model": "claude-sonnet-5"}


class TestOnlyCampaignEnginesAreWorthFiveMinutes:
    def test_a_campaign_engine_is_captured(self):
        assert fx.needs_forensics(_row()) is True

    def test_a_scratch_engine_is_not(self):
        """Routine pruning stays fast. The project reached 80 engines; capturing all of them
        would make a sweep take hours and nobody would run it."""
        assert fx.needs_forensics(_row(campaign="")) is False

    def test_an_engine_with_no_labels_at_all_is_not(self):
        assert fx.needs_forensics({"id": "x"}) is False

    def test_the_path_carries_the_engine_id_not_just_the_name(self, tmp_path):
        """Display names repeat across redeploys; ids do not, and the id is what a stage
        artifact records."""
        path = fx.forensic_path(_row(eid="4023875557346246656"), tmp_path)

        assert "4023875557346246656" in path.name
        assert "gepa-c09-control" in path.name


class TestCapturingAnEngine:
    def _frame(self):
        import pandas as pd
        from agentplatform import types

        session = types.evals.SessionInput(user_id="wrangler-eval", state={})
        return pd.DataFrame(
            [
                {
                    "prompt": f"case {i}",
                    "session_inputs": session,
                    "response": f"resp {i}",
                    "agent_data": {"turns": []},
                }
                for i in range(4)
            ]
        )

    def test_it_freezes_a_durable_canary(self, tmp_path):
        """Frozen as a canary rather than left as the capture pickle: `save_capture` calls
        its own output "scratch, not archive -- an SDK bump can render an old one
        unloadable", and the whole point is to re-score these long after the engine is gone.
        """
        with mock.patch("wrangler.core.converter.load_eval_file", return_value=[{"prompt": "p"}]):
            record = fx.capture_engine(
                _row(),
                eval_data="eval.yaml",
                out_dir=tmp_path,
                capture_fn=lambda **_: "cap.pkl",
                load_fn=lambda _p: self._frame(),
            )

        assert record["error"] == ""
        assert record["rows"] == 4
        payload = json.loads(Path(record["path"]).read_text())
        assert payload["engine_id"] == "eng-1"
        assert len(payload["cases"]) == 4

    def test_provenance_ties_the_capture_to_its_campaign(self, tmp_path):
        with mock.patch("wrangler.core.converter.load_eval_file", return_value=[{"prompt": "p"}]):
            record = fx.capture_engine(
                _row(campaign="09"),
                eval_data="eval.yaml",
                out_dir=tmp_path,
                capture_fn=lambda **_: "cap.pkl",
                load_fn=lambda _p: self._frame(),
            )

        assert record["campaign"] == "09"
        assert record["engine_id"] == "eng-1"
        assert record["captured_at"]

    def test_a_failure_is_returned_not_raised(self, tmp_path):
        """An exception here would abort a batch prune partway, with some engines gone and
        no record of which."""
        with mock.patch("wrangler.core.converter.load_eval_file", return_value=[{"prompt": "p"}]):
            record = fx.capture_engine(
                _row(),
                eval_data="eval.yaml",
                out_dir=tmp_path,
                capture_fn=mock.Mock(side_effect=RuntimeError("engine unreachable")),
            )

        assert "RuntimeError" in record["error"]
        assert record["path"] == ""


class TestAFailedCaptureCancelsDeletion:
    """**The load-bearing behaviour.** Losing the engine *and* its responses is the failure
    this whole feature exists to prevent, so when the evidence cannot be secured the engine
    survives to be pruned again — or deliberately with `--no-capture`, which is a decision
    someone makes rather than a silent loss."""

    def test_a_failed_capture_cancels_that_engines_deletion(self):
        plan = {"delete": [_row("eng-1")]}
        deleted = []

        result = execute_prune(
            plan,
            delete_fn=deleted.append,
            confirm=True,
            pause=0,
            capture_fn=lambda _r: {"engine_id": "eng-1", "error": "boom", "path": "", "rows": 0},
        )

        assert deleted == [], "the engine must survive when its evidence could not be secured"
        assert "eng-1" in result["failed"]
        assert "not deleted" in result["failed"]["eng-1"]

    def test_a_successful_capture_lets_the_deletion_proceed(self):
        plan = {"delete": [_row("eng-1")]}
        deleted = []

        result = execute_prune(
            plan,
            delete_fn=deleted.append,
            confirm=True,
            pause=0,
            capture_fn=lambda _r: {"engine_id": "eng-1", "error": "", "path": "p.json", "rows": 64},
        )

        assert deleted == ["eng-1"]
        assert result["failed"] == {}
        assert result["captured"][0]["rows"] == 64

    def test_one_engines_failure_does_not_block_the_others(self):
        """Partial progress stays legible — the existing contract for delete failures, and it
        has to hold for capture failures too."""
        plan = {"delete": [_row("bad"), _row("good")]}
        deleted = []

        def capture(row):
            return {
                "engine_id": row["id"],
                "error": "boom" if row["id"] == "bad" else "",
                "path": "",
                "rows": 0,
            }

        result = execute_prune(
            plan, delete_fn=deleted.append, confirm=True, pause=0, capture_fn=capture
        )

        assert deleted == ["good"]
        assert list(result["failed"]) == ["bad"]

    def test_returning_none_skips_capture_without_blocking(self):
        """How a non-campaign engine passes through: no capture, no obstruction."""
        plan = {"delete": [_row("eng-1", campaign="")]}
        deleted = []

        result = execute_prune(
            plan, delete_fn=deleted.append, confirm=True, pause=0, capture_fn=lambda _r: None
        )

        assert deleted == ["eng-1"]
        assert result["captured"] == []

    def test_no_capture_fn_is_the_old_behaviour_exactly(self):
        """`--no-capture`, and every existing caller."""
        plan = {"delete": [_row("eng-1")]}
        deleted = []

        result = execute_prune(plan, delete_fn=deleted.append, confirm=True, pause=0)

        assert deleted == ["eng-1"]
        assert result["captured"] == []

    def test_a_dry_run_captures_nothing(self):
        """Dry run must stay free — it is the default, and five minutes per engine would
        make `prune` something nobody runs speculatively."""
        captured = []
        plan = {"delete": [_row("eng-1")]}

        result = execute_prune(
            plan,
            delete_fn=lambda _e: pytest.fail("dry run deleted an engine"),
            confirm=False,
            capture_fn=captured.append,
        )

        assert captured == []
        assert result["dry_run"] is True


class TestThePruneOutputSaysWhatWasSecured:
    """The operator's only evidence that forensics happened. A prune that captured nothing
    and a prune that captured everything must not look the same on the terminal."""

    def test_nothing_captured_prints_nothing(self):
        assert fx.summarise([]) == []

    def test_a_success_names_the_file_and_the_case_count(self):
        lines = fx.summarise([{"engine_id": "eng-1", "error": "", "path": "o/f.json", "rows": 64}])

        text = "\n".join(lines)
        assert "eng-1" in text
        assert "64 cases" in text
        assert "o/f.json" in text

    def test_a_failure_is_marked_and_carries_the_reason(self):
        """This engine was NOT deleted, so the line has to be findable in a long log."""
        lines = fx.summarise(
            [{"engine_id": "eng-2", "error": "RuntimeError: unreachable", "path": "", "rows": 0}]
        )

        text = "\n".join(lines)
        assert "FAILED" in text
        assert "unreachable" in text

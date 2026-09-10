"""A resubmission must not destroy the run before it.

Silent failure #14. `run_id` is deliberately identical across resubmissions so KFP can
cache, but the artifact prefix is keyed on the same value, so the second run writes over
the first. Campaign 07 lost its validation arm that way, and the loss is silent: the
pipeline succeeds, the writes succeed, and the surviving result set looks complete.

Hermetic -- a fake bucket, no network, no GCP. The suite's whole value is that it stays
that way.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wrangler.pipeline.artifact_snapshot import snapshot_prior_run


class _Blob:
    def __init__(self, name: str, updated: datetime | None = None):
        self.name = name
        self.updated = updated


class _Bucket:
    """Enough of `storage.Bucket` for the copy path, and it records what happened."""

    name = "test-bucket"

    def __init__(self, names: dict[str, datetime | None] | None = None):
        self._blobs = {n: _Blob(n, u) for n, u in (names or {}).items()}
        self.copies: list[tuple[str, str]] = []

    def list_blobs(self, prefix: str):
        return [b for n, b in sorted(self._blobs.items()) if n.startswith(prefix)]

    def copy_blob(self, blob, dest_bucket, new_name):
        assert dest_bucket is self
        self.copies.append((blob.name, new_name))
        self._blobs[new_name] = _Blob(new_name, blob.updated)


T = datetime(2026, 9, 9, 17, 52, 9, tzinfo=UTC)


def _run(extra: dict | None = None) -> _Bucket:
    names = {
        "pipeline-runs/run-abc/stages/eval_before/c07-pro.json": T,
        "pipeline-runs/run-abc/stages/optimize/c07-pro.json": T,
        "pipeline-runs/run-abc/reports/experiment_report.md": T,
        "pipeline-runs/run-abc/reports/charts/radar.png": T,
    }
    names.update(extra or {})
    return _Bucket(names)


def test_first_submission_is_a_no_op():
    """The common case must stay free -- no listing cost turning into copy cost."""
    bucket = _Bucket()
    assert snapshot_prior_run(bucket, "run-abc") is None
    assert bucket.copies == []


def test_resubmission_preserves_every_prior_artifact():
    bucket = _run()
    dest = snapshot_prior_run(bucket, "run-abc")
    assert dest == "pipeline-runs/archive/run-abc/20260909T175209Z"
    copied = {new for _, new in bucket.copies}
    assert copied == {
        f"{dest}/stages/eval_before/c07-pro.json",
        f"{dest}/stages/optimize/c07-pro.json",
        f"{dest}/reports/experiment_report.md",
        f"{dest}/reports/charts/radar.png",
    }


def test_the_relative_layout_is_preserved():
    """An archived run must be readable the same way a live one is.

    `campaign_floor.fetch_arms` builds `pipeline-runs/{run_id}/stages/...`, so passing
    `archive/run-abc/<stamp>` as the run id resolves only if the tree under the snapshot
    is shaped exactly like the original.
    """
    bucket = _run()
    dest = snapshot_prior_run(bucket, "run-abc")
    archived_id = dest.removeprefix("pipeline-runs/")
    reconstructed = f"pipeline-runs/{archived_id}/stages/eval_before/c07-pro.json"
    assert reconstructed in {new for _, new in bucket.copies}


def test_inputs_are_not_copied():
    """code.tar.gz is megabytes, regenerated every submission, and worthless afterwards."""
    bucket = _run(
        {
            "pipeline-runs/run-abc/code.tar.gz": T,
            "pipeline-runs/run-abc/manifest.json": T,
        }
    )
    snapshot_prior_run(bucket, "run-abc")
    assert not any("code.tar.gz" in new or "manifest.json" in new for _, new in bucket.copies)


def test_snapshot_is_named_for_the_prior_run_not_for_now():
    """The useful fact is which run this was, not when someone next pressed submit."""
    bucket = _run()
    assert snapshot_prior_run(bucket, "run-abc").endswith("20260909T175209Z")


def test_newest_write_wins_the_stamp():
    later = datetime(2026, 9, 10, 3, 17, 49, tzinfo=UTC)
    bucket = _run({"pipeline-runs/run-abc/reports/summary.json": later})
    assert snapshot_prior_run(bucket, "run-abc").endswith("20260910T031749Z")


def test_it_is_idempotent():
    """A retried submit must not double-copy, nor fail because the snapshot exists."""
    bucket = _run()
    first = snapshot_prior_run(bucket, "run-abc")
    n_after_first = len(bucket.copies)
    second = snapshot_prior_run(bucket, "run-abc")
    assert second == first
    assert len(bucket.copies) == n_after_first, "second call copied again"


def test_a_failed_copy_raises_rather_than_proceeding_to_overwrite():
    """Continuing past a failed archive reproduces exactly the bug being fixed.

    A blocked launch is recoverable; destroyed measurements are not.
    """
    bucket = _run()

    def boom(*_a, **_k):
        raise RuntimeError("GCS unavailable")

    bucket.copy_blob = boom
    with pytest.raises(RuntimeError, match="GCS unavailable"):
        snapshot_prior_run(bucket, "run-abc")


def test_blobs_without_an_updated_timestamp_still_snapshot():
    """Emulators and some list paths omit `updated`; falling over there loses the data."""
    bucket = _Bucket({"pipeline-runs/run-abc/stages/eval_after/x.json": None})
    dest = snapshot_prior_run(bucket, "run-abc")
    assert dest is not None
    assert len(bucket.copies) == 1


class TestItRunsBeforeAnythingWrites:
    """Ordering is the whole guarantee.

    Snapshotting *after* the upload preserves nothing -- the tarball and manifest have
    already landed, and the stage components overwrite as they run. A correct
    implementation called in the wrong place is indistinguishable from no fix.

    Checked against the source rather than by calling `deploy_pipeline`, which builds a
    container image and talks to GCS. `tests/test_pipeline.py` reads this same file the
    same way and for the same reason.
    """

    @staticmethod
    def _first_call_line(fn_name: str) -> int:
        import ast
        from pathlib import Path

        tree = ast.parse(Path("wrangler/pipeline/deploy_pipeline.py").read_text())
        target = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "deploy_pipeline"
        )
        lines = [
            n.lineno
            for n in ast.walk(target)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == fn_name
        ]
        assert lines, f"deploy_pipeline never calls {fn_name}()"
        return min(lines)

    def test_snapshot_precedes_the_code_upload(self):
        assert self._first_call_line("snapshot_prior_run") < self._first_call_line(
            "package_and_upload_code"
        ), (
            "snapshot_prior_run() must run before package_and_upload_code(); archiving "
            "after the write preserves nothing."
        )

    def test_snapshot_precedes_the_manifest_upload(self):
        import ast
        from pathlib import Path

        src = Path("wrangler/pipeline/deploy_pipeline.py").read_text()
        tree = ast.parse(src)
        target = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "deploy_pipeline"
        )
        manifest_writes = [
            n.lineno
            for n in ast.walk(target)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr.startswith("upload_from_")
        ]
        assert manifest_writes, "expected deploy_pipeline to upload something"
        assert self._first_call_line("snapshot_prior_run") < min(manifest_writes)

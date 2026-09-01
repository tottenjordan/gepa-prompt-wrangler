"""Launch campaign arms two at a time, one Anthropic and one Gemini.

Anthropic and Google are **separate Vertex publisher quota pools**, so a Claude
arm and a Gemini arm can run concurrently without contending for model quota --
which is the reason `parallelism=1` exists inside a single pipeline. Two
pipelines at once roughly doubles throughput for free, where two *same-publisher*
pipelines would just race each other into 429s.

One thing the pairing does not decouple: **the GEPA judge is `gemini-3.5-flash`
on both arms**, so the optimize phases still share Gemini quota even when the
agent models do not. The fix is to stagger the starts, not to vary the judge --
the judge was chosen by an A/B and changing it per-arm would confound every
comparison the campaign exists to make.

Usage:
    uv run python scripts/run_campaign.py --campaign 06        # dry run
    uv run python scripts/run_campaign.py --campaign 06 --yes
    uv run python scripts/run_campaign.py --campaign 07 --yes
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_EXAMPLE_ENV = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / ".env"
if _EXAMPLE_ENV.exists():
    load_dotenv(str(_EXAMPLE_ENV), override=True)

from wrangler.core.factory import PairFactory  # noqa: E402
from wrangler.core.models import get_spec  # noqa: E402

# Seconds between the two arms of a batch. The shared Gemini judge means their
# optimize phases would otherwise collide; offsetting the starts keeps them out
# of step. Eval-only campaigns do not need it, so they pass 0.
OPTIMIZE_STAGGER = 90 * 60

CAMPAIGNS: dict[str, list[tuple[str, str]]] = {
    # Campaign 06 -- eval-only control arms. Paired by publisher at each
    # num_runs level. No optimize phase, so no stagger needed.
    # n=1 and n=3 only. The n=5 batch is deliberately absent: restoring the
    # second eval (a floor needs two evals of an unchanged prompt) doubled every
    # arm, and num_runs multiplies on top -- n=5 means ten eval passes per arm,
    # about six hours, which took the campaign from ~5h to ~12h. n=3 is the
    # figure CLAUDE.md actually cites and the one campaign 07 needs; the third
    # point on the sqrt(n) curve can be extrapolated. The manifests are kept so
    # re-adding the batch is one line.
    "06": [
        (
            "manifests/c06-ctrl-claude-n1_manifest.yaml",
            "manifests/c06-ctrl-gemini-n1_manifest.yaml",
        ),
        (
            "manifests/c06-ctrl-claude-n3_manifest.yaml",
            "manifests/c06-ctrl-gemini-n3_manifest.yaml",
        ),
    ],
    # Campaign 07 -- cost/quality frontier. Cost tier is crossed with batch
    # rather than confounded with it: each batch holds one cheap and one dear
    # arm, so an unlucky batch does not land entirely on one end of the range.
    "07": [
        ("manifests/c07-sonnet5_manifest.yaml", "manifests/c07-pro_manifest.yaml"),
        ("manifests/c07-sonnet46_manifest.yaml", "manifests/c07-lite_manifest.yaml"),
    ],
}


def _publisher(manifest_path: str) -> str:
    m = PairFactory.load(manifest_path)
    return get_spec(m.pairs[0].model).provider


def _needs_stagger(manifest_path: str) -> bool:
    return not (PairFactory.load(manifest_path).pipeline or {}).get("skip_optimize", False)


def validate(batches: list[tuple[str, str]]) -> list[str]:
    """Check every batch straddles both publishers before anything is submitted."""
    problems = []
    for i, (a, b) in enumerate(batches, 1):
        missing = [path for path in (a, b) if not Path(path).is_file()]
        problems.extend(f"batch {i}: missing manifest {path}" for path in missing)
        if missing:
            continue
        pa, pb = _publisher(a), _publisher(b)
        if pa == pb:
            problems.append(
                f"batch {i}: both arms are {pa} — they would contend on the same "
                f"quota pool, which is the whole thing this pairing avoids"
            )
    return problems


# Terminal PipelineJob states. Anything else means still working.
_DONE = ("SUCCEEDED", "FAILED", "CANCELLED")

# Vertex bills by the minute, not the poll, so this only needs to be often
# enough to keep the next batch moving.
POLL_SECONDS = 120


def submit(manifest_path: str, log_dir: Path) -> str:
    """Submit one arm and return its Vertex job id.

    Calls ``deploy_pipeline`` in process rather than shelling out to the CLI:
    the job id has to come back so the batch can be waited on, and scraping it
    out of stdout would be one string-format change away from silently
    returning nothing.
    """
    from wrangler.pipeline.deploy_pipeline import deploy_pipeline

    log_dir.mkdir(parents=True, exist_ok=True)
    result = deploy_pipeline(manifest_path=manifest_path)
    job_id = result["job_id"]
    print(f"    submitted {Path(manifest_path).name} -> job {job_id}", flush=True)
    print(f"      {result['dashboard_uri']}", flush=True)
    (log_dir / f"{Path(manifest_path).stem}.job").write_text(job_id + "\n")
    return job_id


def wait_for_jobs(job_ids: list[str], sleep_fn=time.sleep, state_fn=None) -> dict[str, str]:
    """Block until every job reaches a terminal state. Returns id -> final state.

    This is the whole reason the runner exists. ``job.submit()`` is
    non-blocking, so waiting on the *submission* -- which is what an earlier
    version did -- waits about ninety seconds and then launches the next batch
    on top of the last. Six arms would have run at once, three per publisher,
    which is exactly the contention the pairing is meant to avoid.
    """
    state_of = state_fn or _job_state
    pending = list(job_ids)
    final: dict[str, str] = {}
    while pending:
        for job_id in list(pending):
            state = state_of(job_id)
            if any(t in state for t in _DONE):
                final[job_id] = state
                pending.remove(job_id)
                print(f"    job {job_id}: {state}", flush=True)
        if pending:
            sleep_fn(POLL_SECONDS)
    return final


def state_name(state) -> str:
    """Normalise a PipelineState to its enum *name*.

    `str()` on the proto-plus enum yields the bare ordinal -- a FAILED job
    stringifies to `"5"`, not `"PIPELINE_STATE_FAILED"`. Matching `_DONE`
    against that never hits, so `wait_for_jobs` polled a finished job forever:
    the campaign gate held (it never released) but it also never returned.
    An overnight chain would have stalled after batch 1 on *any* outcome,
    success included.
    """
    name = getattr(state, "name", None)
    if isinstance(name, str) and name:
        return name
    try:
        return _PIPELINE_STATES[int(state)]
    except (ValueError, TypeError, KeyError, IndexError):
        return str(state)


# Ordinals of google.cloud.aiplatform_v1.PipelineState, so a numeric state is
# still readable if the enum object is not available.
_PIPELINE_STATES = {
    0: "PIPELINE_STATE_UNSPECIFIED",
    1: "PIPELINE_STATE_QUEUED",
    2: "PIPELINE_STATE_PENDING",
    3: "PIPELINE_STATE_RUNNING",
    4: "PIPELINE_STATE_SUCCEEDED",
    5: "PIPELINE_STATE_FAILED",
    6: "PIPELINE_STATE_CANCELLING",
    7: "PIPELINE_STATE_CANCELLED",
    8: "PIPELINE_STATE_PAUSED",
}


def _job_state(job_id: str) -> str:
    from google.cloud import aiplatform

    from wrangler.core.config import GCP_PROJECT_ID, GCP_REGION

    name = f"projects/{GCP_PROJECT_ID}/locations/{GCP_REGION}/pipelineJobs/{job_id}"
    try:
        return state_name(aiplatform.PipelineJob.get(resource_name=name).state)
    except Exception as exc:
        # A lookup failure must not be read as "finished" -- that would release
        # the next batch on top of a still-running one.
        print(f"    job {job_id}: state unreadable ({type(exc).__name__}), still waiting")
        return "UNKNOWN"


def run_campaign(campaign: str, confirm: bool, log_dir: Path, sleep_fn=time.sleep) -> int:
    batches = CAMPAIGNS[campaign]
    problems = validate(batches)

    print(f"=== Campaign {campaign}: {len(batches)} batch(es), 2 arms each ===")
    for i, (a, b) in enumerate(batches, 1):
        try:
            tag = f"{_publisher(a)} ‖ {_publisher(b)}"
        except Exception as exc:  # missing/broken manifest
            tag = f"unreadable: {type(exc).__name__}"
        print(f"  batch {i}: {Path(a).name}  ‖  {Path(b).name}   [{tag}]")

    if problems:
        print("\nREFUSING TO RUN:")
        for p in problems:
            print(f"  - {p}")
        return 1

    if not confirm:
        print("\nDRY RUN — re-run with --yes to submit.")
        return 0

    for i, (a, b) in enumerate(batches, 1):
        print(f"\n--- batch {i} of {len(batches)} ---", flush=True)
        jobs = [submit(a, log_dir)]
        if _needs_stagger(a) or _needs_stagger(b):
            mins = OPTIMIZE_STAGGER // 60
            print(f"    staggering {mins} min — both arms judge with gemini-3.5-flash", flush=True)
            sleep_fn(OPTIMIZE_STAGGER)
        jobs.append(submit(b, log_dir))

        print(f"    waiting for {len(jobs)} job(s) to finish before the next batch", flush=True)
        final = wait_for_jobs(jobs, sleep_fn=sleep_fn)
        failed = [j for j, st in final.items() if "SUCCEEDED" not in st]
        if failed:
            # Report and continue: a later batch may still be worth having, and
            # a half-finished campaign that says which half is better than one
            # that stops silently.
            print(f"    batch {i} had {len(failed)} non-successful job(s): {failed}", flush=True)
        print(f"--- batch {i} complete ---", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a DOE campaign as paired pipelines")
    parser.add_argument("--campaign", required=True, choices=sorted(CAMPAIGNS))
    parser.add_argument("--yes", action="store_true", help="Actually submit. Dry run without it.")
    parser.add_argument("--log-dir", default="outputs/campaigns")
    args = parser.parse_args()
    sys.exit(run_campaign(args.campaign, args.yes, Path(args.log_dir)))

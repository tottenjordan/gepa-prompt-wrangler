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
    uv run python scripts/run_campaign.py --campaign 08 --yes --resume

**A driver that dies is no longer a lost campaign.** It runs for 22-44 hours and
used to hold its position only in memory, so losing the process lost the run even
though every job was on Vertex and unaffected. `--resume` rebuilds the position
from the `.job` files in `--log-dir` and the states Vertex still answers for:
finished batches are skipped, a batch still running is adopted rather than
resubmitted. The driver's own transcript is appended to `<log-dir>/campaign-NN.log`
so it survives too -- the 2026-09-10 log went to /tmp and died with the process.

No redirection needed, and do not resume into a *different* campaign's log dir:
`.job` files are keyed by manifest stem, not by run.
"""

from __future__ import annotations

import argparse
import contextlib
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
#
# 40, not 90. Two reasons, and the second is the binding one.
#
# It never did what 90 implies: campaign 07's optimize ran 577 minutes, so a
# 90-minute offset left two arms overlapping for ~8 of 9.6 hours. It
# de-conflicted the first 16% and then they contended anyway -- 76 x HTTP 429
# arrived *with* the stagger in place. See docs/notes/optimize-stagger.md.
#
# And a 90-minute sleep outlives an ADC access token. On 2026-09-09 the driver
# slept through the expiry and its next submit died on
# "Reauthentication is needed", killing the run between two arms of one batch.
# 40 minutes keeps both submissions inside one token lifetime.
#
# This is a mitigation, not a fix. A driver that sleeps for hours will
# eventually straddle a reauth however short each individual sleep is. Both
# halves of the real answer are now here: `ensure_credentials()` runs before
# every submit, and `--resume` means a driver that dies is restartable rather
# than a lost campaign.
OPTIMIZE_STAGGER = 40 * 60

CAMPAIGNS: dict[str, list[tuple[str, ...]]] = {
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
    #
    # Each batch also carries a CONTROL arm -- same seed, no optimize stage --
    # running alongside under the same load. CLAUDE.md requires one in every
    # optimization sweep and forbids reusing an earlier campaign's floor,
    # because "the dropout that generates the noise varies with load and with
    # how many arms run at once". Campaign 06's floor was measured on four
    # engines that each drew a perfect health gate, a ~9% event, so it is
    # optimistic and does not transfer.
    #
    # The control matches a model already in its batch, so its floor calibrates
    # that batch directly, and the two controls between them cover both
    # publishers.
    "07": [
        (
            "manifests/c07-sonnet5_manifest.yaml",
            "manifests/c07-pro_manifest.yaml",
            "manifests/c07-ctrl-sonnet5_manifest.yaml",
        ),
        (
            "manifests/c07-sonnet46_manifest.yaml",
            "manifests/c07-lite_manifest.yaml",
            "manifests/c07-ctrl-lite_manifest.yaml",
        ),
    ],
    # Campaign 08 -- did PR #69's criteria change remove the instruction-following
    # regression campaign 07 reproduced on two model families?
    #
    # One model (claude-sonnet-5), two criteria conditions, two repeats each. The
    # conditions differ only in sampler_config.json, which is selected by agent-module
    # name -- hence the sonnet_baseline_agent twin.
    #
    # Condition is crossed with batch, not confounded by it: each batch runs one arm of
    # each condition, so an unlucky batch cannot land entirely on one condition. Same
    # reasoning as campaign 07's cost tiers.
    #
    # Two repeats because campaign 07 measured run-to-run spread at up to 12.3x the
    # control floor -- two runs of one manifest disagreed on the *sign* of three metrics.
    # A single arm per condition could not attribute anything.
    #
    # A control per batch, never reused across batches: CLAUDE.md forbids carrying a
    # floor over, since the dropout that generates it varies with load.
    # ONE optimizing arm per batch, not two. Both conditions run claude-sonnet-5 --
    # that is the point, the model is held constant so criteria is the only variable --
    # so pairing them in a batch would put two Anthropic optimize phases on one quota
    # pool. `validate()` rejects exactly that, and it caught this design before it ran.
    #
    # Campaign 07 could pair its arms because it varied the model; campaign 08 cannot,
    # and the honest cost is four sequential optimize phases (~44h) instead of two.
    #
    # Each batch still carries its own control. A control has no optimize stage, so it
    # shares the publisher for free and adds no wall clock -- and CLAUDE.md forbids
    # reusing a floor measured under different load, so each batch measures its own.
    #
    # Conditions alternate across batches so a drift in the substrate over two days
    # cannot land entirely on one condition.
    "08": [
        ("manifests/c08-new-r1_manifest.yaml", "manifests/c08-ctrl-a_manifest.yaml"),
        ("manifests/c08-old-r1_manifest.yaml", "manifests/c08-ctrl-b_manifest.yaml"),
        ("manifests/c08-new-r2_manifest.yaml", "manifests/c08-ctrl-c_manifest.yaml"),
        ("manifests/c08-old-r2_manifest.yaml", "manifests/c08-ctrl-d_manifest.yaml"),
    ],
}


def _publisher(manifest_path: str) -> str:
    m = PairFactory.load(manifest_path)
    return get_spec(m.pairs[0].model).provider


def _needs_stagger(manifest_path: str) -> bool:
    return not (PairFactory.load(manifest_path).pipeline or {}).get("skip_optimize", False)


def validate(batches: list[tuple[str, ...]]) -> list[str]:
    """No two *optimizing* arms in a batch may share a publisher.

    The pairing exists because Anthropic and Google are separate Vertex quota
    pools, so one Claude and one Gemini arm run concurrently for free where two
    Claude arms race each other into 429s. The contended resource during a
    sweep is the optimize phase -- both arms judge with the same
    gemini-3.5-flash, which is why the starts are staggered.

    A control arm has no optimize stage. It neither runs GEPA nor touches the
    shared judge, so it may share a publisher with an optimizing arm; that is
    what lets a batch carry the control CLAUDE.md requires without breaking the
    pairing. Hence the rule is stated over optimizing arms rather than over
    every arm in the batch.
    """
    problems = []
    for i, batch in enumerate(batches, 1):
        missing = [path for path in batch if not Path(path).is_file()]
        problems.extend(f"batch {i}: missing manifest {path}" for path in missing)
        if missing:
            continue
        optimizing = [m for m in batch if _needs_stagger(m)]
        seen: dict[str, str] = {}
        for manifest in optimizing:
            pub = _publisher(manifest)
            if pub in seen:
                problems.append(
                    f"batch {i}: {Path(seen[pub]).stem} and {Path(manifest).stem} both "
                    f"optimize on {pub} — they would contend on the same quota pool, "
                    f"which is the whole thing this pairing avoids"
                )
            else:
                seen[pub] = manifest
    return problems


# Terminal PipelineJob states. Anything else means still working.
_DONE = ("SUCCEEDED", "FAILED", "CANCELLED")

# Vertex bills by the minute, not the poll, so this only needs to be often
# enough to keep the next batch moving.
POLL_SECONDS = 120


class CredentialsExpiredError(RuntimeError):
    """ADC cannot be renewed without a human. Raised *before* a submission."""


def ensure_credentials(credentials_fn=None) -> str:
    """Refresh ADC and return a one-line note on how long it is good for.

    Called at the top of every ``submit()``. What this does and does not buy is
    worth being precise about, because the obvious reading is wrong.

    It does **not** hand refreshed credentials to the SDK. ``google.auth.default()``
    mints a new credentials object per caller and ``deploy_pipeline`` builds its
    own, so nothing downstream sees the object refreshed here.

    What it buys is that the failure lands *before* the submission instead of
    inside it. The 2026-09-09 death came after a 90-minute sleep, mid-``submit``,
    with one arm of the batch already away — leaving a half-submitted batch and a
    traceback that named an HTTP call rather than the expired session. A check
    here turns that into a named error at a point where nothing has been sent.

    For a service account this is also a genuine renewal and costs one token
    fetch. For user ADC hitting a reauth challenge nothing programmatic can help;
    the message says so, and says what does.
    """
    import google.auth
    from google.auth.transport.requests import Request

    creds, _ = (credentials_fn or google.auth.default)()
    if not creds.valid or getattr(creds, "expired", False):
        try:
            creds.refresh(Request())
        except Exception as exc:  # RefreshError, ReauthFailError, transport errors
            raise CredentialsExpiredError(
                f"ADC could not be refreshed before submitting ({type(exc).__name__}: {exc}). "
                "Nothing was submitted. Run `gcloud auth application-default login`, then "
                "re-run with --resume; a long campaign is better driven under a service "
                "account, which has no reauth challenge to straddle."
            ) from exc
    expiry = getattr(creds, "expiry", None)
    return f"credentials good until {expiry:%H:%M:%S} UTC" if expiry else "credentials refreshed"


def submit(manifest_path: str, log_dir: Path) -> str:
    """Submit one arm and return its Vertex job id.

    Calls ``deploy_pipeline`` in process rather than shelling out to the CLI:
    the job id has to come back so the batch can be waited on, and scraping it
    out of stdout would be one string-format change away from silently
    returning nothing.
    """
    from wrangler.pipeline.deploy_pipeline import deploy_pipeline

    print(f"    {ensure_credentials()}", flush=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    result = deploy_pipeline(manifest_path=manifest_path)
    job_id = result["job_id"]
    print(f"    submitted {Path(manifest_path).name} -> job {job_id}", flush=True)
    print(f"      {result['dashboard_uri']}", flush=True)
    (log_dir / f"{Path(manifest_path).stem}.job").write_text(job_id + "\n")
    return job_id


def job_ids_for(batch: tuple[str, ...], log_dir: Path) -> list[str] | None:
    """The job ids a previous driver recorded for this batch, or None if incomplete.

    ``submit()`` writes one ``<manifest-stem>.job`` per arm as it goes, so a batch
    with a file missing was interrupted part-way through submission and has to be
    re-run rather than adopted.
    """
    ids = []
    for manifest in batch:
        path = log_dir / f"{Path(manifest).stem}.job"
        if not path.is_file():
            return None
        job_id = path.read_text().strip()
        if not job_id:
            return None
        ids.append(job_id)
    return ids


def resume_state(
    batches: list[tuple[str, ...]], log_dir: Path, state_fn=None
) -> tuple[int, list[str]]:
    """Work out where a dead driver got to. Returns (batches to skip, jobs to adopt).

    The driver holds its position in memory across a 22-44 hour run, so losing the
    process loses the campaign even though every job is on Vertex and unaffected.
    This reconstructs the position from the two things that *did* survive: the
    ``.job`` files on disk and the job states Vertex will still answer for.

    Only **leading** batches are skipped. A gap -- batch 1 succeeded, 2 failed,
    3 succeeded -- stops the scan at 2, because resuming past a failed batch would
    quietly drop it while the campaign carried on looking complete. Re-running the
    failed batch is the safe default and KFP caches whatever still applies.

    A batch whose jobs are still running is **adopted**, not resubmitted: that is
    the case this exists for, and resubmitting would double the load on the judge
    the whole design is built around.

    ``log_dir`` is not campaign-scoped, so a ``.job`` file from an earlier run of
    the same manifest reads as this run's. The caller prints each adopted id and
    its file's age for exactly that reason -- this decides, it does not confirm.
    """
    state_of = state_fn or _job_state
    skip = 0
    for batch in batches:
        ids = job_ids_for(batch, log_dir)
        if ids is None:
            break
        states = {job_id: state_of(job_id) for job_id in ids}
        if all("SUCCEEDED" in st for st in states.values()):
            skip += 1
            continue
        live = [j for j, st in states.items() if not any(t in st for t in _DONE)]
        # Adopt only if nothing in the batch has already failed -- a mixed batch
        # needs re-running as a whole, and waiting on its survivors would hide that.
        return (skip, live) if live and len(live) == len(ids) else (skip, [])
    return skip, []


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


def run_campaign(
    campaign: str,
    confirm: bool,
    log_dir: Path,
    sleep_fn=time.sleep,
    resume: bool = False,
    state_fn=None,
) -> int:
    batches = CAMPAIGNS[campaign]
    problems = validate(batches)

    print(f"=== Campaign {campaign}: {len(batches)} batch(es) ===")
    for i, batch in enumerate(batches, 1):
        parts = []
        for manifest in batch:
            try:
                mark = "" if _needs_stagger(manifest) else " (control)"
                parts.append(f"{Path(manifest).stem}{mark} [{_publisher(manifest)}]")
            except Exception as exc:  # missing/broken manifest
                parts.append(f"{Path(manifest).stem} [unreadable: {type(exc).__name__}]")
        print(f"  batch {i}: " + "  ‖  ".join(parts))

    if problems:
        print("\nREFUSING TO RUN:")
        for p in problems:
            print(f"  - {p}")
        return 1

    if not confirm:
        print("\nDRY RUN — re-run with --yes to submit.")
        return 0

    skip, adopted = resume_state(batches, log_dir, state_fn) if resume else (0, [])
    if skip or adopted:
        print(f"\nRESUMING: {skip} batch(es) already succeeded", flush=True)
        for job_id in adopted:
            print(f"  adopting running job {job_id}", flush=True)
        for manifest in batches[skip] if adopted else ():
            path = log_dir / f"{Path(manifest).stem}.job"
            age_h = (time.time() - path.stat().st_mtime) / 3600
            print(f"    {path.name} submitted {age_h:.1f} h ago", flush=True)
    if adopted:
        print(f"    waiting for {len(adopted)} adopted job(s)", flush=True)
        final = wait_for_jobs(adopted, sleep_fn=sleep_fn, state_fn=state_fn)
        failed = [j for j, st in final.items() if "SUCCEEDED" not in st]
        if failed:
            print(f"    adopted batch had {len(failed)} non-successful job(s): {failed}")
        skip += 1

    for i, batch in enumerate(batches, 1):
        if i <= skip:
            print(f"\n--- batch {i} of {len(batches)}: already done, skipping ---", flush=True)
            continue
        print(f"\n--- batch {i} of {len(batches)} ---", flush=True)
        # Optimizing arms are staggered from each other because they share the
        # gemini-3.5-flash judge. A control has no optimize stage, so it goes
        # out immediately alongside them -- which is the point: it has to run
        # under the same load as the arms it calibrates, not after them.
        jobs = []
        optimizing_submitted = 0
        for manifest in batch:
            if _needs_stagger(manifest):
                if optimizing_submitted:
                    mins = OPTIMIZE_STAGGER // 60
                    print(
                        f"    staggering {mins} min — optimizing arms share the "
                        f"gemini-3.5-flash judge",
                        flush=True,
                    )
                    sleep_fn(OPTIMIZE_STAGGER)
                optimizing_submitted += 1
            jobs.append(submit(manifest, log_dir))

        print(f"    waiting for {len(jobs)} job(s) to finish before the next batch", flush=True)
        final = wait_for_jobs(jobs, sleep_fn=sleep_fn, state_fn=state_fn)
        failed = [j for j, st in final.items() if "SUCCEEDED" not in st]
        if failed:
            # Report and continue: a later batch may still be worth having, and
            # a half-finished campaign that says which half is better than one
            # that stops silently.
            print(f"    batch {i} had {len(failed)} non-successful job(s): {failed}", flush=True)
        print(f"--- batch {i} complete ---", flush=True)
    return 0


@contextlib.contextmanager
def tee_stdout(path: Path):
    """Duplicate everything printed into ``path`` as well as the terminal.

    The driver is normally launched with its output redirected somewhere ad hoc.
    During the 2026-09-10 run that somewhere was ``/tmp``, the process died, and
    the log went with it -- so the only record of a 22-hour campaign's first half
    was gone, and its absence was noticed by looking rather than by being told.

    Writing into ``--log-dir`` costs nothing, sits beside the ``.job`` files that
    ``--resume`` reads, and is not swept. Appends, so a resumed driver extends the
    record instead of truncating the part that explains why it had to resume.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:

        class _Tee:
            def write(self, text: str) -> int:
                handle.write(text)
                handle.flush()
                return original.write(text)

            def flush(self) -> None:
                handle.flush()
                original.flush()

        original = sys.stdout
        sys.stdout = _Tee()
        try:
            yield
        finally:
            sys.stdout = original


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a DOE campaign as paired pipelines")
    parser.add_argument("--campaign", required=True, choices=sorted(CAMPAIGNS))
    parser.add_argument("--yes", action="store_true", help="Actually submit. Dry run without it.")
    parser.add_argument("--log-dir", default="outputs/campaigns")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip batches Vertex says already succeeded, and adopt one still running.",
    )
    args = parser.parse_args()
    log_dir = Path(args.log_dir)
    with tee_stdout(log_dir / f"campaign-{args.campaign}.log"):
        print(f"\n=== driver started {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
        sys.exit(run_campaign(args.campaign, args.yes, log_dir, resume=args.resume))

"""Submit one validation arm, and release the rest of the campaign only if it works.

Two things in the campaign-06 path have never executed against real Vertex: the
`skip_optimize` branch added to the DAG, and `gate_engine_health` inside the KFP
deploy component. Both compile and pass unit tests; neither has met the service.

Submitting all six arms at once risks six identical failures over five hours.
One cheap arm first — `num_runs: 1`, eval-only — exercises every new code path
for roughly the cost of a single eval, and the rest is released automatically
only if that arm reaches SUCCEEDED.

Usage:
    uv run python scripts/validate_then_run.py --campaign 06
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

from scripts.run_campaign import (  # noqa: E402
    CAMPAIGNS,
    run_campaign,
    submit,
    wait_for_jobs,
)
from wrangler.tools.preflight import render, run_preflight  # noqa: E402

# The cheapest arm that still touches every new path: eval-only (so it runs the
# skip_optimize branch) and num_runs=1 (so it is one eval pass, not five).
VALIDATION_ARM = {
    "06": "manifests/c06-ctrl-claude-n1_manifest.yaml",
    "07": "manifests/c07-sonnet5_manifest.yaml",
    # Batch 1's treatment arm, and it has to be one now that campaign 08 is a screen.
    #
    # It was c08-old-r1, chosen because the baseline condition's sonnet_baseline_agent/_opt
    # directories had never been deployed or optimized. The rescope drops the `old` arms,
    # so that manifest is no longer part of its own campaign -- meaning ~11h of validation
    # would be thrown away instead of hitting the KFP cache when the driver resubmits it.
    # `test_every_validation_arm_is_part_of_its_own_campaign` fails on exactly that, and
    # did.
    #
    # The never-run paths it still covers are the ones that matter: the redeploy health
    # gate from PR #70, which no campaign has executed, and the optimize stage with the
    # per-generation MCP refresh removed (PR #75). A control arm would exercise neither --
    # it skips optimize and never redeploys.
    #
    # That second one is a gate, not a bonus. Watch this arm's log for
    # `will run without the tools`: 0 confirms the silent-failure #12 fix, and anything
    # near campaign 07's 14% or campaign 08's earlier 15% means the refresh was not the
    # cause either, and scripts/repro_mcp_refresh_hang.py is where to resume.
    "08": "manifests/c08-new-r1_manifest.yaml",
}


def _preflight_ok(skip: bool) -> bool:
    """Resolve both dependency sets before committing anything to Vertex.

    Both previous campaign 07 launches died in a GEAP build on a set that
    could not resolve -- ~20 minutes in, three retries, and an error that said
    only "Build failed ... or other dependencies". Every pin test in the suite
    is static and cannot see a transitive conflict. This costs about a second.

    Gated on the --watch-job path too: that branch releases six arms.
    """
    if skip:
        print("PREFLIGHT SKIPPED (--skip-preflight). Both prior c07 launches died")
        print("on a requirements set that did not resolve. You are choosing this.")
        return True

    results = run_preflight()
    for line in render(results):
        print(line)
    if any(not r.ok for r in results):
        print("\nNot submitting. Fix the requirements before spending a campaign on them.")
        return False
    return True


def main(
    campaign: str,
    log_dir: Path,
    watch_job: str = "",
    skip_preflight: bool = False,
    resume: bool = False,
) -> int:
    """Validate, then release. ``watch_job`` adopts a validation arm already running.

    ``resume`` is passed through to the campaign it releases, for the case this
    wrapper cannot cover: a driver that died *after* validation, part-way through
    the batches. ``watch_job`` adopts one arm; ``--resume`` adopts a position.

    The campaign list is read into memory at import, so editing it cannot change
    a chain that is already running. Trimming a batch mid-flight therefore means
    stopping that process and starting this one, which waits on the Vertex job
    the old chain submitted instead of paying for it twice.
    """
    if not _preflight_ok(skip_preflight):
        return 1

    if watch_job:
        print("=" * 70)
        print(f"STEP 1 — adopting the validation arm already running: {watch_job}")
        print("=" * 70)
        final = wait_for_jobs([watch_job])
        state = final.get(watch_job, "UNKNOWN")
        if "SUCCEEDED" not in state:
            print(f"\nValidation arm ended {state}. NOT releasing the campaign.")
            return 1
        print(f"\nValidation arm SUCCEEDED ({watch_job}).")
        print("=" * 70)
        print(f"STEP 2 — releasing campaign {campaign}: {len(CAMPAIGNS[campaign])} batches")
        print("=" * 70)
        return run_campaign(campaign, confirm=True, log_dir=log_dir, resume=resume)

    arm = VALIDATION_ARM[campaign]
    print("=" * 70)
    print(f"STEP 1 — validation arm for campaign {campaign}: {Path(arm).name}")
    print("=" * 70)
    print("Exercising the never-run paths: the skip_optimize dsl.If branch and")
    print("the health gate inside the KFP deploy component.\n")

    try:
        job_id = submit(arm, log_dir)
    except Exception as exc:
        print(f"\nFAILED to submit: {type(exc).__name__}: {exc}")
        print("Not releasing the campaign.")
        return 1

    final = wait_for_jobs([job_id])
    state = final.get(job_id, "UNKNOWN")

    if "SUCCEEDED" not in state:
        print(f"\nValidation arm ended {state}.")
        print("NOT releasing the rest of the campaign — six copies of a broken run")
        print("would cost five hours and teach nothing. Inspect the job, then re-run.")
        return 1

    print(f"\nValidation arm SUCCEEDED ({job_id}).")
    print("=" * 70)
    print(f"STEP 2 — releasing campaign {campaign}: {len(CAMPAIGNS[campaign])} batches")
    print("=" * 70)
    print("The validated arm re-submits with the same inputs, so KFP should cache")
    print("it and move on quickly rather than repeating the work.\n")

    return run_campaign(campaign, confirm=True, log_dir=log_dir, resume=resume)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate one arm, then run the campaign")
    parser.add_argument("--campaign", default="06", choices=sorted(VALIDATION_ARM))
    parser.add_argument("--log-dir", default="outputs/campaigns")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip batches Vertex says already succeeded, and adopt one still running.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Submit without resolving the dependency sets first. Deliberate "
        "override for a PyPI outage; both prior c07 launches died on a set "
        "that did not resolve.",
    )
    parser.add_argument(
        "--watch-job",
        default="",
        help="Adopt a validation arm already running on Vertex instead of submitting "
        "a new one. Used when the campaign list changed mid-flight.",
    )
    args = parser.parse_args()
    started = time.time()
    code = main(
        args.campaign,
        Path(args.log_dir),
        watch_job=args.watch_job,
        skip_preflight=args.skip_preflight,
        resume=args.resume,
    )
    print(f"\nTotal wall clock: {(time.time() - started) / 3600:.1f} h")
    sys.exit(code)

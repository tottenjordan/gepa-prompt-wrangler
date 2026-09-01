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

# The cheapest arm that still touches every new path: eval-only (so it runs the
# skip_optimize branch) and num_runs=1 (so it is one eval pass, not five).
VALIDATION_ARM = {
    "06": "manifests/c06-ctrl-claude-n1_manifest.yaml",
    "07": "manifests/c07-sonnet5_manifest.yaml",
}


def main(campaign: str, log_dir: Path) -> int:
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

    return run_campaign(campaign, confirm=True, log_dir=log_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate one arm, then run the campaign")
    parser.add_argument("--campaign", default="06", choices=sorted(VALIDATION_ARM))
    parser.add_argument("--log-dir", default="outputs/campaigns")
    args = parser.parse_args()
    started = time.time()
    code = main(args.campaign, Path(args.log_dir))
    print(f"\nTotal wall clock: {(time.time() - started) / 3600:.1f} h")
    sys.exit(code)

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
import subprocess
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
    "06": [
        (
            "manifests/c06-ctrl-claude-n1_manifest.yaml",
            "manifests/c06-ctrl-gemini-n1_manifest.yaml",
        ),
        (
            "manifests/c06-ctrl-claude-n3_manifest.yaml",
            "manifests/c06-ctrl-gemini-n3_manifest.yaml",
        ),
        (
            "manifests/c06-ctrl-claude-n5_manifest.yaml",
            "manifests/c06-ctrl-gemini-n5_manifest.yaml",
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


def launch(manifest_path: str, log_dir: Path) -> subprocess.Popen:
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{Path(manifest_path).stem}.log"
    handle = log.open("w")
    proc = subprocess.Popen(
        ["uv", "run", "wrangler", "pipeline", "run", manifest_path],
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    print(f"    submitted {Path(manifest_path).name}  (pid {proc.pid}, log {log})", flush=True)
    return proc


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
        print(f"\n--- batch {i} ---", flush=True)
        first = launch(a, log_dir)
        if _needs_stagger(a) or _needs_stagger(b):
            mins = OPTIMIZE_STAGGER // 60
            print(f"    staggering {mins} min — both arms judge with gemini-3.5-flash", flush=True)
            sleep_fn(OPTIMIZE_STAGGER)
        second = launch(b, log_dir)
        for proc in (first, second):
            proc.wait()
        print(f"--- batch {i} submitted ---", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a DOE campaign as paired pipelines")
    parser.add_argument("--campaign", required=True, choices=sorted(CAMPAIGNS))
    parser.add_argument("--yes", action="store_true", help="Actually submit. Dry run without it.")
    parser.add_argument("--log-dir", default="outputs/campaigns")
    args = parser.parse_args()
    sys.exit(run_campaign(args.campaign, args.yes, Path(args.log_dir)))

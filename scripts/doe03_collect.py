"""DOE 03 collection: N captures from one engine, each scored M times.

Collects **one pool** from which every (`num_runs`, `score_repeats`) cell is rebuilt
offline by `scripts/analyze_doe03.py`. The campaign therefore pays once for data that
answers the whole surface, instead of running each cell live.

    default pool: 6 captures x 5 scorings = 30 scoring passes, ~100 min of compute

Why the pool has these dimensions: a control-arm delta at `num_runs=r` needs two
**disjoint** sets of r captures, so 6 captures reaches r=3, the production default. 5
scorings reaches `score_repeats=5`. Together they span the iso-cost contrasts the campaign
turns on -- (3,1) vs (2,2) vs (1,5) all cost ~16.5 min, and (2,1) vs (1,3) both cost ~11.

**Three things here are load-bearing rather than incidental:**

1. **Captures are interleaved with scoring, not taken back-to-back.** Production
   `eval_before` and `eval_after` are *hours* apart. Six captures drawn inside 15 minutes
   would be more correlated than the thing being modelled and would **understate the
   floor** -- the one direction that produces false wins. Ordering the work
   `capture1, score1 x M, capture2, score2 x M, ...` spreads them over the full run for
   free, and is the largest external-validity lever in the design.
2. **The engine is health-gated before anything is spent.** A capture from a degraded
   engine loses cases, and every cell built from it inherits that.
3. **Each capture is checksummed before and after its scorings.** The judge-only cells are
   only meaningful if the responses underneath are identical; DOE 02 verified this and so
   does this script, rather than assuming scoring is read-only.

Resumable: work already on disk is skipped, so a restart after an ADC expiry costs nothing.
That failure is not hypothetical -- a stale ADC killed the campaign 08 driver and the m01
watcher mid-run.

Usage:
    uv run python scripts/doe03_collect.py --engine-id 4023875557346246656
    uv run python scripts/doe03_collect.py --engine-id ... --captures 6 --scorings 5
    uv run python scripts/doe03_collect.py --engine-id ... --skip-health   # resume only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_EXAMPLE_ENV = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / ".env"
if _EXAMPLE_ENV.exists():
    load_dotenv(str(_EXAMPLE_ENV), override=True)

from scripts.run_campaign import ensure_credentials  # noqa: E402

DEFAULT_EVAL_DATA = "examples/multi_model_agents/eval_data/eval_cases.yaml"
OUT_DIR = Path("outputs/doe03")


def _checksum(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def health_gate(engine_id: str) -> bool:
    """Probe the engine and report whether it clears the bar. ~60 requests, ~12 min.

    Deliberately uses boot_probe's own `GATE_ATTEMPTS`/`GATE_THRESHOLD` rather than a
    cheaper local pair. Both are measurements, not preferences: campaign 01 probed ten
    byte-identical engines and found six at 97-100%, two at 35-55% and two at 0-6% with
    **nothing between 56% and 97%**, so 0.8 separates two populations, and n=60 is what
    places an engine in the right one. A cheaper n would blur exactly the distinction the
    gate exists to make, to save 12 minutes of a two-hour run.
    """
    from wrangler.tools.boot_probe import (
        GATE_ATTEMPTS,
        GATE_THRESHOLD,
        gate_decision,
        gate_report,
        run_probe,
    )

    print(f"\n  Health-gating {engine_id} ({GATE_ATTEMPTS} probes, ~12 min)...", flush=True)
    results = run_probe({"doe03": engine_id}, n=GATE_ATTEMPTS, spacing=2.0)
    summary = results.get("doe03", {})
    # An arm with no rows is ABSENT from summarize(), not reported at zero. Falling back
    # to the requested n keeps that case a failure rather than a division by zero.
    reached, n = int(summary.get("reached", 0)), int(summary.get("n") or GATE_ATTEMPTS)
    decision = gate_decision(reached, n, threshold=GATE_THRESHOLD)
    for line in gate_report(engine_id, decision):
        print(line, flush=True)
    return bool(decision["passed"])


def collect(engine_id: str, eval_data: str, n_captures: int, n_scorings: int) -> dict:
    """Interleaved capture/score loop. Returns the manifest of what exists on disk."""
    from wrangler.core.converter import load_eval_file
    from wrangler.eval.evaluator import capture_inference, save_eval_results, score_captured

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    manifest.setdefault("engine_id", engine_id)
    manifest.setdefault("captures", {})

    if manifest["engine_id"] != engine_id:
        raise SystemExit(
            f"manifest was collected from engine {manifest['engine_id']}, not {engine_id}. "
            f"Mixing engines silently turns within-engine noise into between-engine noise. "
            f"Use a different --out-dir or delete {manifest_path}."
        )

    cases = load_eval_file(eval_data)
    print(f"  Eval set: {eval_data} ({len(cases)} cases)")

    t0 = time.time()
    for i in range(1, n_captures + 1):
        key = f"c{i}"
        entry = manifest["captures"].setdefault(key, {"passes": {}})

        if not entry.get("path") or not Path(entry["path"]).is_file():
            print(f"\n  [{time.time() - t0:6.0f}s] Capture {i}/{n_captures}...", flush=True)
            print(ensure_credentials())
            entry["path"] = capture_inference(
                engine_id=engine_id,
                eval_cases=cases,
                label=f"doe03-{key}",
                agent_name=f"doe03-{key}",
            )
            entry["checksum"] = _checksum(entry["path"])
            manifest_path.write_text(json.dumps(manifest, indent=2))
        else:
            print(f"\n  [{time.time() - t0:6.0f}s] Capture {i}/{n_captures}: on disk, skipping")

        for j in range(1, n_scorings + 1):
            pass_key = f"pass{j}"
            if pass_key in entry["passes"]:
                continue
            print(f"  [{time.time() - t0:6.0f}s]   scoring {key} {j}/{n_scorings}...", flush=True)
            result = score_captured(entry["path"], agent_name=f"doe03-{key}-{pass_key}")
            saved = save_eval_results(
                agent_name=f"doe03-{key}-{pass_key}",
                scores=result.scores,
                phase="scored",
                per_case=result.per_case,
                coverage=result.coverage,
                scoring=result.scoring,
            )
            scored = len(result.per_case)
            entry["passes"][pass_key] = {"file": saved, "cases_scored": scored}
            print(f"      -> {scored}/{len(cases)} cases, {saved}")
            manifest_path.write_text(json.dumps(manifest, indent=2))

        # The premise of every judge-only cell: scoring must not mutate the capture.
        after = _checksum(entry["path"])
        if after != entry["checksum"]:
            raise SystemExit(
                f"{key}'s capture changed during scoring ({entry['checksum']} -> {after}). "
                f"Every score_repeats cell assumes identical responses; it does not hold."
            )

    manifest["elapsed_min"] = round((time.time() - t0) / 60, 1)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine-id", required=True, help="ONE engine; all captures come from it")
    ap.add_argument("--eval-data", default=DEFAULT_EVAL_DATA)
    ap.add_argument("--captures", type=int, default=6, help="pool size; 6 reaches num_runs=3")
    ap.add_argument("--scorings", type=int, default=5, help="per capture; 5 reaches repeats=5")
    ap.add_argument(
        "--skip-health",
        action="store_true",
        help="skip the gate. Only for resuming a run whose engine already passed.",
    )
    args = ap.parse_args()

    if args.captures < 2:
        raise SystemExit("need >= 2 captures for a disjoint before/after split")

    if not args.skip_health and not health_gate(args.engine_id):
        print(
            "\nABORTED: the engine is below the health bar. Captures from it would lose\n"
            "cases, and every resampled cell would inherit that. Pick another engine or\n"
            "redeploy -- do not proceed with --skip-health to get past this.",
            file=sys.stderr,
        )
        return 2

    manifest = collect(args.engine_id, args.eval_data, args.captures, args.scorings)

    passes = sum(len(c["passes"]) for c in manifest["captures"].values())
    print(f"\n  Pool: {len(manifest['captures'])} captures x up to {args.scorings} scorings")
    print(f"  Scoring passes on disk: {passes}")
    print(f"  Elapsed: {manifest.get('elapsed_min')} min")
    print(f"  Manifest: {OUT_DIR / 'manifest.json'}")
    print("\n  Next: uv run python scripts/analyze_doe03.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

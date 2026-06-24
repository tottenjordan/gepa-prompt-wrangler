"""End-to-end experiment runner — pre-flight, pipeline, analysis, comparison.

Usage:
    uv run python scripts/run_experiment_v2.py
    uv run python scripts/run_experiment_v2.py --manifest examples/multi_model_agents/manifest.yaml
    uv run python scripts/run_experiment_v2.py --skip-diagrams
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from wrangler.core.config import REPORTS_DIR


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m {s:02d}s"


def run_step(name: str, cmd: list[str], step_times: list) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")
    t0 = time.time()
    result = subprocess.run(cmd, timeout=7200)
    elapsed = time.time() - t0
    step_times.append((name, elapsed))
    ok = result.returncode == 0
    status = "PASSED" if ok else "FAILED"
    print(f"\n  >> {name}: {status} ({_fmt_duration(elapsed)})")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Run the full GEPA experiment pipeline")
    parser.add_argument("--manifest", default="examples/multi_model_agents/manifest.yaml",
                        help="Path to manifest.yaml")
    parser.add_argument("--skip-diagrams", action="store_true",
                        help="Skip PaperBanana diagram generation")
    parser.add_argument("--max-concurrent", type=int, default=1,
                        help="Max parallel evals (default: 1 = sequential)")
    parser.add_argument("--version", default=None,
                        help="Version tag for saved prompts (e.g. wrangler_v5)")
    args = parser.parse_args()

    pipeline_start = time.time()
    step_times: list[tuple[str, float]] = []

    print(f"{'=' * 60}")
    print(f"GEPA EXPERIMENT v2 — Enhanced Pipeline")
    print(f"{'=' * 60}")
    print(f"  Manifest: {args.manifest}")
    print(f"  Time:     {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # Step 1: Pre-flight validation
    ok = run_step(
        "Step 1: Pre-flight Validation",
        ["uv", "run", "python", "-m", "pytest", "tests/test_eval_data.py", "-v", "--tb=short"],
        step_times,
    )
    if not ok:
        print("\nPre-flight validation failed. Fix test failures before running the experiment.")
        return

    # Step 2: Regenerate evalsets from tagged YAML
    ok = run_step(
        "Step 2: Regenerate Evalsets",
        ["uv", "run", "python", "examples/multi_model_agents/scripts/generate_evalsets.py"],
        step_times,
    )
    if not ok:
        print("\nEvalset generation failed.")
        return

    # Step 3: Run the full pipeline (deploy → eval → optimize → redeploy → eval → report)
    run_cmd = ["uv", "run", "wrangler", "run", args.manifest,
               "--max-concurrent", str(args.max_concurrent)]
    if args.version:
        run_cmd.extend(["--version", args.version])
    ok = run_step(
        "Step 3: Pipeline (deploy, eval, optimize, redeploy, eval, report)",
        run_cmd,
        step_times,
    )
    if not ok:
        print("\nPipeline failed. Check error output above.")
        print("Results may have been partially saved — check outputs/ directory.")

    # Step 4: Generate charts + per-agent reports
    run_step(
        "Step 4: Generate Charts & Reports",
        ["uv", "run", "python", "scripts/generate_analysis.py"],
        step_times,
    )

    # Step 5: Generate architecture diagrams
    if not args.skip_diagrams:
        run_step(
            "Step 5: Generate Diagrams",
            ["uv", "run", "python", "scripts/generate_diagrams.py"],
            step_times,
        )
    else:
        print(f"\n  Step 5: Skipping diagrams (--skip-diagrams)")

    # Step 6: Assemble full report
    print(f"\n{'=' * 60}")
    print(f"  Step 6: Assemble Full Report")
    print(f"{'=' * 60}")
    t0 = time.time()

    reports_dir = Path(REPORTS_DIR)
    agents_dir = reports_dir / "agents"
    charts_dir = reports_dir / "charts"

    report_lines = ["# GEPA Prompt Wrangler — Full Analysis Report\n"]

    diagrams_dir = reports_dir / "diagrams"
    if diagrams_dir.exists():
        pngs = sorted(diagrams_dir.glob("*.png"))
        if pngs:
            report_lines.append("## Architecture Diagrams\n")
            for png in pngs:
                name = png.stem.replace("_", " ").title()
                report_lines.append(f"### {name}\n")
                report_lines.append(f"![{name}](diagrams/{png.name})\n")

    if charts_dir.exists():
        report_lines.append("## Evaluation Charts\n")
        for chart in sorted(charts_dir.glob("*.png")):
            name = chart.stem.replace("_", " ").title()
            report_lines.append(f"### {name}\n")
            report_lines.append(f"![{name}](charts/{chart.name})\n")

    if agents_dir.exists():
        agent_reports = sorted(agents_dir.glob("*_analysis.md"))
        if agent_reports:
            report_lines.append("## Per-Agent Analysis\n")
            for report in agent_reports:
                name = report.stem.replace("_analysis", "").replace("_", " ").title()
                report_lines.append(f"- [{name}](agents/{report.name})")
            report_lines.append("")

    full_report = reports_dir / "full_report.md"
    with open(full_report, "w") as f:
        f.write("\n".join(report_lines))

    elapsed = time.time() - t0
    step_times.append(("Step 6: Assemble Full Report", elapsed))
    print(f"  Full report: {full_report}")

    # Final timing summary
    total = time.time() - pipeline_start
    print(f"\n{'=' * 60}")
    print(f"EXPERIMENT COMPLETE — Total: {_fmt_duration(total)}")
    print(f"{'=' * 60}")
    for step_name, step_time in step_times:
        print(f"  {step_name:55s} {_fmt_duration(step_time):>8s}")
    print(f"{'=' * 60}")
    print(f"\n  Reports:  {reports_dir}")
    print(f"  Charts:   {charts_dir}")
    print(f"  Agents:   {agents_dir}")


if __name__ == "__main__":
    main()

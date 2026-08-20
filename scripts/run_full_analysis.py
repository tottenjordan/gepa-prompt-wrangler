"""Master analysis script — chains chart generation, agent reports, and diagrams.

Usage:
    uv run python scripts/run_full_analysis.py
    uv run python scripts/run_full_analysis.py --skip-diagrams
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from wrangler.core.config import REPORTS_DIR


def run_step(name: str, cmd: list[str]) -> bool:
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")
    result = subprocess.run(cmd, timeout=600)
    return result.returncode == 0


def main(skip_diagrams: bool = False):
    print("GEPA Prompt Wrangler — Full Analysis Pipeline\n")

    # Step 1: Charts + per-agent reports
    ok = run_step(
        "Step 1: Generate Charts & Agent Reports",
        ["uv", "run", "python", "scripts/generate_analysis.py"],
    )
    if not ok:
        print("Analysis generation failed.")
        return

    # Step 2: Architecture diagrams
    if not skip_diagrams:
        run_step(
            "Step 2: Generate Architecture Diagrams",
            ["uv", "run", "python", "scripts/generate_diagrams.py"],
        )
    else:
        print("\nStep 2: Skipping diagrams (--skip-diagrams)")

    # Step 3: Assemble full report
    print(f"\n{'=' * 60}")
    print(f"  Step 3: Assemble Full Report")
    print(f"{'=' * 60}")

    reports_dir = Path(REPORTS_DIR)
    agents_dir = reports_dir / "agents"
    charts_dir = reports_dir / "charts"

    lines = ["# GEPA Prompt Wrangler — Full Analysis Report\n"]

    # Link diagrams
    diagrams_dir = reports_dir / "diagrams"
    if diagrams_dir.exists():
        pngs = sorted(diagrams_dir.glob("*.png"))
        if pngs:
            lines.append("## Architecture Diagrams\n")
            for png in pngs:
                name = png.stem.replace("_", " ").title()
                lines.append(f"### {name}\n")
                lines.append(f"![{name}](diagrams/{png.name})\n")

    # Link charts
    if charts_dir.exists():
        lines.append("## Evaluation Charts\n")
        for chart in sorted(charts_dir.glob("*.png")):
            name = chart.stem.replace("_", " ").title()
            lines.append(f"### {name}\n")
            lines.append(f"![{name}](charts/{chart.name})\n")

    # Link per-agent reports
    if agents_dir.exists():
        agent_reports = sorted(agents_dir.glob("*_analysis.md"))
        if agent_reports:
            lines.append("## Per-Agent Analysis\n")
            for report in agent_reports:
                name = report.stem.replace("_analysis", "").replace("_", " ").title()
                lines.append(f"- [{name}](agents/{report.name})")
            lines.append("")

    full_report = reports_dir / "full_report.md"
    with open(full_report, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  Full report: {full_report}")

    print(f"\n{'=' * 60}")
    print(f"  ANALYSIS COMPLETE")
    print(f"{'=' * 60}")
    print(f"\n  Reports:  {reports_dir}")
    print(f"  Charts:   {charts_dir}")
    print(f"  Agents:   {agents_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-diagrams", action="store_true")
    args = parser.parse_args()
    main(skip_diagrams=args.skip_diagrams)

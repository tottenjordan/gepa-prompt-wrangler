"""Analysis report generation — charts + markdown for prompt optimization experiments."""

import csv
import re
import subprocess
import shutil
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .factory import AgentPromptPair

REPORTS_DIR = Path("outputs/reports")
CHARTS_DIR = REPORTS_DIR / "charts"

METRIC_LABELS = {
    "final_response_quality_v1": "Quality",
    "hallucination_v1": "Hallucination",
    "safety_v1": "Safety",
    "tool_use_quality_v1": "Tool Use",
    "instruction_following_v1": "Instruction",
    "final_response_match_v2": "Response Match",
}


def _ensure_dirs():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def generate_comparison_chart(results: dict[str, dict]):
    """Grouped bar chart comparing all pairs across metrics."""
    _ensure_dirs()
    pairs = list(results.keys())
    metrics = list(METRIC_LABELS.keys())
    n = len(pairs)

    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    for i, metric in enumerate(metrics):
        values = [results[p].get("after", {}).get(metric, 0) for p in pairs]
        ax.bar(x + i * width, values, width, label=METRIC_LABELS[metric], color=colors[i])

    ax.set_xlabel("Agent-Prompt Pair")
    ax.set_ylabel("Score")
    ax.set_title("Post-Optimization Scores — All Pairs")
    ax.set_xticks(x + width * (len(metrics) - 1) / 2)
    ax.set_xticklabels(pairs, rotation=15, ha="right")
    ax.legend(fontsize=8)
    ax.set_ylim(0, 1.15)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "comparison.png", dpi=150)
    plt.close()


def generate_improvement_chart(results: dict[str, dict]):
    """Bar chart showing improvement delta per pair."""
    _ensure_dirs()
    pairs = list(results.keys())
    metrics = list(METRIC_LABELS.keys())
    n = len(pairs)

    fig, ax = plt.subplots(figsize=(14, 7))
    x = np.arange(n)
    width = 0.12
    colors = plt.cm.Set2(np.linspace(0, 1, len(metrics)))

    for i, metric in enumerate(metrics):
        deltas = []
        for p in pairs:
            before = results[p].get("before", {}).get(metric, 0)
            after = results[p].get("after", {}).get(metric, 0)
            deltas.append(after - before)
        ax.bar(x + i * width, deltas, width, label=METRIC_LABELS[metric], color=colors[i])

    ax.set_xlabel("Agent-Prompt Pair")
    ax.set_ylabel("Score Change")
    ax.set_title("GEPA Optimization Impact (After - Before)")
    ax.set_xticks(x + width * (len(metrics) - 1) / 2)
    ax.set_xticklabels(pairs, rotation=15, ha="right")
    ax.legend(fontsize=8)
    ax.axhline(y=0, color="black", linewidth=0.5)
    ax.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig(CHARTS_DIR / "improvement_delta.png", dpi=150)
    plt.close()


def _extract_cost(description: str) -> float | None:
    """Parse cost from pair description. Looks for '$X.XX/M' pattern."""
    match = re.search(r"\$(\d+(?:\.\d+)?)/M", description)
    if match:
        return float(match.group(1))
    return None


def generate_cost_benefit_chart(results: dict[str, dict]):
    """Scatter plot: model cost vs score improvement, generated via PaperBanana."""
    _ensure_dirs()

    rows = []
    for pair_id, data in results.items():
        before = data.get("before", {})
        after = data.get("after", {})
        if not before or not after:
            continue

        # Get cost from pair config description
        desc = ""
        for key in ("description", "model_description"):
            if key in data:
                desc = data[key]
                break
        cost = _extract_cost(desc)
        if cost is None:
            continue

        common = set(before) & set(after)
        if not common:
            continue
        avg_delta = sum(after[m] - before[m] for m in common) / len(common)
        verdict = "improved" if avg_delta > 0 else "regressed"

        rows.append({
            "pair_id": pair_id,
            "cost_per_m": cost,
            "avg_delta": round(avg_delta, 4),
            "verdict": verdict,
        })

    if not rows:
        print("  No cost data found in pair descriptions — skipping cost-benefit chart")
        return

    output_path = CHARTS_DIR / "cost_benefit.png"

    # Try PaperBanana first, fall back to matplotlib
    if _try_paperbanana_cost_chart(rows, output_path):
        return

    _matplotlib_cost_chart(rows, output_path)


def _try_paperbanana_cost_chart(rows: list[dict], output_path: Path) -> bool:
    """Attempt cost-benefit chart via PaperBanana CLI. Returns True on success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "cost_benefit.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["pair_id", "cost_per_m", "avg_delta", "verdict"])
            writer.writeheader()
            writer.writerows(rows)

        intent = (
            "Scatter plot comparing model cost versus optimization improvement. "
            "X-axis: cost_per_m (dollars per million output tokens, log scale). "
            "Y-axis: avg_delta (average score change, positive = improved). "
            "Label each point with its pair_id. "
            "Color points green if verdict is 'improved', red if 'regressed'. "
            "Add a horizontal dashed line at y=0. "
            "Title: 'Cost vs Optimization Impact'."
        )

        try:
            import os
            env = os.environ.copy()
            gemini_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
            if not gemini_key:
                for env_file in [Path(".env"), Path("examples/multi_model_agents/.env")]:
                    if env_file.exists():
                        for line in env_file.read_text().splitlines():
                            if line.startswith("GOOGLE_API_KEY="):
                                gemini_key = line.split("=", 1)[1].strip()
                                break
                    if gemini_key:
                        break
            if gemini_key:
                env["GOOGLE_API_KEY"] = gemini_key
            env["GOOGLE_GENAI_USE_VERTEXAI"] = "0"

            result = subprocess.run(
                [
                    "uv", "run", "paperbanana", "plot",
                    "-d", str(csv_path),
                    "--intent", intent,
                    "-o", str(output_path),
                    "-n", "2",
                ],
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            if result.returncode == 0:
                if output_path.exists():
                    print(f"  Cost-benefit chart saved to {output_path} (PaperBanana)")
                    return True
                # PaperBanana plot command ignores -o; find its output
                pb_outputs = sorted(Path("outputs").glob("run_*/final_output.png"), key=lambda p: p.stat().st_mtime, reverse=True)
                if pb_outputs:
                    shutil.copy2(pb_outputs[0], output_path)
                    print(f"  Cost-benefit chart saved to {output_path} (PaperBanana)")
                    return True
            print(f"  PaperBanana failed (rc={result.returncode}), falling back to matplotlib")
        except (subprocess.TimeoutExpired, FileNotFoundError):
            print("  PaperBanana not available, falling back to matplotlib")
    return False


def _matplotlib_cost_chart(rows: list[dict], output_path: Path):
    """Matplotlib fallback for cost-benefit scatter plot."""
    fig, ax = plt.subplots(figsize=(10, 7))

    for row in rows:
        color = "#2ecc71" if row["verdict"] == "improved" else "#e74c3c"
        ax.scatter(row["cost_per_m"], row["avg_delta"], color=color, s=120, zorder=3, edgecolors="white", linewidth=1.5)
        ax.annotate(
            row["pair_id"], (row["cost_per_m"], row["avg_delta"]),
            textcoords="offset points", xytext=(8, 8), fontsize=8,
        )

    ax.axhline(y=0, color="black", linestyle="--", linewidth=0.8, alpha=0.6)
    ax.set_xscale("log")
    ax.set_xlabel("Cost ($/M output tokens)")
    ax.set_ylabel("Avg Score Delta (After − Before)")
    ax.set_title("Cost vs Optimization Impact")
    ax.grid(True, alpha=0.3)

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#2ecc71", markersize=10, label="Improved"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#e74c3c", markersize=10, label="Regressed"),
    ]
    ax.legend(handles=legend_elements, loc="best")

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()
    print(f"  Cost-benefit chart saved to {output_path}")


def generate_report(results: dict[str, dict], experiment_name: str = "experiment"):
    """Generate full markdown report with charts."""
    _ensure_dirs()
    generate_comparison_chart(results)
    generate_improvement_chart(results)
    generate_cost_benefit_chart(results)

    lines = []
    lines.append(f"# GEPA Prompt Wrangler — {experiment_name}\n")
    lines.append("## Results Overview\n")
    lines.append("![Comparison](../images/comparison.png)\n")
    lines.append("![Improvement Delta](../images/improvement_delta.png)\n")

    cost_benefit_path = CHARTS_DIR / "cost_benefit.png"
    if cost_benefit_path.exists():
        lines.append("![Cost-Benefit](../images/cost_benefit.png)\n")

    lines.append("## Before vs After Scores\n")
    lines.append("| Pair | Metric | Before | After | Delta | Change |")
    lines.append("|------|--------|--------|-------|-------|--------|")

    for pair_id, data in results.items():
        before = data.get("before", {})
        after = data.get("after", {})
        for metric in METRIC_LABELS:
            b = before.get(metric, 0)
            a = after.get(metric, 0)
            delta = a - b
            pct = f"{delta/b*100:+.0f}%" if b > 0 else "N/A"
            lines.append(
                f"| {pair_id} | {METRIC_LABELS[metric]} "
                f"| {b:.2f} | {a:.2f} | {delta:+.2f} | {pct} |"
            )
    lines.append("")

    lines.append("## Optimized Prompts\n")
    for pair_id, data in results.items():
        lines.append(f"### {pair_id}\n")
        lines.append(f"**Model:** `{data.get('model', 'unknown')}`\n")
        if data.get("optimized_prompt"):
            lines.append("**Optimized instruction:**")
            lines.append(f"```\n{data['optimized_prompt']}\n```\n")

    report_path = REPORTS_DIR / "experiment_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Report saved to: {report_path}")

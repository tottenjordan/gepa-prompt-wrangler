"""PaperBanana chart wrappers with matplotlib fallback."""

import glob
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..core.config import MODEL_COSTS, blended_cost
from .analysis import (
    AGENT_ORDER,
    METRIC_LABELS,
    MODEL_MAP,
    PROVIDERS,
    generate_comparison_chart,
    generate_cost_quality_chart,
    generate_improvement_chart,
    generate_radar_chart,
)


def _try_paperbanana(
    data: dict,
    intent: str,
    output_path: Path,
    fallback_fn,
    fallback_kwargs: dict,
    timeout: int = 180,
    max_attempts: int = 2,
) -> bool:
    """Try PaperBanana CLI for chart generation, fall back to matplotlib on failure.

    Returns True if PaperBanana succeeded, False if fallback was used.
    """
    env = os.environ.copy()
    if not env.get("GOOGLE_API_KEY"):
        print("  PaperBanana skipped (no GOOGLE_API_KEY), using matplotlib")
        fallback_fn(**fallback_kwargs)
        return False

    last_error = None
    for attempt in range(max_attempts):
        data_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                json.dump(data, tmp)
                data_path = tmp.name

            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "paperbanana",
                    "plot",
                    "--data",
                    data_path,
                    "--intent",
                    intent,
                    "-n",
                    "3",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )

            # Both raises below deliberately funnel into this function's own
            # `except` so the attempt loop can retry them uniformly.
            if result.returncode != 0:
                msg = result.stderr[-300:] if result.stderr else "unknown error"
                raise RuntimeError(msg)  # noqa: TRY301

            run_dirs = sorted(glob.glob("outputs/run_*"), reverse=True)
            for run_dir in run_dirs:
                final = Path(run_dir) / "final_output.png"
                if final.exists():
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(final), str(output_path))
                    print(f"  Generated (PaperBanana): {output_path.name}")
                    return True

            msg = "PaperBanana output not found in run directories"
            raise FileNotFoundError(msg)  # noqa: TRY301

        except Exception as e:
            last_error = e
            if attempt < max_attempts - 1:
                print(f"  PaperBanana attempt {attempt + 1} failed, retrying...")

        finally:
            if data_path and os.path.exists(data_path):
                os.unlink(data_path)

    print(
        f"  PaperBanana failed after {max_attempts} attempts ({type(last_error).__name__}), using matplotlib"
    )
    fallback_fn(**fallback_kwargs)
    return False


def _get_agents(results: dict) -> list[str]:
    return [a for a in AGENT_ORDER if a in results]


def generate_comparison_chart_pb(
    results: dict,
    charts_dir: Path | None = None,
    use_paperbanana: bool = True,
):
    charts_dir = Path(charts_dir or "outputs/reports/charts")
    if not use_paperbanana:
        generate_comparison_chart(results, charts_dir)
        return

    agents = _get_agents(results)
    data = {
        "agents": [a.title() for a in agents],
        "metrics": {},
    }
    for metric, label in METRIC_LABELS.items():
        data["metrics"][label] = [results[a].get("before", {}).get(metric, 0) for a in agents]

    intent = (
        "Grouped bar chart comparing baseline evaluation scores for AI agent models "
        "across quality metrics. X-axis: agent names, grouped bars per metric. "
        "Y-axis: score (0 to 1). Include a dashed threshold line at 0.6. "
        "Professional color palette, clean grid lines."
    )

    _try_paperbanana(
        data,
        intent,
        charts_dir / "comparison.png",
        fallback_fn=generate_comparison_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )


def generate_improvement_chart_pb(
    results: dict,
    charts_dir: Path | None = None,
    use_paperbanana: bool = True,
):
    charts_dir = Path(charts_dir or "outputs/reports/charts")
    agents = _get_agents(results)
    has_after = any(results[a].get("after") for a in agents)
    if not has_after:
        return

    if not use_paperbanana:
        generate_improvement_chart(results, charts_dir)
        return

    data = {"agents": [a.title() for a in agents], "metrics": {}, "error_bars": {}}
    for metric, label in METRIC_LABELS.items():
        deltas = []
        errors = []
        for a in agents:
            b = results[a].get("before", {}).get(metric, 0)
            af = results[a].get("after", {}).get(metric, 0)
            deltas.append(round(af - b, 4))
            std = results[a].get("after_std", {}).get(metric, 0)
            errors.append(round(std, 4) if std else 0)
        data["metrics"][label] = deltas
        if any(e > 0 for e in errors):
            data["error_bars"][label] = errors

    intent = (
        "Grouped bar chart showing per-metric score change (after minus before) from "
        "prompt optimization. Bars above zero indicate improvement, below zero indicate "
        "regression. Include error bars for standard deviation where available. "
        "Horizontal baseline at y=0. Professional color palette."
    )

    _try_paperbanana(
        data,
        intent,
        charts_dir / "improvement_delta.png",
        fallback_fn=generate_improvement_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )


def generate_cost_quality_chart_pb(
    results: dict,
    charts_dir: Path | None = None,
    use_paperbanana: bool = True,
):
    charts_dir = Path(charts_dir or "outputs/reports/charts")
    if not use_paperbanana:
        generate_cost_quality_chart(results, charts_dir)
        return

    agents = _get_agents(results)
    data = {"agents": []}
    for a in agents:
        model = results[a].get("model", MODEL_MAP.get(a, ""))
        blend = blended_cost(model)
        cost_info = MODEL_COSTS.get(model, {"input": 0, "output": 0})
        before = results[a].get("before", {})
        after = results[a].get("after", before)
        avg_before = sum(before.values()) / max(len(before), 1) if before else 0
        avg_after = sum(after.values()) / max(len(after), 1) if after else 0
        provider = PROVIDERS.get(model, "Unknown")

        data["agents"].append(
            {
                "name": a.title(),
                "blended_cost_per_million": round(blend, 2),
                "input_cost_per_million": round(cost_info["input"], 2),
                "output_cost_per_million": round(cost_info["output"], 2),
                "before_quality": round(avg_before, 4),
                "after_quality": round(avg_after, 4),
                "provider": provider,
            }
        )

    intent = (
        "Scatter plot of model cost vs average quality score with Pareto frontier. "
        "X-axis: blended cost per million tokens (4:1 input:output ratio, log scale). "
        "Y-axis: average quality score (0 to 1). "
        "Show before (circle) and after (diamond) points for each model with dashed arrows "
        "connecting them. Color by provider: blue shades for Google, orange shades for "
        "Anthropic. Draw a green Pareto frontier line connecting non-dominated after points "
        "(sorted by cost ascending, quality must be non-decreasing). Label each point."
    )

    _try_paperbanana(
        data,
        intent,
        charts_dir / "cost_quality.png",
        fallback_fn=generate_cost_quality_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )


def generate_radar_chart_pb(
    results: dict,
    charts_dir: Path | None = None,
    use_paperbanana: bool = True,
):
    charts_dir = Path(charts_dir or "outputs/reports/charts")
    if not use_paperbanana:
        generate_radar_chart(results, charts_dir)
        return

    agents = _get_agents(results)
    data = {"agents": {}}
    for a in agents:
        scores = results[a].get("after", results[a].get("before", {}))
        model = results[a].get("model", MODEL_MAP.get(a, ""))
        provider = PROVIDERS.get(model, "Unknown")
        data["agents"][a.title()] = {
            "scores": {METRIC_LABELS[m]: scores.get(m, 0) for m in METRIC_LABELS},
            "provider": provider,
        }

    intent = (
        "Radar (spider) chart overlaying metric profiles for AI models. Each model "
        "is a polygon with 6 axes for quality metrics. Use blue shades for Google "
        "models and orange shades for Anthropic models. Semi-transparent fills, "
        "clean axis labels. Scale 0 to 1."
    )

    _try_paperbanana(
        data,
        intent,
        charts_dir / "radar.png",
        fallback_fn=generate_radar_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )

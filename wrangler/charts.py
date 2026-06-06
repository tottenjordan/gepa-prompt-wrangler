"""PaperBanana chart wrappers with matplotlib fallback."""

import glob
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .analysis import (
    generate_comparison_chart,
    generate_cost_quality_chart,
    generate_improvement_chart,
    generate_radar_chart,
    METRIC_LABELS,
    AGENT_ORDER,
    MODEL_MAP,
    PROVIDERS,
)
from .config import MODEL_COSTS, PAPERBANANA_PROJECT, PAPERBANANA_LOCATION


def _try_paperbanana(
    data: dict,
    intent: str,
    output_path: Path,
    fallback_fn,
    fallback_kwargs: dict,
    timeout: int = 180,
) -> bool:
    """Try PaperBanana CLI for chart generation, fall back to matplotlib on failure.

    Returns True if PaperBanana succeeded, False if fallback was used.
    """
    data_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(data, tmp)
            data_path = tmp.name

        env = os.environ.copy()
        env["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
        env["GOOGLE_CLOUD_PROJECT"] = PAPERBANANA_PROJECT
        env["GOOGLE_CLOUD_LOCATION"] = PAPERBANANA_LOCATION

        result = subprocess.run(
            [
                "uv", "run", "paperbanana", "plot",
                "--data", data_path,
                "--intent", intent,
                "-n", "2",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr[-300:] if result.stderr else "unknown error")

        run_dirs = sorted(glob.glob("outputs/run_*"), reverse=True)
        for run_dir in run_dirs:
            final = Path(run_dir) / "final_output.png"
            if final.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(final), str(output_path))
                print(f"  Generated (PaperBanana): {output_path.name}")
                return True

        raise FileNotFoundError("PaperBanana output not found in run directories")

    except Exception as e:
        print(f"  PaperBanana unavailable ({type(e).__name__}: {e}), using matplotlib")
        fallback_fn(**fallback_kwargs)
        return False

    finally:
        if data_path and os.path.exists(data_path):
            os.unlink(data_path)


def _get_agents(results: dict) -> list[str]:
    return [a for a in AGENT_ORDER if a in results]


def generate_comparison_chart_pb(
    results: dict, charts_dir: Path | None = None, use_paperbanana: bool = True,
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
        data["metrics"][label] = [
            results[a].get("before", {}).get(metric, 0) for a in agents
        ]

    intent = (
        "Grouped bar chart comparing baseline evaluation scores for AI agent models "
        "across quality metrics. X-axis: agent names, grouped bars per metric. "
        "Y-axis: score (0 to 1). Include a dashed threshold line at 0.6. "
        "Professional color palette, clean grid lines."
    )

    _try_paperbanana(
        data, intent, charts_dir / "comparison.png",
        fallback_fn=generate_comparison_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )


def generate_improvement_chart_pb(
    results: dict, charts_dir: Path | None = None, use_paperbanana: bool = True,
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
        data, intent, charts_dir / "improvement_delta.png",
        fallback_fn=generate_improvement_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )


def generate_cost_quality_chart_pb(
    results: dict, charts_dir: Path | None = None, use_paperbanana: bool = True,
):
    charts_dir = Path(charts_dir or "outputs/reports/charts")
    if not use_paperbanana:
        generate_cost_quality_chart(results, charts_dir)
        return

    agents = _get_agents(results)
    data = {"agents": []}
    for a in agents:
        model = results[a].get("model", MODEL_MAP.get(a, ""))
        cost_info = MODEL_COSTS.get(model, {"input": 0, "output": 0})
        combined_cost = cost_info["input"] + cost_info["output"]
        before = results[a].get("before", {})
        after = results[a].get("after", before)
        avg_before = sum(before.values()) / max(len(before), 1) if before else 0
        avg_after = sum(after.values()) / max(len(after), 1) if after else 0
        provider = PROVIDERS.get(model, "Unknown")

        data["agents"].append({
            "name": a.title(),
            "cost_per_million": round(combined_cost, 2),
            "before_quality": round(avg_before, 4),
            "after_quality": round(avg_after, 4),
            "provider": provider,
        })

    intent = (
        "Scatter plot of model cost vs average quality score. X-axis: combined cost "
        "per million tokens (log scale). Y-axis: average quality score (0 to 1). "
        "Show before (circle) and after (diamond) points for each model with arrows "
        "connecting them. Color by provider: blue shades for Google, orange shades for "
        "Anthropic. Label each point with the model name."
    )

    _try_paperbanana(
        data, intent, charts_dir / "cost_quality.png",
        fallback_fn=generate_cost_quality_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )


def generate_radar_chart_pb(
    results: dict, charts_dir: Path | None = None, use_paperbanana: bool = True,
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
        data, intent, charts_dir / "radar.png",
        fallback_fn=generate_radar_chart,
        fallback_kwargs={"results": results, "charts_dir": charts_dir},
    )

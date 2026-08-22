"""PaperBanana chart wrappers with matplotlib fallback."""

import glob
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..core.config import MODEL_COSTS, PAPERBANANA_API_KEY
from ..core.models import AGENT_ORDER, DEFAULT_FIGURE_VLM_MODEL, MODEL_MAP, PROVIDERS
from ..core.models import blended_cost_for_report as blended_cost
from .analysis import (
    METRIC_LABELS,
    generate_comparison_chart,
    generate_cost_quality_chart,
    generate_improvement_chart,
    generate_radar_chart,
)

# Measured 2026-08-21 on this repo. Every setting below is a fix for something
# that made PaperBanana silently fall back to matplotlib on every chart:
#
# 1. `uvx --from paperbanana` rather than `uv run paperbanana`. Run out of THIS
#    repo's venv, the planner fails and tenacity retries it three times with
#    exponential backoff -- 6m32s to a `ClientError`, reproducibly, from any
#    working directory. The identical CLI and version (0.3.0) in an isolated
#    uvx environment completes in ~63s. The API itself is fine: the same key
#    and models answer a direct generateContent in 2-7s. Something in the
#    repo's dependency set breaks paperbanana's planner call, so give it its
#    own environment rather than sharing ours.
#
# 2. IMAGE_MODEL / VLM_MODEL set explicitly. paperbanana's own defaults are
#    `gemini-3-pro-image-preview` and `gemini-2.5-flash` (its core/config.py).
#    The MCP server sets the good models in its env block; this subprocess
#    inherited nothing, so the repo had been running the slow pro/preview
#    image model on every chart.
#
# 3. `-n 1`, not `-n 3`. `-n` is `--iterations`: *refinement* passes, each a
#    visualizer->critic round trip. Three of them tripled the runtime for a
#    chart the first pass already renders correctly.
#
# 4. cwd is the temp dir. paperbanana's Settings declare `env_file=".env"`, so
#    running from the repo root feeds it our .env; and its run directories are
#    written to cwd, which is how `outputs/run_*` accumulated here. Scoping the
#    glob to a per-call temp dir also removes a real trap: the old code globbed
#    the repo's shared `outputs/`, so a run that produced nothing would copy the
#    newest *previous* chart and report success.
#
# 5. The subprocess env is built from scratch rather than inherited. THIS is the
#    one that actually broke it. Importing `core.config` calls load_dotenv(), so
#    the whole Vertex/GEAP configuration lands in os.environ and was copied
#    straight in. `GOOGLE_GENAI_USE_VERTEXAI=1` alone is fatal -- measured
#    directly, same key and model:
#
#      USE_VERTEXAI=1 -> ClientError: 401 UNAUTHENTICATED.
#                        API keys are not supported by this API.
#      unset          -> OK
#
#    which is precisely the ClientError the planner retried three times. Popping
#    just that one was still not enough, so we pass only what the working MCP
#    server passes. CLAUDE.md carries the mirror-image rule for the pipeline (pop
#    GOOGLE_API_KEY so it cannot override Vertex ADC): the two credential styles
#    are mutually exclusive, and this process needs the other one.
_PB_TIMEOUT = 300
_PB_ENV = {
    # Image model kept as a literal and exempted in tests/test_models.py: it is a
    # figure renderer, not an agent or judge, so it has no cost or RPM to register.
    "IMAGE_MODEL": "gemini-3.1-flash-image",
    "VLM_MODEL": DEFAULT_FIGURE_VLM_MODEL,
}


def _try_paperbanana(
    data: dict,
    intent: str,
    output_path: Path,
    fallback_fn,
    fallback_kwargs: dict,
    timeout: int = _PB_TIMEOUT,
    max_attempts: int = 2,
) -> bool:
    """Try PaperBanana CLI for chart generation, fall back to matplotlib on failure.

    Returns True if PaperBanana succeeded, False if fallback was used.
    """
    # From the stash, not the environment: core.config pops the API keys at
    # import so they cannot reach Vertex, which rejects them (401 on
    # EvaluationService). PaperBanana is the one caller that legitimately
    # wants an API key, so it is handed the value directly.
    api_key = PAPERBANANA_API_KEY
    if not api_key:
        print("  PaperBanana skipped (no GOOGLE_API_KEY), using matplotlib")
        fallback_fn(**fallback_kwargs)
        return False

    # Built from scratch, NOT os.environ.copy(). Handing paperbanana this
    # repo's full environment is what broke it; the MCP server, which works,
    # passes exactly three variables. PATH and HOME are needed for uvx and its
    # cache. Everything else in our environment is Vertex/GEAP configuration
    # that this process must not see.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GOOGLE_API_KEY": api_key,
        **_PB_ENV,
    }

    last_error = None
    for attempt in range(max_attempts):
        # A fresh working directory per attempt: paperbanana writes its run dirs
        # into cwd, and scoping the search to this directory means we can only
        # ever pick up output *this* call produced.
        with tempfile.TemporaryDirectory(prefix="paperbanana_") as workdir:
            try:
                data_path = Path(workdir) / "data.json"
                data_path.write_text(json.dumps(data))

                result = subprocess.run(
                    [
                        "uvx",
                        "--from",
                        "paperbanana",
                        "paperbanana",
                        "plot",
                        "--data",
                        str(data_path),
                        "--intent",
                        intent,
                        "-n",
                        "1",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                    cwd=workdir,
                    check=False,
                )

                # Both raises below deliberately funnel into this function's own
                # `except` so the attempt loop can retry them uniformly.
                if result.returncode != 0:
                    msg = result.stderr[-300:] if result.stderr else "unknown error"
                    raise RuntimeError(msg)  # noqa: TRY301

                produced = sorted(glob.glob(f"{workdir}/**/final_output.png", recursive=True))
                if produced:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(produced[-1], str(output_path))
                    print(f"  Generated (PaperBanana): {output_path.name}")
                    return True

                msg = "PaperBanana output not found in run directories"
                raise FileNotFoundError(msg)  # noqa: TRY301

            except Exception as e:
                last_error = e
                if attempt < max_attempts - 1:
                    print(f"  PaperBanana attempt {attempt + 1} failed, retrying...")

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

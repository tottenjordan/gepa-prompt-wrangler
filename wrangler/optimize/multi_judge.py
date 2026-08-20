"""Multi-judge ensemble scoring for GEPA custom metrics.

Calls multiple judge models with the same evaluation prompt and averages
their scores. Register as a custom metric in sampler_config.json via
EvalConfig.custom_metrics.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_JUDGE_MODELS = ["gemini-2.5-pro", "gemini-2.5-flash"]

_QUALITY_PROMPT = """Evaluate the agent's response to the user's query.

User query: {query}

Agent response: {response}

Reference response: {reference}

Rate the overall quality of the agent's response on a scale from 0.0 to 1.0:
- 1.0: Perfect — accurate, complete, well-formatted, follows all instructions
- 0.75: Good — mostly correct with minor issues
- 0.5: Acceptable — partially addresses the query but has significant gaps
- 0.25: Poor — largely incorrect or incomplete
- 0.0: Failure — wrong, harmful, or completely off-topic

Respond with ONLY a single number between 0.0 and 1.0."""


def _get_judge_models() -> list[str]:
    env = os.environ.get("WRANGLER_JUDGE_MODELS", "")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    return list(DEFAULT_JUDGE_MODELS)


def _call_judge(model: str, prompt: str) -> float:
    """Call a single judge model and extract a numeric score."""
    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ.get("GCP_PROJECT_ID", ""),
        location="global",
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
    )
    text = response.text.strip()
    try:
        score = float(text)
        return max(0.0, min(1.0, score))
    except ValueError:
        import re

        match = re.search(r"(\d+\.?\d*)", text)
        if match:
            return max(0.0, min(1.0, float(match.group(1))))
        log.warning("Judge %s returned non-numeric: %s", model, text[:100])
        return 0.5


def evaluate(
    query: str = "",
    response: str = "",
    reference: str = "",
    **kwargs: Any,
) -> float:
    """Multi-judge ensemble evaluation.

    Compatible with ADK's custom_metrics function signature.
    Returns the mean score across all configured judge models.
    """
    judges = _get_judge_models()
    prompt = _QUALITY_PROMPT.format(
        query=query,
        response=response,
        reference=reference,
    )

    scores = []
    for model in judges:
        try:
            score = _call_judge(model, prompt)
            scores.append(score)
            log.debug("Judge %s scored %.2f", model, score)
        except Exception as e:
            log.warning("Judge %s failed: %s", model, e)

    if not scores:
        log.warning("All judges failed, returning 0.5")
        return 0.5

    mean = sum(scores) / len(scores)
    log.debug("Multi-judge mean: %.3f (from %d judges)", mean, len(scores))
    return mean

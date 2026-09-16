"""Probe candidate models for the GEPA optimizer-model role.

The optimizer model reads eval failures and writes the next candidate prompt. It has moved
three times in a week -- gemini-2.5-flash (ADK's unexamined default), gemini-3.5-flash
(PR #83), claude-opus-4-8 (PR #86) -- and none of those was chosen against a survey. This
measures the candidates instead.

**Every probe goes through ADK's own `generate_reflection_response`, never a hand-rolled
client.** That is not fussiness. The function ends:

    if not content or not content.parts:
        return ""
    return "".join(part.text for part in content.parts if part.text and not part.thought)

It strips thought parts and returns an empty string rather than raising, so a model that
emits only thoughts, or is truncated before visible text, hands GEPA an **empty prompt
proposal** and the optimize stage runs its full nine hours regardless. A probe that called
the vendor SDK directly would see a perfectly good response and miss it entirely. The
thinking-heavy candidates are the ones most at risk, and they are also the most attractive
on paper -- so this is the probe that matters.

What is measured, per model:

    resolve    ADK class, and whether construction works at all
    thinking   which of adaptive / enabled / disabled / none the vendor accepts
    empty      does a successful call survive thought-stripping with text left
    reflect    does a realistic GEPA-shaped prompt produce a usable instruction
    chars      how long the proposed prompts are, over several samples
    latency    seconds per call
    spread     do three identical calls agree in length

**Length is reported, not ranked.** GEPA's documented failure mode is the reflector
encoding edge cases into ever-longer prompts, trading training score for generalisation;
campaign 07 measured 78 -> 3,873 characters alongside exactly the criterion-up /
holdout-down signature that predicts. That makes a concise writer *plausibly* better, on
one internal correlation plus the literature. It is not established, and this script must
not be read as establishing it.

**These probes measure capability, not outcome.** A model can pass every one and still
write worse prompts. Only a real optimize-stage comparison answers that.

Usage:
    uv run python scripts/probe_optimizer_models.py --list
    uv run python scripts/probe_optimizer_models.py --models claude-opus-5,gemini-3.5-flash
    uv run python scripts/probe_optimizer_models.py --all
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_EXAMPLE_ENV = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents" / ".env"
if _EXAMPLE_ENV.exists():
    load_dotenv(str(_EXAMPLE_ENV), override=True)

# Claude and Gemini 3.x are not servable from a region; see CLAUDE.md's location rule.
# Set after load_dotenv, because a .env value would otherwise win and send Claude to a
# regional endpoint -- the same ordering bug PR #86 fixed in the pipeline components.
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
os.environ.pop("GOOGLE_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)

# A realistic reflection request: traces, scores and rubric feedback, which is the shape
# GEPA actually sends. A one-line prompt would exercise liveness and nothing else -- in
# particular it would not show whether a model answers with an instruction or with
# commentary about the instruction, which is the difference between a usable writer and a
# useless one.
REFLECTION_PROMPT = """You are improving the system prompt of a travel-booking assistant.

Current prompt:
  "You are a helpful assistant."

Evaluation results on 4 cases:
  case 1 | safety 0.62 | the assistant offered to bypass a fare rule when asked
  case 2 | safety 0.55 | the assistant speculated about a refund policy it had not checked
  case 3 | instruction_following 0.71 | asked for a one-line answer, returned six lines
  case 4 | instruction_following 0.68 | asked to cite the tool used, did not cite it

Rubric feedback: responses should refuse policy circumvention, ground claims in tool
output, and follow explicit formatting instructions.

Write an improved system prompt. Reply with the prompt text only."""

LIVENESS_PROMPT = "Reply with exactly the word: OK"

# Scoped deliberately to the **latest first-party models**: Claude and Gemini, plus the
# incumbent. Model Garden's open and partner models were surveyed once on 2026-09-16 and
# ruled out -- reachable only as `vertex_ai/<publisher>/<model>` at location "global" with
# `vertex_project`/`vertex_location` passed explicitly, and then 2 of 5 returned 429 on a
# first call with LiteLLM retrying internally for minutes. Against a 69-call reflection
# budget that is a campaign-length risk on the axis already binding. The findings are in
# docs/analysis/2026-09-16-writer-model-survey.md; re-add them here only if the quota
# position changes.
CANDIDATES: dict[str, str] = {
    # -- the two the question names --
    "claude-opus-4-8": "current default (PR #86); Model Garden reports CAN_PREDICT=No",
    "gemini-3.5-flash": "PR #83's choice, and the judge -- identity collision",
    # -- latest Claude --
    "claude-opus-5": "latest Opus; the Anthropic model Model Garden advertises",
    "claude-sonnet-5": "latest Sonnet; collides with the agent under test in c08",
    "claude-fable-5-1": "latest Fable tier; unregistered",
    # -- latest Gemini --
    "gemini-3.7-flash": "callable but ABSENT from the registry",
    "gemini-3.8-flash": "newest Gemini flash; callable but ABSENT from the registry",
}

# -1 adaptive, 10240 legacy manual (ADK's default), 0 disabled, None no thinking config.
THINKING_MODES: list[tuple[str, int | None]] = [
    ("adaptive", -1),
    ("enabled", 10240),
    ("disabled", 0),
    ("none", None),
]


@dataclass
class Result:
    model: str
    note: str
    adk_class: str = "-"
    accepted: list[str] = field(default_factory=list)
    empty_after_strip: bool | None = None
    reflect_ok: bool | None = None
    chars: list[int] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    error: str = ""

    @property
    def verdict(self) -> str:
        if self.error and not self.accepted:
            return "FAIL"
        if self.empty_after_strip:
            return "EMPTY"  # the silent one
        if self.reflect_ok is False:
            return "UNUSABLE"
        return "ok" if self.accepted else "FAIL"


def _config(budget: int | None, max_out: int = 16384):
    from google.genai import types as t

    if budget is None:
        return t.GenerateContentConfig(max_output_tokens=max_out)
    return t.GenerateContentConfig(
        thinking_config=t.ThinkingConfig(include_thoughts=True, thinking_budget=budget),
        max_output_tokens=max_out,
    )


def _build_llm(model: str):
    """Construct the ADK LLM for a candidate.

    LiteLLM needs `vertex_project` and `vertex_location` passed explicitly -- it does not
    read GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION on the Vertex path, and without them
    every Model Garden model fails with "Vertex project and location are required for
    custom endpoint". `LLMRegistry.new_llm()` cannot supply them, so LiteLlm is constructed
    directly.
    """
    from google.adk.models.registry import LLMRegistry

    cls = LLMRegistry.resolve(model)
    if cls.__name__ == "LiteLlm":
        return cls(
            model=model,
            vertex_project=os.environ["GCP_PROJECT_ID"],
            vertex_location="global",
        )
    return LLMRegistry.new_llm(model)


async def _call(model: str, budget: int | None, prompt: str) -> tuple[str, float]:
    """One reflection request through ADK's own path. Returns (text, seconds)."""
    from google.adk.optimization._gepa_utils import generate_reflection_response

    llm = _build_llm(model)
    t0 = time.time()
    out = await generate_reflection_response(
        llm=llm, model=model, config=_config(budget), prompt=prompt
    )
    return out, time.time() - t0


async def probe(model: str, note: str, samples: int) -> Result:
    from google.adk.models.registry import LLMRegistry

    res = Result(model=model, note=note)
    try:
        res.adk_class = LLMRegistry.resolve(model).__name__
        _build_llm(model)  # construction is part of P0
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"[:120]
        return res

    # Which thinking modes does the vendor accept? Each attempt is also a liveness check
    # and, because it goes through generate_reflection_response, an empty-after-strip check.
    best: int | None = None
    for label, budget in THINKING_MODES:
        try:
            out, secs = await _call(model, budget, LIVENESS_PROMPT)
        except Exception as exc:
            if not res.error:
                res.error = f"{label}: {type(exc).__name__}: {str(exc)[:80]}"
            continue
        res.accepted.append(label)
        res.latencies.append(secs)
        if best is None:
            best = budget
            res.empty_after_strip = not out.strip()

    if not res.accepted:
        return res

    # A realistic reflection, sampled, using the first accepted mode.
    for _ in range(samples):
        try:
            out, secs = await _call(model, best, REFLECTION_PROMPT)
        except Exception as exc:
            res.error = res.error or f"reflect: {type(exc).__name__}: {str(exc)[:80]}"
            break
        res.latencies.append(secs)
        res.chars.append(len(out.strip()))
        if res.reflect_ok is None:
            # Usable means: it returned something prompt-shaped rather than an empty
            # string, a refusal, or a sentence about the prompt it declined to write.
            res.reflect_ok = len(out.strip()) > 80
    return res


def render(results: list[Result]) -> list[str]:
    head = (
        f"{'model':44} {'class':8} {'verdict':9} {'thinking accepted':26} "
        f"{'chars':>13} {'s/call':>7}"
    )
    lines = [head, "-" * len(head)]
    for r in sorted(results, key=lambda x: (x.verdict != "ok", x.model)):
        chars = (
            f"{statistics.mean(r.chars):.0f}"
            + (f" ±{statistics.pstdev(r.chars):.0f}" if len(r.chars) > 1 else "")
            if r.chars
            else "-"
        )
        lat = f"{statistics.mean(r.latencies):.1f}" if r.latencies else "-"
        lines.append(
            f"{r.model:44} {r.adk_class:8} {r.verdict:9} "
            f"{','.join(r.accepted) or '-':26} {chars:>13} {lat:>7}"
        )
        if r.error:
            lines.append(f"{'':44} └─ {r.error}")
    return lines


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--models", help="comma-separated subset of the candidate list")
    ap.add_argument("--all", action="store_true", help="probe every candidate")
    ap.add_argument("--list", action="store_true", help="print the candidates and exit")
    ap.add_argument("--samples", type=int, default=3, help="reflection samples per model")
    args = ap.parse_args()

    if args.list:
        for m, note in CANDIDATES.items():
            print(f"  {m:44} {note}")
        return 0
    if args.models:
        wanted = {m.strip(): CANDIDATES.get(m.strip(), "") for m in args.models.split(",")}
    elif args.all:
        wanted = CANDIDATES
    else:
        ap.error("pass --models, --all, or --list")

    results = []
    for model, note in wanted.items():
        print(f"probing {model} ...", file=sys.stderr, flush=True)
        results.append(await probe(model, note, args.samples))

    print()
    for line in render(results):
        print(line)
    print(
        "\nverdicts: ok | EMPTY (returned '' after thought-stripping -- silent failure) | "
        "UNUSABLE (no prompt-shaped output) | FAIL (no accepted config)"
    )
    print("Capability, not outcome: passing says nothing about prompt quality.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

# Evaluation Quality Guide

How GEPA local eval and GEAP batch eval metrics relate, how to configure judge models, and how to use multi-judge scoring.

---

## Metric Mapping: GEPA vs GEAP

GEPA local eval (optimization) and GEAP batch eval (measurement) use different metric systems. Understanding the mapping prevents optimizing for one thing while measuring another.

| GEPA Local Eval Metric | GEAP Batch Eval Metric | Alignment |
|----------------------|----------------------|-----------|
| `response_match_score` | — | GEPA only (string similarity) |
| `final_response_match_v2` | `final_response_match_v1` | Equivalent (LLM-judged semantic match) |
| `safety_v1` | `safety_v1` | Equivalent |
| `rubric_based_final_response_quality_v1` | `final_response_quality_v1` / `instruction_following_v1` | Approximate (via custom rubrics) |
| `rubric_based_tool_use_quality_v1` | `tool_use_quality_v1` | Approximate |
| — | `hallucination_v1` | Batch eval only |

### Key Gap

GEAP batch eval has a dedicated `instruction_following_v1` metric. ADK's `PrebuiltMetrics` enum does not include an equivalent. The closest ADK-native mechanism is `rubric_based_final_response_quality_v1` with instruction-adherence rubrics. The wrangler's default sampler config includes two rubrics that approximate this:

- **`instruction_adherence`** — Evaluates whether the response follows system prompt instructions (formatting, tone, content requirements)
- **`completeness`** — Evaluates whether the response fully addresses all parts of the user's request

---

## Judge Model Configuration

### GEPA Local Eval

The judge model for GEPA optimization is configurable at three levels:

1. **Manifest** (`eval_config.judge_model`):
   ```yaml
   eval_config:
     judge_model: gemini-2.5-pro
   ```

2. **CLI** (`--judge-model`):
   ```bash
   wrangler optimize manifest.yaml --judge-model gemini-2.5-flash
   ```

3. **Sampler config** (`eval_config.criteria.*.judge_model_options.judge_model`):
   ```json
   {
     "eval_config": {
       "criteria": {
         "final_response_match_v2": {
           "threshold": 0.5,
           "judge_model_options": {"judge_model": "gemini-2.5-pro"}
         }
       }
     }
   }
   ```

### GEAP Batch Eval

Batch eval uses Vertex AI's built-in evaluation service with predefined `RubricMetric` enums. The judge model is **not configurable** — it's internal to Vertex AI.

---

## Multi-Judge Ensemble

Single-judge evaluation is susceptible to model-specific biases. Multi-judge scoring calls multiple models with the same evaluation prompt and averages their scores.

### Enabling Multi-Judge

**Via CLI:**
```bash
wrangler optimize manifest.yaml --multi-judge
```

**Via environment variable** (configure which models to use):
```bash
export WRANGLER_JUDGE_MODELS="gemini-2.5-pro,gemini-2.5-flash"
wrangler optimize manifest.yaml --multi-judge
```

Default judges when `WRANGLER_JUDGE_MODELS` is not set: `gemini-2.5-pro` and `gemini-2.5-flash`.

### How It Works

Multi-judge registers a custom metric (`multi_judge_quality`) in the GEPA sampler config:

```json
{
  "eval_config": {
    "criteria": {
      "multi_judge_quality": 0.5
    },
    "custom_metrics": {
      "multi_judge_quality": {
        "code_config": {"name": "wrangler.multi_judge.evaluate"},
        "description": "Multi-model ensemble quality score"
      }
    }
  }
}
```

The `wrangler.multi_judge.evaluate` function:
1. Receives the user query, agent response, and reference response
2. Sends the same evaluation prompt to each configured judge model
3. Extracts a numeric score (0.0-1.0) from each judge
4. Returns the mean score

### Cost Implications

Each additional judge model multiplies inference cost for the multi-judge metric. With `num_samples=5` (ADK default), each evaluation already runs 5 inference calls per case for LLM-judged metrics.

| Configuration | Judge Calls per Case | Impact |
|--------------|---------------------|--------|
| Single judge (default) | 5 per LLM-judged metric | Baseline |
| Multi-judge (2 models) | 2 additional calls for `multi_judge_quality` | +40 calls per iteration (20 train cases) |
| Multi-judge (3 models) | 3 additional calls | +60 calls per iteration |

For a 28-case training set with 11 GEPA iterations, multi-judge with 2 models adds ~880 judge calls to the optimization run.

---

## Rubric Customization

The `rubric_based_final_response_quality_v1` metric supports custom rubrics. Each rubric defines a testable property that the LLM judge evaluates with a yes/no verdict.

### Default Rubrics

```json
{
  "rubric_based_final_response_quality_v1": {
    "threshold": 0.5,
    "judge_model_options": {"judge_model": "gemini-2.5-pro"},
    "rubrics": [
      {
        "rubric_id": "instruction_adherence",
        "rubric_content": {
          "text_property": "The agent's response follows all instructions in the system prompt."
        },
        "type": "INSTRUCTION_ADHERENCE"
      },
      {
        "rubric_id": "completeness",
        "rubric_content": {
          "text_property": "The agent's response fully addresses all parts of the user's request."
        },
        "type": "FINAL_RESPONSE_QUALITY"
      }
    ]
  }
}
```

### Adding Custom Rubrics

Add rubrics to the sampler config's criteria. Each rubric needs:
- `rubric_id` — unique identifier
- `rubric_content.text_property` — the testable property (must be phrased as a statement)
- `type` — rubric type (e.g., `INSTRUCTION_ADHERENCE`, `FINAL_RESPONSE_QUALITY`, `TOOL_USE_QUALITY`)

Example for a travel agent:
```json
{
  "rubric_id": "policy_citation",
  "rubric_content": {
    "text_property": "When the response mentions an expense policy limit, it cites the specific dollar amount."
  },
  "type": "FINAL_RESPONSE_QUALITY"
}
```

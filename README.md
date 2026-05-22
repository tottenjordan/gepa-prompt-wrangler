# GEPA Prompt Wrangler

![GEPA Prompt Wrangler](docs/gepa_prompt_wrangler_banner.png)

A prompt optimization harness for [Google ADK](https://google.github.io/adk-docs/) agents. Define multiple model + system-prompt pairs in a YAML manifest, evaluate them against a shared eval set, and let the harness find the best-performing combination -- then deploy the winner to Vertex AI Agent Engine with a single command.

---

## Default Workflow

```
1. Define     write a manifest with model/prompt pairs
2. Deploy     wrangler run manifest.yaml
3. Evaluate   wrangler eval manifest.yaml
4. Optimize   wrangler optimize manifest.yaml
5. Re-eval    wrangler eval manifest.yaml
6. Report     wrangler report manifest.yaml
```

The optimize step rewrites system prompts using an LLM judge's feedback, then you re-evaluate to confirm improvement. When satisfied, deploy the winning pair:

```
wrangler deploy manifest.yaml --pair gemini-flash-concise
```

---

## Quick Start

```bash
# Clone the repo
git clone <repo-url>
cd gepa-prompt-wrangler

# Install dependencies with uv
uv sync

# Configure environment
cp .env.example .env
# Edit .env with your GCP project, region, and staging bucket

# Generate a starter manifest
uv run wrangler init

# Inspect the manifest
uv run wrangler inspect manifest.yaml

# Run the example end-to-end
uv run wrangler run manifests/example_manifest.yaml
uv run wrangler eval manifests/example_manifest.yaml
uv run wrangler report manifests/example_manifest.yaml
```

---

## Repository Structure

```
gepa-prompt-wrangler/
├── pyproject.toml              # Project config (uv, dependencies, scripts)
├── .env.example                # Environment variable template
├── .gitignore
├── README.md
│
├── wrangler/                   # Core library
│   ├── __init__.py
│   ├── cli.py                  # Click-based CLI entry point
│   ├── config.py               # GCP config, resolve_model(), disable_pyopenssl()
│   ├── converter.py            # YAML ↔ ADK evalset format conversion
│   └── factory.py              # Manifest parser and AgentPromptPair dataclass
│
├── agents/                     # Agent modules (one directory per agent)
│   └── example_agent/
│       ├── __init__.py
│       └── agent.py            # Example travel agent with mock tools
│
├── manifests/                  # Optimization manifests
│   └── example_manifest.yaml
│
├── eval_data/                  # Eval datasets (YAML or ADK JSON)
│   └── example_eval.yaml
│
├── tests/                      # Test suite
│   └── __init__.py
│
├── outputs/                    # Generated reports (gitignored)
├── eval_outputs/               # Eval results (gitignored)
├── examples/                   # Additional usage examples
└── scripts/                    # Utility scripts
```

---

## Manifest Format

A manifest defines one optimization run. It specifies the agent module, eval data, and one or more model + system-prompt pairs to compare.

```yaml
name: travel-agent-prompt-comparison
description: Compare Gemini Flash and Claude Sonnet on travel queries.

agent_module: agents.example_agent
eval_data: eval_data/example_eval.yaml

pairs:
  - id: gemini-flash-concise
    model: gemini-3.5-flash
    description: Concise directive prompt style.
    system_prompt: |
      You are a corporate travel assistant. Use the available tools to
      search flights, find hotels, check travel policies, create bookings,
      and manage expense reports.
    temperature: 1.0
    tags: [gemini, concise]

  - id: claude-sonnet-detailed
    model: claude-sonnet-4-6
    description: Detailed persona-driven prompt style.
    system_prompt: |
      You are TravelBot, a friendly and thorough corporate travel assistant
      employed by Acme Corp...
    tags: [claude, detailed]

eval_config:
  metrics:
    - tool_trajectory
    - response_match
  judge_model: gemini-2.5-flash
```

### Required fields

| Field          | Description                                        |
|----------------|----------------------------------------------------|
| `name`         | Unique name for this optimization run              |
| `agent_module` | Python module path to the agent (must export `agent`) |
| `pairs`        | List of model + system-prompt pairs                |

### Pair fields

| Field           | Required | Description                          |
|-----------------|----------|--------------------------------------|
| `id`            | Yes      | Unique identifier for the pair       |
| `model`         | Yes      | Model string (see Supported Models)  |
| `system_prompt` | Yes      | System prompt text                   |
| `temperature`   | No       | Sampling temperature (default 1.0)   |
| `description`   | No       | Human-readable description           |
| `tags`          | No       | Tags for filtering and grouping      |

---

## Eval Data Formats

### Simplified YAML (recommended for authoring)

```yaml
- query: "Find flights from SFO to JFK on June 15"
  expected_response: "Found available flights from SFO to JFK"
  expected_tools:
    - search_flights
  tags:
    - search

- query: "Does a $450 one-way domestic flight comply with our travel policy?"
  expected_response: "policy"
  expected_tools:
    - check_policy
  tags:
    - policy
```

### ADK JSON (native evaluation format)

```json
[
  {
    "query": "Find flights from SFO to JFK on June 15",
    "reference": "Found available flights from SFO to JFK",
    "expected_tool_use": [
      { "tool_name": "search_flights", "tool_input": {} }
    ]
  }
]
```

The converter module auto-detects the format and translates between them. Use the simplified YAML for authoring eval cases and let the harness convert to ADK JSON at evaluation time.

---

## CLI Commands

| Command    | Description                                              |
|------------|----------------------------------------------------------|
| `init`     | Create a starter `manifest.yaml` in the current directory |
| `inspect`  | Parse and display a manifest's structure                 |
| `run`      | Deploy agents defined in the manifest                    |
| `eval`     | Run ADK evaluation against deployed agents               |
| `optimize` | Rewrite prompts using eval feedback (grid, bayesian, llm)|
| `report`   | Generate a comparison report (HTML, JSON, or CSV)        |
| `deploy`   | Deploy the winning pair to Agent Engine                   |

### Common options

```bash
# Show version
wrangler --version

# Run a specific pair only
wrangler run manifest.yaml --pair gemini-flash-concise

# Dry run (validate without executing)
wrangler run manifest.yaml --dry-run

# Custom output directory for reports
wrangler report manifest.yaml --output-dir ./my-reports --format csv

# Deploy with existing engine
wrangler deploy manifest.yaml --pair gemini-flash-concise --engine-id 12345
```

---

## Supported Models

All models are resolved through `resolve_model()` which handles Vertex AI routing automatically.

### Gemini (Google)

| Model                       | Notes                                   |
|-----------------------------|-----------------------------------------|
| `gemini-2.5-flash`         | Regional endpoint, passed as string     |
| `gemini-3.5-flash`         | Global endpoint via LiteLLM             |
| `gemini-3.1-flash-lite`    | Global endpoint via LiteLLM             |
| `gemini-3.1-pro-preview`   | Global endpoint via LiteLLM             |

### Claude (Anthropic via Vertex AI)

| Model                       | Notes                                   |
|-----------------------------|-----------------------------------------|
| `claude-sonnet-4-6`        | Global endpoint via LiteLLM             |
| `claude-opus-4-6`          | Global endpoint via LiteLLM             |

Gemini 2.x models use regional Vertex AI endpoints and are passed as plain strings. All other models (Gemini 3.x, Claude) route through `LiteLlm` with `vertex_location="global"`.

---

## Examples

### Compare two prompt styles

```bash
# 1. Write a manifest with a concise vs. verbose prompt pair
# 2. Deploy both
uv run wrangler run manifests/example_manifest.yaml

# 3. Evaluate both against the same eval set
uv run wrangler eval manifests/example_manifest.yaml

# 4. Generate a side-by-side report
uv run wrangler report manifests/example_manifest.yaml --format html
```

### Optimize a prompt iteratively

```bash
# Run 3 rounds of LLM-guided prompt optimization
uv run wrangler optimize manifests/example_manifest.yaml \
    --strategy llm \
    --iterations 3

# Re-evaluate after optimization
uv run wrangler eval manifests/example_manifest.yaml

# Deploy the winner
uv run wrangler deploy manifests/example_manifest.yaml \
    --pair gemini-flash-concise
```

### Convert eval formats

```python
from wrangler.converter import load_eval_file, save_adk_evalset

# Load simplified YAML
cases = load_eval_file("eval_data/example_eval.yaml")

# Export as ADK JSON
save_adk_evalset(cases, "eval_data/example_eval.json")
```

---

## Contributing

1. Fork the repository and create a feature branch.
2. Install dev dependencies: `uv sync`.
3. Write tests in `tests/` for any new functionality.
4. Run the test suite: `uv run pytest`.
5. Submit a pull request with a clear description of the change.

### Code style

- Follow PEP 8 with a 100-character line limit.
- Use type hints for all function signatures.
- Docstrings for all public functions and classes.

---

## FAQ

**Q: Do I need a GCP project to use this?**
A: Yes. The harness deploys agents to Vertex AI Agent Engine and uses Vertex AI models for evaluation. You need a GCP project with the Vertex AI API enabled.

**Q: Can I use non-Vertex AI models?**
A: The `resolve_model()` function currently supports Gemini and Claude models via Vertex AI. To add other providers, extend `resolve_model()` with additional LiteLLM provider prefixes.

**Q: What is the difference between `run` and `deploy`?**
A: `run` deploys all pairs in a manifest for evaluation purposes. `deploy` promotes a single winning pair to a production Agent Engine instance.

**Q: How does the optimizer work?**
A: The `optimize` command supports three strategies:
- `grid` -- exhaustive search over prompt variations.
- `bayesian` -- Bayesian optimization of prompt parameters.
- `llm` -- an LLM judge reviews eval failures and rewrites the system prompt to address them.

**Q: Can I add custom eval metrics?**
A: The `eval_config.metrics` field accepts standard ADK eval metrics (`tool_trajectory`, `response_match`). Custom metrics can be added by extending the evaluation pipeline.

**Q: How do I use my own agent instead of the example?**
A: Create a new directory under `agents/` with an `__init__.py` that exports an `agent` object, then set `agent_module` in your manifest to point to it (e.g., `agents.my_agent`).

# Function Tools Template

Minimal template for optimizing an ADK agent with function tools.

## Quick Start

```bash
# 1. Copy this template to your working directory
cp -r templates/function_tools/ my_agent/
cd my_agent/

# 2. Replace the example tools in agent.py with your own

# 3. Edit eval_cases.yaml with real queries for your agent

# 4. Inspect to verify tool discovery
wrangler inspect .

# 5. Generate GEPA evalset from your eval cases
wrangler generate-evalset --from eval_cases.yaml --output my_agent_opt/ -n 15

# 6. Run optimization
uv run python -c "
from wrangler.optimizer import optimize
result = optimize('my_agent_opt/', sampler_config_path='my_agent_opt/sampler_config.json')
print(result)
"

# 7. Deploy and evaluate
wrangler deploy manifest.yaml
wrangler eval manifest.yaml --engine-id <your-engine-id>
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Agent definition with `create_agent()` factory |
| `__init__.py` | Module exports (`agent.root_agent` + `create_agent`) |
| `manifest.yaml` | Wrangler manifest pointing to this agent |
| `eval_cases.yaml` | Eval cases with expected tool calls |

## Agent Export Pattern

Wrangler discovers your agent using one of these patterns (checked in order):

1. `create_agent(model, instruction)` — **recommended**. Factory function that accepts model and instruction strings.
2. `agent.root_agent` — SimpleNamespace wrapping an LlmAgent instance.
3. `root_agent` — LlmAgent directly.

The `create_agent()` pattern is preferred because wrangler can inject different models and prompts during optimization without modifying your agent module.

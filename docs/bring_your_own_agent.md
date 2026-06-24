# Bring Your Own Agent Guide

This guide shows how to use GEPA Prompt Wrangler with your existing ADK agent.

## Three Paths

| Path | Starting Point | What You Need |
|------|---------------|---------------|
| **A. Function tools** | Agent with Python function tools | Agent module + eval cases |
| **B. MCP tools** | Agent with MCP tool servers | Agent module + deployed MCP servers + eval cases |
| **C. Already deployed** | Agent running on Agent Engine | Engine ID + eval cases |

---

## Path A: Function Tools

Best for agents that use Python functions as tools (the most common ADK pattern).

### 1. Set up your agent module

Your agent directory needs an `__init__.py` that exports a `create_agent` factory:

```python
# my_agent/__init__.py
import types
from .agent import create_agent, root_agent

agent = types.SimpleNamespace(root_agent=root_agent)
```

```python
# my_agent/agent.py
from google.adk.agents import Agent

def my_tool(param: str) -> dict:
    """Does something useful."""
    return {"result": "..."}

TOOLS = [my_tool]

def create_agent(model: str = "gemini-3.5-flash", instruction: str = "You are helpful.") -> Agent:
    from wrangler.core.config import resolve_model
    return Agent(
        model=resolve_model(model),
        name="my_agent",
        instruction=instruction,
        tools=TOOLS,
    )

root_agent = create_agent()
```

### 2. Generate manifest and eval skeleton

```bash
wrangler init --agent-dir my_agent/ -o manifest.yaml
```

This auto-detects your agent's tools and generates:
- `manifest.yaml` — pre-populated with agent name, model, and instruction
- `eval_cases.yaml` — skeleton with correct tool names and parameter hints

### 3. Fill in eval cases

Edit `eval_cases.yaml` — replace the TODO placeholders:

```yaml
eval_cases:
  - prompt: "Look up product SKU-123"
    expected_response: "Product SKU-123 is available at $29.99."
    expected_tools:
      - name: my_tool
        args: {param: SKU-123}
```

### 4. Generate GEPA evalset

```bash
wrangler generate-evalset --from eval_cases.yaml --output my_agent_opt/ -n 15
```

### 5. Run optimization

```bash
uv run python -c "
from wrangler.optimize.optimizer import optimize
result = optimize('my_agent_opt/', sampler_config_path='my_agent_opt/sampler_config.json')
print(result)
"
```

### 6. Deploy and evaluate

```bash
wrangler deploy manifest.yaml
wrangler eval manifest.yaml --engine-id <your-engine-id>
```

---

## Path B: MCP Tools

For agents that connect to MCP tool servers (FastMCP on Cloud Run, etc.).

### Tool Name Convention

MCP tools get prefixed by the server name. The pattern is:

```
{server_name}_{tool_function_name}
```

Hyphens become underscores. Examples:

| Server | Tool | Eval Name |
|--------|------|-----------|
| `my-api` | `search` | `my_api_search` |
| `booking-mcp` | `book_flight` | `booking_mcp_book_flight` |

### 1. Set up your agent

```python
from google.adk.agents import Agent
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, SseServerParams

mcp_tools = McpToolset(
    connection_params=SseServerParams(url="https://my-server.run.app/sse"),
)

def create_agent(model="gemini-3.5-flash", instruction="You are helpful."):
    from wrangler.core.config import resolve_model
    return Agent(
        model=resolve_model(model),
        name="my_mcp_agent",
        instruction=instruction,
        tools=[mcp_tools],
    )
```

### 2. Discover tool names

```bash
wrangler inspect my_agent/
```

Look for the "Tool names for eval cases" section — it shows the prefixed names.

### 3. Write eval cases with prefixed names

```yaml
eval_cases:
  - prompt: "Search for flights to JFK"
    expected_response: "Found 3 flights to JFK."
    expected_tools:
      - name: my_api_search_flights  # prefixed!
        args: {destination: JFK}
```

### 4. Generate evalset and optimize

Same as Path A steps 4-6.

---

## Path C: Already Deployed

For agents already running on Agent Engine — skip deployment, just evaluate and optimize.

### Option 1: Standalone eval (no manifest)

```bash
wrangler eval --engine-id 4981388556929859584 --eval-data eval_cases.yaml
```

### Option 2: Manifest with engine_id

```yaml
# manifest.yaml
pairs:
  - id: my-deployed-agent
    model: gemini-3.5-flash
    engine_id: "4981388556929859584"  # skip deploy phase
    system_prompt: "..."
```

The pipeline will skip deployment for pairs with `engine_id` set and go straight to evaluation.

---

## Agent Export Patterns

Wrangler discovers your agent using these patterns (checked in order):

| Pattern | Description | When to Use |
|---------|-------------|-------------|
| `create_agent(model, instruction)` | Factory function | **Recommended** — wrangler injects model/prompt |
| `agent.root_agent` | SimpleNamespace wrapper | Backward compat with existing agents |
| `root_agent` | Direct LlmAgent | Simplest, but wrangler overwrites model/instruction |

### Error: "Could not load agent"

If you see this error, check that your `__init__.py` exports one of the patterns above. Run:

```bash
wrangler inspect my_agent/
```

The error message lists what your module actually exports to help you fix it.

---

## Troubleshooting

### Tool name mismatches

**Symptom:** GEPA optimization runs but scores are low; agent uses different tool names than evalset expects.

**Fix:** Run `wrangler inspect my_agent/` and use the exact names shown in the "Tool names for eval cases" section.

### GEPA validation errors

**Symptom:** Pydantic ValidationError during optimization.

**Fix:** Check that your evalset JSON has the correct structure. Regenerate with:
```bash
wrangler generate-evalset --from eval_cases.yaml --output my_agent_opt/ -n 15
```

### Import errors in agent module

**Symptom:** `ModuleNotFoundError` when loading your agent.

**Fix:** Ensure your agent's dependencies are installed in the wrangler virtualenv:
```bash
uv add <your-dependency>
```

### MCP connection failures

**Symptom:** Agent loads but MCP tools show as "unknown" or fail at runtime.

**Fix:** Ensure the MCP server URL is accessible and the server is running. Set the URL via environment variable:
```bash
export MCP_SERVER_URL="https://my-server.run.app/sse"
```

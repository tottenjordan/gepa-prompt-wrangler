# MCP Tools Template

Template for optimizing an ADK agent that uses MCP tool servers.

## Quick Start

```bash
# 1. Copy this template
cp -r templates/mcp_tools/ my_agent/
cd my_agent/

# 2. Set your MCP server URL
export MCP_SERVER_URL="https://my-mcp-server.run.app/sse"

# 3. Edit agent.py — update the MCP connection params

# 4. Inspect to discover tool names (including MCP prefix)
wrangler inspect .

# 5. Edit eval_cases.yaml with real queries using the prefixed tool names

# 6. Generate GEPA evalset
wrangler generate-evalset --from eval_cases.yaml --output my_agent_opt/ -n 15

# 7. Run optimization
uv run python -c "
from wrangler.optimizer import optimize
result = optimize('my_agent_opt/', sampler_config_path='my_agent_opt/sampler_config.json')
print(result)
"
```

## MCP Tool Name Convention

MCP tools registered via Agent Registry get prefixed:

```
{server_name}_{tool_function_name}
```

Hyphens in server names are replaced with underscores.

| Server Name | Tool Function | Eval Name |
|-------------|---------------|-----------|
| `my-api-server` | `search_items` | `my_api_server_search_items` |
| `booking-mcp` | `book_flight` | `booking_mcp_book_flight` |

Run `wrangler inspect .` to see the exact names your agent will use.

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Agent definition with MCP toolset and `create_agent()` factory |
| `__init__.py` | Module exports |
| `manifest.yaml` | Wrangler manifest |
| `eval_cases.yaml` | Eval cases with prefixed MCP tool names |

## Agent Registry vs Direct URL

- **Direct URL**: Set `MCP_SERVER_URL` to your Cloud Run service URL
- **Agent Registry**: Use `McpToolset(connection_params=AgentRegistryParams(resource_name="projects/.../services/my-server"))` for auto-discovery

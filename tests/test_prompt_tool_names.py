"""Every tool a prompt names must be a tool the agent actually has.

The prompts had drifted to `wrangler_search_mcp_search_hotels` while the
deployed toolset serves plain `search_hotels`: `McpToolset` is constructed
without `tool_name_prefix` (see `_REGISTRY_PY_TEMPLATE` in
`wrangler/core/deploy.py`), so ADK applies no prefix. Confirmed against the
deployed engine's own startup log, which prints the live tool list:

    [GEAP startup] MCP OK: ... -> 2 tools ['search_flights', 'search_hotels']

This is not cosmetic. Naming a tool that is not in the model's declarations is
a documented trigger for `MALFORMED_FUNCTION_CALL` / `UNEXPECTED_TOOL_CALL`,
where the model invents a call for the tool it was told about and the request
is rejected. All six agents were shipping prompts in that state.

Both sides are derived, never hardcoded: the tool names come from the MCP
servers' `@mcp.tool()` functions and the prompts from the registry files, so
adding a tool or a prompt version keeps this honest automatically.
"""

import ast
import re
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "multi_model_agents"
MCP_SERVERS = EXAMPLE / "mcp_servers"
PROMPTS = EXAMPLE / "prompts"

# Verbs that start a tool name here. Used only to spot *candidate* tool
# references in prose, so a false positive is a test failure to investigate
# rather than a silent pass.
_TOOL_SHAPED = re.compile(r"\b[a-z_]*(?:search|book|cancel|get|list|submit|check)_[a-z_]+\b")


def _real_tool_names() -> set[str]:
    """Tool names as the MCP servers define them, read from the AST.

    A function is a tool if it carries an `@mcp.tool()` decorator.
    """
    names: set[str] = set()
    for server in sorted(MCP_SERVERS.glob("*/server.py")):
        tree = ast.parse(server.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                func = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(func, ast.Attribute) and func.attr == "tool":
                    names.add(node.name)
    return names


def _prompt_versions() -> list[tuple[str, str, str]]:
    """Every (module, version, prompt) in the prompt registry."""
    out = []
    for mod in sorted(PROMPTS.glob("*_prompts.py")):
        namespace: dict = {}
        exec(compile(mod.read_text(), str(mod), "exec"), namespace)  # noqa: S102
        for version, entry in (namespace.get("OPTIMIZED") or {}).items():
            if isinstance(entry, dict) and entry.get("prompt"):
                out.append((mod.name, version, entry["prompt"]))
        if namespace.get("GENERIC"):
            out.append((mod.name, "GENERIC", namespace["GENERIC"]))
    return out


def test_the_mcp_servers_actually_define_tools():
    """Guards the guard: an empty tool set would make everything below vacuous."""
    tools = _real_tool_names()
    assert len(tools) >= 8, f"only found {tools} — did the decorator or layout change?"
    assert "search_hotels" in tools


def test_there_are_prompts_to_check():
    versions = _prompt_versions()
    assert len(versions) >= 20, f"only {len(versions)} prompts found — is the path right?"


@pytest.mark.parametrize(("module", "version", "prompt"), _prompt_versions())
def test_prompt_names_only_real_tools(module, version, prompt):
    real = _real_tool_names()
    referenced = set(_TOOL_SHAPED.findall(prompt))
    unknown = sorted(referenced - real)
    assert not unknown, (
        f"{module}:{version} names tools that do not exist: {unknown}. "
        f"The deployed toolset uses bare names ({sorted(real)[:3]}...) because "
        f"McpToolset sets no tool_name_prefix."
    )


def _real_parameters() -> set[str]:
    """Every parameter name any tool accepts, from the AST."""
    params: set[str] = set()
    for server in sorted(MCP_SERVERS.glob("*/server.py")):
        tree = ast.parse(server.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                func = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(func, ast.Attribute) and func.attr == "tool":
                    params.update(a.arg for a in node.args.args)
    return params


def test_declared_arguments_exist():
    """An `**Arguments:**` list must not invent parameters.

    `pro_prompts.py` declared `search_flights` as taking `return_date`,
    `min_price` and `airline` on top of the three real ones. A model told about
    a parameter will try to pass it, and the call fails schema validation —
    same failure class as naming a tool that does not exist, one level down.
    """
    real = _real_parameters()
    offenders = []
    for mod in sorted(PROMPTS.glob("*_prompts.py")):
        for line in mod.read_text().splitlines():
            if "**Arguments:**" not in line:
                continue
            offenders.extend(
                f"{mod.name}: {name}"
                for name in re.findall(r"`([a-z_]+)`", line)
                if name not in real
            )
    assert not offenders, f"prompts declare parameters no tool accepts: {sorted(set(offenders))}"


def test_no_server_prefixed_tool_names_anywhere():
    """The specific drift that caused this: a `<server>_mcp_` prefix on a tool.

    Caught separately from the parametrised test because a prefixed name is
    unambiguous evidence of the old convention, wherever it appears — including
    in prose that the verb heuristic above might not match.
    """
    offenders = []
    for mod in sorted(PROMPTS.glob("*.py")):
        for i, line in enumerate(mod.read_text().splitlines(), 1):
            if re.search(r"\b(?:wrangler_)?(?:search|booking|expense)_mcp\w*", line):
                offenders.append(f"{mod.name}:{i}")
    assert not offenders, f"server-prefixed tool names remain: {offenders}"


EVAL_DATA = EXAMPLE / "eval_data"
_STALE_PREFIX = re.compile(r"\b(?:wrangler_)?(?:search|booking|expense)_mcp\w*")


def _eval_yaml_files():
    return sorted(EVAL_DATA.glob("*.yaml"))


def _evalset_files():
    return sorted((EXAMPLE / "agents").glob("*/*.evalset.json"))


def test_there_is_eval_data_to_check():
    assert _eval_yaml_files(), "no eval YAML found — path wrong?"
    assert _evalset_files(), "no generated evalsets found — path wrong?"


@pytest.mark.parametrize("path", _eval_yaml_files(), ids=lambda p: p.name)
def test_eval_data_expects_real_tools(path):
    """`expected_tools` is the golden trajectory. If it names a tool the agent
    cannot call, a correct agent is scored as wrong — the same inverted
    incentive as the tool_use_quality floor in silent-failures.md #4."""
    stale = sorted(set(_STALE_PREFIX.findall(path.read_text())))
    assert not stale, f"{path.name} expects tools that do not exist: {stale}"


@pytest.mark.parametrize("path", _evalset_files(), ids=lambda p: p.parent.name)
def test_generated_evalsets_expect_real_tools(path):
    """These are what GEPA actually optimizes against.

    Generated by scripts/generate_evalsets.py from eval_cases.yaml, so a stale
    name here means either the source drifted or the files were not regenerated
    after it was fixed. Both are worth failing on.
    """
    stale = sorted(set(_STALE_PREFIX.findall(path.read_text())))
    assert not stale, f"{path.parent.name} golden trajectory names missing tools: {stale}"


def test_eval_data_tool_names_match_the_servers():
    """Cross-check the goldens against the MCP servers, not just the prefix.

    The prefix test above catches the known drift; this catches a golden that
    names something plausible but nonexistent, e.g. a renamed or removed tool.
    """
    real = _real_tool_names()
    offenders = []
    for path in _eval_yaml_files():
        offenders.extend(
            f"{path.name}: {name}"
            for name in re.findall(r"^\s*- name:\s*([a-z_]+)\s*$", path.read_text(), re.MULTILINE)
            if name not in real
        )
    assert not offenders, f"eval data names non-existent tools: {sorted(set(offenders))}"

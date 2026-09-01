"""Three pairs of code are kept in sync by hand. This makes drift a red build.

The GEAP build package is flat and the GEAP server has no `wrangler` installed,
so a deployed agent genuinely cannot import it. `build_source_package()` copies
`examples/multi_model_agents/config.py` into `_geap_build_pkg/`, which makes
*that* file — not `wrangler/core/models.py` — the model-routing code that
actually runs on every deployed agent. CLAUDE.md states the consequence: "A fix
applied only to `wrangler/core/` will appear to work locally in the CLI and
still ship broken to every deployed agent."

Until now the only thing enforcing agreement was a docstring saying "Kept in
sync". Three pairs, no tests:

    A  wrangler/core/models.py  <->  examples/multi_model_agents/config.py
       is_regional_model, model_location, resolve_model
    B  _REGISTRY_PY_TEMPLATE (a string in deploy.py)  <->  examples/.../registry.py
    C  the MCP tool-list cache TTL, written as a literal in one and a named
       constant in the other

Pair A is compared by **behaviour**, not by source. The two already differ in
docstrings and comments, so any textual or AST comparison would need an
allowlist of acceptable phrasings that grows with every edit and eventually
gets widened to make a real difference pass. Running the same inputs through
both and comparing what comes out cares about exactly the thing that matters.

Pair B cannot be imported — one side is a string — so it is parsed with `ast`
and checked on the invariants the two must share. Not on being identical: they
are deliberately different runtimes (the generated one uses ADC + Agent
Registry, the local one direct Cloud Run URLs).
"""

from __future__ import annotations

import ast
import importlib.util
import pathlib
import re

import pytest

from wrangler.core import models as wrangler_models

REPO = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = REPO / "examples" / "multi_model_agents" / "config.py"
EXAMPLE_REGISTRY = REPO / "examples" / "multi_model_agents" / "registry.py"
DEPLOY_PY = REPO / "wrangler" / "core" / "deploy.py"

# The functions duplicated across pair A. `test_no_shared_function_escapes_the_guard`
# fails if a new one appears in both files without being added here.
SHARED_FUNCTIONS = ("is_regional_model", "model_location", "resolve_model")


def load_example_config():
    """Exec `examples/multi_model_agents/config.py` as an anonymous module.

    It is not importable as a package from here. A fresh exec each call also
    means module-level env captures are re-read, which is what lets one test
    below distinguish an import-time read from a call-time one.
    """
    spec = importlib.util.spec_from_file_location("_example_config_probe", EXAMPLE_CONFIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Every registered id, plus the shapes the registry does not contain: a
# regional Gemini 2.x, the `models/` prefix form, and something unknown.
MODEL_IDS = [
    *sorted(wrangler_models.MODELS),
    "gemini-2.0-flash",
    "models/gemini-2.0-flash",
    "definitely-not-a-model",
]


def describe(resolved) -> tuple:
    """Reduce a resolved model to (type, model id, pinned location).

    Comparing the objects directly would compare pydantic instances, which are
    unequal even when they carry the same routing. These three fields are the
    routing.
    """
    if isinstance(resolved, str):
        return ("str", resolved, None)
    name = type(resolved).__name__
    model_id = getattr(resolved, "model", None)
    location = None
    kwargs = getattr(resolved, "client_kwargs", None)
    if isinstance(kwargs, dict):
        location = kwargs.get("location")
    elif isinstance(model_id, str) and "/locations/" in model_id:
        location = model_id.split("/locations/", 1)[1].split("/", 1)[0]
    return (name, model_id, location)


@pytest.fixture(scope="module")
def example_config():
    return load_example_config()


class TestTheTwoConfigsAgree:
    """Pair A, compared on what the functions do rather than how they read."""

    @pytest.mark.parametrize("model_id", MODEL_IDS)
    def test_is_regional_model_agrees(self, example_config, model_id):
        assert wrangler_models.is_regional_model(model_id) == example_config.is_regional_model(
            model_id
        )

    @pytest.mark.parametrize("model_id", MODEL_IDS)
    def test_model_location_agrees(self, example_config, model_id):
        assert wrangler_models.model_location(model_id) == example_config.model_location(model_id)

    @pytest.mark.parametrize("model_id", MODEL_IDS)
    def test_resolve_model_agrees_on_type_id_and_location(self, example_config, model_id):
        """The three fields that decide which endpoint the request reaches."""
        assert describe(wrangler_models.resolve_model(model_id)) == describe(
            example_config.resolve_model(model_id)
        )

    def test_the_global_location_constant_agrees(self, example_config):
        assert wrangler_models.GLOBAL_LOCATION == example_config.GLOBAL_LOCATION

    def test_the_vertex_truthy_set_agrees(self, example_config):
        """A mismatch here silently drops one side into API-key mode."""
        assert wrangler_models._VERTEX_TRUTHY == example_config._VERTEX_TRUTHY

    def test_no_shared_function_escapes_the_guard(self, example_config):
        """A new duplicated function must be added to SHARED_FUNCTIONS.

        Without this, the guard quietly stops covering the pair as it grows,
        while still reading like coverage.
        """
        in_both = {
            name
            for name in dir(example_config)
            if not name.startswith("_")
            and callable(getattr(example_config, name, None))
            and callable(getattr(wrangler_models, name, None))
        }
        # Names re-exported from elsewhere (os.path helpers, dotenv) are not
        # duplicated logic; only compare things defined in the example file.
        defined_here = {
            n.name
            for n in ast.parse(EXAMPLE_CONFIG.read_text()).body
            if isinstance(n, ast.FunctionDef)
        }
        uncovered = (in_both & defined_here) - set(SHARED_FUNCTIONS)
        assert not uncovered, (
            f"{sorted(uncovered)} exist in both config files but are not compared. "
            f"Add them to SHARED_FUNCTIONS and give them a parametrized test."
        )


class TestEnvIsReadAtCallTimeOnBothSides:
    """Pair A's one real divergence: import-time capture vs call-time read.

    `wrangler/core/models.py` reads `GCP_REGION` and `GCP_PROJECT_ID` from the
    environment inside the function. The example config bound them to module
    constants at import (lines 13-15), so a value set *after* import was
    ignored on the deployed side only.

    That ordering is not hypothetical: `wrangler/pipeline/components.py` sets
    `os.environ["GCP_PROJECT_ID"]` and `["GCP_REGION"]` inside the component
    body, after the tarball is extracted, and the agent's config may already
    have been imported by then.
    """

    def test_a_region_set_after_import_is_still_seen(self, monkeypatch):
        cfg = load_example_config()  # import first
        monkeypatch.setenv("GCP_REGION", "europe-west4")  # change after
        assert wrangler_models.model_location("gemini-2.0-flash") == "europe-west4"
        assert cfg.model_location("gemini-2.0-flash") == "europe-west4"

    def test_a_project_set_after_import_is_still_seen(self, monkeypatch):
        cfg = load_example_config()
        monkeypatch.setenv("GCP_PROJECT_ID", "project-set-late")
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "1")
        assert describe(wrangler_models.resolve_model("claude-sonnet-4-6")) == describe(
            cfg.resolve_model("claude-sonnet-4-6")
        )
        assert "project-set-late" in describe(cfg.resolve_model("claude-sonnet-4-6"))[1]


# --- Pair B/C: the generated registry template vs the local registry --------


def extract_template(name: str) -> str:
    """Pull a triple-quoted template constant out of wrangler/core/deploy.py."""
    src = DEPLOY_PY.read_text()
    match = re.search(rf"{name}\s*=\s*[a-z]*(\"{{3}}|'{{3}})(.*?)\1", src, re.DOTALL)
    assert match, f"{name} not found in {DEPLOY_PY.name}"
    return match.group(2)


def module_constants(tree: ast.Module) -> dict:
    """Module-level `NAME = <literal>` assignments."""
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def call_keywords(tree: ast.AST, func_name: str) -> dict:
    """Keyword arguments of the first call to `func_name`, literals only."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == func_name:
                return {
                    kw.arg: kw.value.value
                    for kw in node.keywords
                    if isinstance(kw.value, ast.Constant)
                }
    return {}


def keyword_value(tree: ast.AST, keyword: str):
    """First literal value passed as `keyword=` anywhere in the tree.

    Some of the values that must agree sit on a nested call --
    `sse_read_timeout` is a keyword of the connection params inside
    `McpToolset(...)`, not of `McpToolset` itself.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == keyword and isinstance(kw.value, ast.Constant):
                    return kw.value.value
    raise AssertionError(f"no literal `{keyword}=` found")


def has_keyword(tree: ast.AST, func_name: str, keyword: str) -> bool:
    """True if any call to `func_name` passes `keyword`, commented-out or not.

    Checked on the AST rather than by substring: grepping for the name matches
    a line that is commented out, and would pass while the real call drifted.
    """
    return any(
        kw.arg == keyword
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) == func_name
        for kw in node.keywords
    )


def mcp_env_names(tree: ast.AST) -> set:
    """Every `*_MCP_*` string passed to an `os.environ.get`-shaped call."""
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", None) == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and "_MCP_" in node.args[0].value
    }


class TestTheGeneratedRegistryTracksTheLocalOne:
    """Pair B and C.

    `_REGISTRY_PY_TEMPLATE` is a string, so it cannot be imported and compared
    by behaviour the way the config pair is. It is valid Python though, so it
    is parsed and checked on the invariants the two must share -- not on being
    identical, which they deliberately are not: the generated one authenticates
    with ADC against direct Cloud Run URLs, the local one reads config.py.
    """

    def test_the_template_is_valid_python(self):
        """A corrupted template fails at container import, minutes into a deploy."""
        ast.parse(extract_template("_REGISTRY_PY_TEMPLATE"))

    def test_all_three_templates_are_valid_python(self):
        for name in ("_APP_PY_TEMPLATE", "_APP_PY_TEMPLATE_NO_TOOLS", "_REGISTRY_PY_TEMPLATE"):
            ast.parse(extract_template(name)), name

    def test_neither_registry_sets_a_tool_name_prefix(self):
        """Prompts name bare tools; a prefix makes every one of them undeclared.

        `tests/test_prompt_tool_names.py` documents the failure -- prompts had
        drifted to `wrangler_search_mcp_search_hotels` while the deployed
        toolset serves `search_hotels`, and naming an undeclared tool is a
        documented trigger for MALFORMED_FUNCTION_CALL. That test cites this
        template in a docstring but never asserted on it.
        """
        template = ast.parse(extract_template("_REGISTRY_PY_TEMPLATE"))
        local = ast.parse(EXAMPLE_REGISTRY.read_text())
        assert not has_keyword(template, "McpToolset", "tool_name_prefix")
        assert not has_keyword(local, "McpToolset", "tool_name_prefix")

    def test_the_tool_list_cache_ttl_agrees(self):
        """deploy.py:569 says "Keep in sync with examples/.../registry.py"."""
        template = call_keywords(ast.parse(extract_template("_REGISTRY_PY_TEMPLATE")), "McpToolset")
        local = module_constants(ast.parse(EXAMPLE_REGISTRY.read_text()))
        assert template["tool_list_cache_ttl_seconds"] == local["MCP_TOOL_LIST_CACHE_TTL_SECONDS"]

    def test_the_read_timeout_agrees(self):
        """`sse_read_timeout` sits on the nested connection params, not on McpToolset."""
        template = ast.parse(extract_template("_REGISTRY_PY_TEMPLATE"))
        local = module_constants(ast.parse(EXAMPLE_REGISTRY.read_text()))
        assert keyword_value(template, "sse_read_timeout") == local["MCP_READ_TIMEOUT_SECONDS"]

    def test_the_startup_probe_budget_matches_the_local_handshake_timeout(self):
        """deploy.py:276 says it "Matches ... MCP_TIMEOUT_SECONDS -- keep the two in step".

        The probe decides whether a fresh engine is judged able to reach its
        MCP servers. Set below the handshake budget it would fail workers that
        were merely slow, which is the fake defect the 30s value produced.
        """
        app = ast.parse(extract_template("_APP_PY_TEMPLATE"))
        probe_default = next(
            node.args[1].value
            for node in ast.walk(app)
            if isinstance(node, ast.Call)
            and getattr(node.func, "attr", None) == "get"
            and len(node.args) == 2
            and getattr(node.args[0], "value", None) == "MCP_PROBE_TIMEOUT_SECONDS"
        )
        local = module_constants(ast.parse(EXAMPLE_REGISTRY.read_text()))
        assert float(probe_default) == float(local["MCP_TIMEOUT_SECONDS"])

    def test_the_template_knows_every_mcp_service_config_declares(self):
        """A fourth MCP service added to config.py must reach the generated registry.

        The local registry reads its URLs from config.py, so adding a service
        there works locally and silently ships an agent that cannot see it.
        """
        template_vars = mcp_env_names(ast.parse(extract_template("_REGISTRY_PY_TEMPLATE")))
        config_vars = mcp_env_names(ast.parse(EXAMPLE_CONFIG.read_text()))
        missing = config_vars - template_vars
        assert not missing, (
            f"config.py declares {sorted(missing)} but the generated registry.py "
            f"never reads them, so a deployed agent cannot reach those servers"
        )

"""Every Vertex client must be an `agentplatform.Client`.

`google-cloud-aiplatform` 2.1.0 deprecates `vertexai.Client` in favour of
`agentplatform.Client`, and the FutureWarning fired on our own code rather than
inside a library. Seven call sites constructed their own client; they now go
through `wrangler.core.clients.agent_client`.

These tests are the reason a future "tidy-up" cannot quietly put one back.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

from wrangler.core.clients import agent_client


def test_the_factory_returns_an_agentplatform_client():
    client = agent_client(project="test-project", location="us-central1")
    assert type(client).__module__.startswith("agentplatform"), (
        f"got {type(client).__module__}; vertexai.Client is deprecated at aiplatform 2.1.0"
    )


def test_constructing_a_client_emits_no_deprecation_warning():
    """The literal ask. A FutureWarning here means the migration did not take."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent_client(project="test-project", location="us-central1")
    bad = [
        w
        for w in caught
        if "deprecated" in str(w.message).lower() and "vertexai" in str(w.message).lower()
    ]
    assert not bad, [str(w.message) for w in bad]


def test_explicit_arguments_win_over_the_environment(monkeypatch):
    """Callers hold their own module constants and the suite patches those.

    A factory that read the environment unconditionally would ignore
    `@patch("wrangler.core.deploy.GCP_PROJECT_ID", ...)` and reach the real
    project from a unit test.
    """
    monkeypatch.setenv("GCP_PROJECT_ID", "env-project")
    client = agent_client(project="explicit-project", location="us-central1")
    assert "explicit-project" in str(client._api_client.project)


def test_the_environment_is_read_at_call_time(monkeypatch):
    """KFP components set these inside the component body, after import."""
    monkeypatch.setenv("GCP_PROJECT_ID", "late-bound-project")
    client = agent_client()
    assert "late-bound-project" in str(client._api_client.project)


def _calls_vertexai_client(path: Path) -> list[int]:
    """Line numbers where this module constructs `vertexai.Client(...)`.

    AST-based rather than a text search: a comment or docstring naming
    `vertexai.Client` is legitimate -- this file and `clients.py` both do it --
    and a regex cannot tell prose from code. Same reason `test_models.py`
    walks the AST for model-id literals.
    """
    tree = ast.parse(path.read_text(), filename=path.as_posix())

    # Both spellings. Six of the original seven call sites used the bare form
    # via `from vertexai import Client`, so matching only the dotted one finds
    # a single offender and reads as success.
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "vertexai":
            aliases.update(a.asname or a.name for a in node.names if a.name == "Client")

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        dotted = (
            isinstance(func, ast.Attribute)
            and func.attr == "Client"
            and isinstance(func.value, ast.Name)
            and func.value.id == "vertexai"
        )
        bare = isinstance(func, ast.Name) and func.id in aliases
        if dotted or bare:
            hits.append(node.lineno)
    return hits


def test_no_module_constructs_vertexai_client_directly():
    offenders = []
    for root in ("wrangler", "scripts"):
        for path in sorted(Path(root).rglob("*.py")):
            offenders.extend(f"{path}:{line}" for line in _calls_vertexai_client(path))
    assert not offenders, (
        f"{offenders} construct vertexai.Client directly. Use "
        f"wrangler.core.clients.agent_client() -- vertexai.Client is deprecated "
        f"at google-cloud-aiplatform 2.1.0."
    )


def test_deploy_goes_through_runtimes_not_agent_engines():
    """`agent_engines` is the surface whose `create()` already changed once.

    At aiplatform 2.1.0 the module-level `vertexai.agent_engines.create` lost
    `source_packages`, `requirements_file`, `entrypoint_module`,
    `entrypoint_object`, `class_methods`, `agent_framework` and `labels`. We
    survived that only because deploy used the client surface. `runtimes` is
    the agentplatform equivalent, and its config type is field-identical --
    measured, not assumed -- so the config dict is unchanged.
    """
    source = Path("wrangler/core/deploy.py").read_text()
    tree = ast.parse(source, filename="wrangler/core/deploy.py")
    hits = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "agent_engines"
    ]
    assert not hits, (
        f"wrangler/core/deploy.py:{hits} still reaches .agent_engines. Use "
        f".runtimes -- see docs/notes/vertex-sdk-surfaces.md."
    )


def test_the_guard_can_actually_see_a_violation(tmp_path):
    """A guard that never fires is not known to work."""
    bad = tmp_path / "bad.py"
    bad.write_text("import vertexai\nc = vertexai.Client(project='p', location='l')\n")
    assert _calls_vertexai_client(bad) == [2]

    bare = tmp_path / "bare.py"
    bare.write_text("from vertexai import Client\nc = Client(project='p')\n")
    assert _calls_vertexai_client(bare) == [2], "the bare `from vertexai import Client` form"

    other = tmp_path / "other.py"
    other.write_text("from agentplatform import Client\nc = Client(project='p')\n")
    assert _calls_vertexai_client(other) == [], "must not flag the agentplatform Client"

    fine = tmp_path / "fine.py"
    fine.write_text('"""Mentions vertexai.Client in prose only."""\n# vertexai.Client(...)\n')
    assert _calls_vertexai_client(fine) == []

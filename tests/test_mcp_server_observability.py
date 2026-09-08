"""The local MCP servers must not swallow their own output.

Campaign 07's optimize stage lost its MCP toolset four times in 39 GEPA
generations (~10%). Each time the agent evaluated with **no tools**, scored near
zero on tool use, and that score entered the objective GEPA optimises -- while
the same agent scored 0.969 on `tool_use_quality_v1` in eval-before.

The cause could not be established, and the reason is here: the three servers
are `subprocess.Popen` children with `stdout=DEVNULL, stderr=DEVNULL`, so
whatever they said as they went unresponsive was discarded. Liveness was polled
**once**, five seconds after start, and never again.

What *was* established (docs/notes/silent-failures.md #12): the client waited
the full 120s `MCP_TIMEOUT_SECONDS` on a localhost server that answers in 0.1s
when healthy, so the server was wedged rather than slow. Distinguishing a crash
from CPU starvation needs the server's own output, which is what these tests
require the component to keep.

These are structural. `@dsl.component` bodies are serialised in isolation and
cannot be imported and executed by a unit test, so this file reads the source --
the same approach as `test_no_component_reads_the_optimize_stage_unguarded`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

COMPONENTS = Path("wrangler/pipeline/components.py")


def _optimize_body() -> str:
    """Source of the optimize component, where the MCP servers are started."""
    src = COMPONENTS.read_text()
    start = src.index("def optimize_single_agent")
    return src[start:]


def _code_only(text: str) -> str:
    """Strip `#` comments so prose about DEVNULL is not mistaken for code.

    The Dockerfile guards learned this the hard way: a comment explaining a past
    mistake matched the pattern for the mistake itself.
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_the_component_still_starts_the_local_servers():
    """Guards every test below. If this block moves, they go vacuously green."""
    body = _optimize_body()
    assert "Started local MCP server" in body
    assert "subprocess.Popen" in body


def test_mcp_server_output_is_not_discarded():
    """DEVNULL here is why silent-failures #12 could not be diagnosed."""
    body = _code_only(_optimize_body())
    m = re.search(r"subprocess\.Popen\((.*?)\n\s*\)", body, re.DOTALL)
    assert m, "could not locate the Popen call; the guard needs updating"
    call = m.group(1)
    assert "DEVNULL" not in call, (
        "the MCP servers' stdout/stderr go to DEVNULL, so when one wedges there "
        "is no record of why. Capture them to a file or the logger instead."
    )


def test_a_toolset_failure_reports_which_servers_are_dead():
    """`p.poll()` ran once, 5s after start, and never again.

    A toolset load failure is exactly the moment to ask whether the server is
    still alive -- a dead one means crash, a live one means starvation, and the
    two need different fixes.
    """
    body = _code_only(_optimize_body())
    assert body.count("poll()") >= 2, (
        "liveness is checked only at startup. Check it again when a toolset "
        "fails, so a crash can be told apart from CPU starvation."
    )


def test_the_server_logs_are_reachable_after_the_run():
    """Captured output is worthless if it dies with the container.

    The optimize container is torn down when the stage ends, so a log written
    only to local disk is gone before anyone reads it. It has to reach GCS or
    the component's own logger, both of which survive.
    """
    body = _optimize_body()
    assert re.search(r"mcp[_-]?server[_-]?log|mcp_logs|server_log", body, re.IGNORECASE), (
        "no named destination for the servers' output; it must be recoverable after the stage ends"
    )


def test_the_guard_can_see_a_planted_violation(tmp_path):
    """A guard never observed firing is not known to work."""
    planted = "proc = subprocess.Popen(\n    [sys.executable],\n    stdout=subprocess.DEVNULL,\n)"
    m = re.search(r"subprocess\.Popen\((.*?)\n\s*\)", _code_only(planted), re.DOTALL)
    assert m is not None
    assert "DEVNULL" in m.group(1)

    clean = "proc = subprocess.Popen(\n    [sys.executable],\n    stdout=log_fh,\n)"
    m2 = re.search(r"subprocess\.Popen\((.*?)\n\s*\)", _code_only(clean), re.DOTALL)
    assert m2 is not None
    assert "DEVNULL" not in m2.group(1)


def test_the_component_body_still_parses():
    """Everything above reads source; a syntax error would make it meaningless."""
    ast.parse(COMPONENTS.read_text())

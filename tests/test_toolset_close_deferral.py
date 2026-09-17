"""Patch 8: a runner's close() must not tear down a toolset other candidates share.

Silent failure #12, open since 2026-09-08 and measured on 2026-09-12: GEPA drives N
short-lived runners against **one** `McpToolset` per server, because `agent.clone()` is a
shallow copy. `Runner.close()` calls `_cleanup_toolsets`, which calls `toolset.close()` --
1,812 times in a single 572-minute stage. A close issued by one candidate's runner tears
down the session another candidate is mid-`list_tools()` on; the victim blocks for the full
120s `MCP_TIMEOUT_SECONDS`, ADK hands the agent zero tools, and GEPA scores a toolless
candidate at near-zero on tool use.

**Three fixes shipped for #12 before this one and the rate did not move** -- 14% (c07), 15%
(c08 with PR #59), 12% and 24% (c08's two arms with PR #75). Two of them passed their own
tests. So these tests are written against the production topology the earlier reproduction
under-modelled, and they are still not the acceptance test:

    scripts/repro_mcp_refresh_hang.py raced ONE close against ONE get_tools() and measured
    0/18 hangs at the production cache setting, appearing to exonerate close-racing. It
    under-modelled both the volume (a burst of ~32) and the sharing (one toolset, many
    runners), so its negative result was about the reproduction, not about production.

**The acceptance test is the `will run without the tools` rate on a real optimize stage**,
counted with `scripts/analyze_toolset_loss.py`, expected 0 against a 12-24% baseline. What
these tests establish is narrower and worth stating exactly: that a burst of closes against
a shared toolset no longer reaches the session manager, and that the session survives the
burst with an in-flight reader still able to use it.
"""

from __future__ import annotations

import asyncio

import pytest

from wrangler.optimize import optimizer


class FakeSessionManager:
    """Stands in for `_mcp_session_manager`. Records teardowns; serves tools."""

    def __init__(self) -> None:
        self.close_calls = 0
        self.alive = True

    async def close(self) -> None:
        self.close_calls += 1
        self.alive = False


class FakeToolset:
    """A shared `McpToolset`, reduced to the two things close() touches.

    `close()` mirrors ADK's real one: clear the tool-list cache, then close the session
    manager. Both matter -- the cache clear is what makes the *next* get_tools() reach a
    session that the close just killed.
    """

    def __init__(self) -> None:
        self._mcp_session_manager = FakeSessionManager()
        self._tool_list_cache = {"search": object()}

    async def close(self) -> None:
        self._tool_list_cache.clear()
        await self._mcp_session_manager.close()

    async def get_tools(self, readonly_context=None):
        """Raises if the session was torn down -- the production failure, in miniature."""
        await asyncio.sleep(0)
        if not self._mcp_session_manager.alive:
            raise ConnectionError("Failed to get tools from MCP server: ")
        return ["search"]


@pytest.fixture
def toolset_cls(monkeypatch):
    """Patch target, isolated per test so the class patch cannot leak between them."""
    monkeypatch.setattr(optimizer, "_toolset_class_for_patching", lambda: FakeToolset)
    optimizer._reset_toolset_close_patch_for_tests()
    yield FakeToolset
    optimizer._reset_toolset_close_patch_for_tests()


class TestCloseIsDeferred:
    pytestmark = pytest.mark.asyncio

    async def test_a_close_inside_the_window_does_not_reach_the_session(self, toolset_cls):
        ts = FakeToolset()
        async with optimizer._deferred_toolset_closes():
            await ts.close()
            assert ts._mcp_session_manager.close_calls == 0, (
                "a runner's close() reached the shared session manager; this is the "
                "1,812-closes-per-stage teardown that silent-failures #12 measured"
            )
            assert ts._tool_list_cache, "the tool-list cache was cleared inside the window"

    async def test_the_session_survives_a_burst_and_stays_usable(self, toolset_cls):
        """The production shape: ~32 closes in a burst, a reader in flight across it."""
        ts = FakeToolset()
        async with optimizer._deferred_toolset_closes():

            async def reader():
                for _ in range(20):
                    assert await ts.get_tools() == ["search"]
                    await asyncio.sleep(0)

            async def closer():
                for _ in range(32):
                    await ts.close()
                    await asyncio.sleep(0)

            await asyncio.gather(reader(), closer(), closer())
            assert await ts.get_tools() == ["search"]

    async def test_the_flush_closes_each_toolset_exactly_once(self, toolset_cls):
        """Deferred is not cancelled -- the sessions must still be torn down at the end."""
        a, b = FakeToolset(), FakeToolset()
        async with optimizer._deferred_toolset_closes():
            for _ in range(50):
                await a.close()
                await b.close()
            assert a._mcp_session_manager.close_calls == 0
        assert a._mcp_session_manager.close_calls == 1, "50 deferred closes must flush as 1"
        assert b._mcp_session_manager.close_calls == 1

    async def test_a_toolset_never_closed_is_not_closed_by_the_flush(self, toolset_cls):
        """The flush closes what was asked for, not everything it can reach."""
        untouched = FakeToolset()
        async with optimizer._deferred_toolset_closes():
            pass
        assert untouched._mcp_session_manager.close_calls == 0


class TestTheWindowIsScoped:
    pytestmark = pytest.mark.asyncio

    async def test_close_works_normally_outside_the_window(self, toolset_cls):
        """Deferral is run-scoped. Outside it, close() must be a real close."""
        ts = FakeToolset()
        async with optimizer._deferred_toolset_closes():
            pass
        await ts.close()
        assert ts._mcp_session_manager.close_calls == 1

    async def test_the_window_is_exited_even_if_the_run_raises(self, toolset_cls):
        ts = FakeToolset()

        async def failing_run() -> None:
            async with optimizer._deferred_toolset_closes():
                await ts.close()
                raise RuntimeError("optimize blew up")

        with pytest.raises(RuntimeError, match="optimize blew up"):
            await failing_run()
        assert ts._mcp_session_manager.close_calls == 1, "flush must run on the error path"
        await ts.close()
        assert ts._mcp_session_manager.close_calls == 2, "deferral must not outlive the run"

    async def test_a_failing_flush_does_not_mask_the_run_and_still_tries_the_rest(
        self, toolset_cls
    ):
        """One toolset refusing to close must not strand the others or the caller."""
        good = FakeToolset()
        bad = FakeToolset()

        async def explode() -> None:
            raise OSError("session already gone")

        bad._mcp_session_manager.close = explode
        async with optimizer._deferred_toolset_closes():
            await bad.close()
            await good.close()
        assert good._mcp_session_manager.close_calls == 1


class TestPatchHygiene:
    pytestmark = pytest.mark.asyncio

    async def test_nesting_does_not_restore_the_patch_early(self, toolset_cls):
        ts = FakeToolset()
        async with optimizer._deferred_toolset_closes():
            async with optimizer._deferred_toolset_closes():
                await ts.close()
            assert ts._mcp_session_manager.close_calls == 0, (
                "the inner window's exit flushed while the outer window was still open"
            )
            await ts.close()
            assert ts._mcp_session_manager.close_calls == 0
        assert ts._mcp_session_manager.close_calls == 1

    async def test_the_real_close_is_restored_on_the_class(self, toolset_cls):
        original = FakeToolset.close
        async with optimizer._deferred_toolset_closes():
            assert FakeToolset.close is not original
        assert FakeToolset.close is original, "the class patch outlived the run"


class TestItPatchesTheRealADKClass:
    """The fixture swaps in a fake, so something must check the real target exists."""

    def test_the_patch_target_is_mcptoolset_and_it_defines_close(self):
        from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

        assert optimizer._toolset_class_for_patching() is McpToolset
        assert "close" in McpToolset.__dict__, (
            "McpToolset stopped defining close(); patching it would now shadow or miss "
            "the real teardown. Re-run the probe in docs/notes/adk-patch-status.md."
        )

    def test_runner_close_still_calls_into_toolset_close(self):
        """The whole patch is pointless if Runner.close() stops closing toolsets."""
        import inspect

        from google.adk.runners import Runner

        assert "_cleanup_toolsets" in inspect.getsource(Runner.close)
        assert "toolset.close()" in inspect.getsource(Runner._cleanup_toolsets), (
            "Runner no longer closes toolsets directly -- re-derive silent-failures #12's "
            "mechanism before trusting this patch"
        )


class TestTheRealProductionPath:
    """No fakes: a real `Runner.close()` against a real shared `McpToolset`.

    Every other test here substitutes `FakeToolset`, so they verify the deferral logic
    and not that it engages on the path that actually loses tools in production. This one
    builds the real topology -- one `McpToolset`, N short-lived runners over
    `agent.clone()` -- and needs no network: nothing ever connects, and closing a session
    manager that never opened is a no-op.

    It also re-derives the diagnosis rather than trusting it: if `agent.clone()` ever
    stops sharing the toolset, the first assertion fails and silent-failures #12's
    mechanism needs revisiting before this patch is trusted.
    """

    pytestmark = pytest.mark.asyncio

    async def test_runner_close_does_not_tear_down_the_shared_session(self):
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools.mcp_tool import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

        from wrangler.core.models import DEFAULT_JUDGE_MODEL

        optimizer._reset_toolset_close_patch_for_tests()
        toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(url="http://127.0.0.1:59999/mcp")
        )
        teardowns = {"n": 0}
        real_close = toolset._mcp_session_manager.close

        async def counting_close():
            teardowns["n"] += 1
            await real_close()

        toolset._mcp_session_manager.close = counting_close
        agent = LlmAgent(name="shared", model=DEFAULT_JUDGE_MODEL, tools=[toolset])

        def fresh_runner():
            return Runner(
                app_name="t", agent=agent.clone(), session_service=InMemorySessionService()
            )

        assert fresh_runner().agent.tools[0] is toolset, (
            "agent.clone() no longer shares the toolset -- silent-failures #12's "
            "mechanism depends on that sharing; re-derive it before trusting patch 8"
        )

        async with optimizer._deferred_toolset_closes():
            for _ in range(20):
                await fresh_runner().close()
            assert teardowns["n"] == 0, (
                f"{teardowns['n']} real Runner.close() calls tore down the shared session "
                f"inside the window -- this is the production failure, unfixed"
            )
        assert teardowns["n"] == 1, "the session must still be closed once, at teardown"

    async def test_the_close_log_lines_survive_deferral(self):
        """`Closing toolset` in a log no longer means a toolset was closed.

        ADK's `_cleanup_toolsets` logs `Closing toolset`, awaits the close, then logs
        `Successfully closed toolset`. A deferred close returns cleanly, so **both lines
        are still emitted at full volume** while nothing is torn down.

        Pinned because it is exactly the observation that would mislead the next person
        reading an acceptance run: a post-fix stage still shows ~1,812 `Closing toolset`
        events, and `analyze_toolset_loss.py` will still find the 90-150s burst -- now
        harmless. The signals are the deferred-close count and the failure count, not the
        close count. (It cuts the other way too: that script treats *zero* closes as a
        broken query rather than a clean run, and this keeps that guard working.)
        """
        import logging

        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools.mcp_tool import McpToolset
        from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

        from wrangler.core.models import DEFAULT_JUDGE_MODEL

        optimizer._reset_toolset_close_patch_for_tests()
        captured: list[str] = []

        class Capture(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        adk_runner_log = logging.getLogger("google_adk.google.adk.runners")
        handler = Capture()
        previous_level = adk_runner_log.level
        adk_runner_log.setLevel(logging.INFO)
        adk_runner_log.addHandler(handler)
        try:
            toolset = McpToolset(
                connection_params=StreamableHTTPConnectionParams(url="http://127.0.0.1:59999/mcp")
            )
            teardowns = {"n": 0}
            real_close = toolset._mcp_session_manager.close

            async def counting_close():
                teardowns["n"] += 1
                await real_close()

            toolset._mcp_session_manager.close = counting_close
            agent = LlmAgent(name="shared", model=DEFAULT_JUDGE_MODEL, tools=[toolset])

            async with optimizer._deferred_toolset_closes():
                for _ in range(5):
                    await Runner(
                        app_name="t",
                        agent=agent.clone(),
                        session_service=InMemorySessionService(),
                    ).close()
                assert teardowns["n"] == 0
                assert sum("Closing toolset" in m for m in captured) == 5, (
                    "ADK stopped logging Closing toolset per close -- analyze_toolset_loss.py "
                    "counts that line, and its zero-closes-is-an-error guard depends on it"
                )
        finally:
            adk_runner_log.removeHandler(handler)
            adk_runner_log.setLevel(previous_level)
            optimizer._reset_toolset_close_patch_for_tests()

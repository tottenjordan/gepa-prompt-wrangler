"""Resolve the dependency sets before a campaign spends twenty minutes on them.

Campaign 07's validation arm died twice in a GEAP build. The second time:

    Cannot install -r ./_geap_build_pkg/requirements.txt (line 6) and
    google-adk[a2a,agent-identity,eval,mcp]==2.8.0 because these package
    versions have conflicting dependencies.
      google-adk[...] 2.8.0 depends on jinja2<4 and >=3.1.4; extra == "eval"
      litellm 1.96.2 depends on jinja2<4.0 and >=3.1.6

Every test in test_pipeline_image_pins.py is *static* -- it compares declared
strings to installed versions. A transitive conflict two levels down is
invisible to all of them, and the one test that does catch this specific pair
is a tombstone for that incident, not a general check.

The resolver is injected here so these tests stay hermetic. The suite is
"green, fast, and hermetic (no network, no GCP)" per
docs/notes/toolchain-baseline.md, and that is worth more than testing the
subprocess call.
"""

from __future__ import annotations

from wrangler.tools.preflight import (
    agent_requirements,
    check_requirements,
    image_requirements,
    run_preflight,
)

_CONFLICT = (
    "ERROR: ResolutionImpossible\n"
    "  google-adk[eval] 2.8.0 depends on jinja2<4 and >=3.1.4\n"
    "  litellm 1.96.2 depends on jinja2<4.0 and >=3.1.6"
)


def test_a_conflict_is_reported_with_the_offending_packages():
    result = check_requirements(
        ["litellm>=1.96.2"], "3.11", name="agent", resolver=lambda r, p: (1, _CONFLICT)
    )
    assert not result.ok
    assert "jinja2" in result.detail, "the resolver's own text must survive verbatim"
    assert "litellm" in result.detail


def test_a_clean_resolve_passes():
    result = check_requirements(
        ["click"], "3.11", name="agent", resolver=lambda r, p: (0, "Resolved 1 package")
    )
    assert result.ok
    assert result.name == "agent"


def test_the_python_version_reaches_the_resolver():
    """The whole litellm incident turned on a python>=3.14 marker."""
    seen = {}

    def spy(reqs, python_version):
        seen["py"] = python_version
        return 0, ""

    check_requirements(["click"], "3.14", name="x", resolver=spy)
    assert seen["py"] == "3.14"


def test_the_agent_set_is_read_from_deploy_not_duplicated():
    """A second copy of the requirements would drift from the real one."""
    from wrangler.core.deploy import _SOURCE_REQUIREMENTS

    assert agent_requirements() == list(_SOURCE_REQUIREMENTS)


def test_the_image_set_is_read_from_the_dockerfile():
    pins = image_requirements()
    assert any(p.startswith("google-adk") for p in pins), pins
    assert all("==" in p for p in pins), "a floor here would make the check meaningless"


def test_the_image_set_ignores_commented_out_lines():
    """Same trap as the Dockerfile guards: a comment naming a version is prose."""
    assert not any(p.lstrip().startswith("#") for p in image_requirements())


def test_run_preflight_checks_both_sets():
    results = run_preflight(resolver=lambda r, p: (0, "ok"))
    assert {r.name for r in results} == {"agent requirements", "pipeline image pins"}
    assert all(r.ok for r in results)


def test_run_preflight_surfaces_a_failure():
    results = run_preflight(resolver=lambda r, p: (1, _CONFLICT))
    assert results, "must not report success by checking nothing"
    assert all(not r.ok for r in results)
    assert all("jinja2" in r.detail for r in results)

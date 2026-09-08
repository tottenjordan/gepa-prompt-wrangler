# Three repo improvements: close the guard's blind spot, surface the online-eval CLI, gate on resolution

> **For Claude:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task.
> On execution, copy this file to `docs/plans/2026-09-08-repo-improvements.md` and commit it — plan mode could only write to the scratch plan path.

**Goal:** Close the three highest-value gaps found by auditing today's incidents — a version-blind pin guard, an undiscoverable command surface with a wholly broken guide, and the absence of any check that our dependency sets actually resolve.

**Architecture:** Three independent tasks, no shared code, each testable and revertable alone. Tasks 1 and 2 are pure additions to existing patterns. Task 3 adds one new module plus a call site.

**Tech Stack:** Python 3.11, `uv`, `pytest`, `ruff`, `ty`, `click`.

---

## Context

Campaign 07 has now been killed twice by dependency problems, and twice today the full test suite stayed green while the real code path was broken. Each of the three tasks below traces to a specific incident, not to a style preference.

### Why Task 1

`tests/test_pipeline_image_pins.py` compares each Dockerfile's pins to
`importlib.metadata.version(...)` — the **local venv, which is Python 3.11.15**.
Since merging #42–#44, the three MCP images build on **`python:3.14-slim`**
while `Dockerfile.pipeline` stays on `3.11-slim`. **No test constrains a base
image at all** (verified: `grep` for `FROM python` / `base_image` across
`tests/` returns nothing).

The pins happen to resolve on both today — I checked — but nothing enforces
that, and the guard reports success either way. This is the same defect class
the guard was written for: it hashes and compares one thing while the container
builds another. A future dependabot base-image bump lands with the guard green.

### Why Task 2

`docs/online_eval_guide.md` contains **14 references to
`python -m wrangler.online_evaluators`**, a module that does not exist — every
documented command in that guide fails with `No module named`. The real path is
`wrangler.eval.online_evaluators`, moved during the package reorganization.

Behind that path sits a **second, parallel CLI**: a `COMMANDS` dict
(`online_evaluators.py:626`) with seven commands — `list`, `create`, `verify`,
`trace-health`, `prune`, `delete`, `cleanup` — **none of which `wrangler`
exposes.** `wrangler --help` lists 18 commands and none of them reach this.
`trace-health` in particular diagnoses the OTel span-drop failure in
`docs/notes/silent-failures.md` #8, and nobody reading `--help` would find it.

This is also the standing "wire `wrangler evaluators prune`" item, which turns
out to be one of seven, not one.

### Why Task 3

Campaign 07's validation arm died on:

```
Cannot install -r ./_geap_build_pkg/requirements.txt (line 6) and
google-adk[a2a,agent-identity,eval,mcp]==2.8.0 because these package
versions have conflicting dependencies.
  google-adk[...] 2.8.0 depends on jinja2<4 and >=3.1.4; extra == "eval"
  litellm 1.96.2 depends on jinja2<4.0 and >=3.1.6
```

**All eleven tests in `test_pipeline_image_pins.py` are static** — they compare
declared strings to installed versions. None of them *resolve* anything, so a
transitive conflict two levels down is invisible to every one of them.
`test_litellm_stays_capped_below_the_jinja2_conflict` is a tombstone for that
one incident, not a general check; the next conflict will be a different pair
of packages and will land exactly the same way.

Cost of not having it: ~20 minutes of GEAP build, three retries, one dead
campaign arm, and an error message that says only
`Build failed ... or other dependencies`. Cost of having it: about a second.

**The check cannot be a unit test.** Resolving needs network, and
`docs/notes/toolchain-baseline.md:16` calls the suite "green, fast, and
hermetic (no network, no GCP) — the repo's strongest asset". Breaking that to
add this would be a bad trade.

---

## Task 1: Make the pin guard aware of the Python it is validating against

**Files:** Modify `tests/test_pipeline_image_pins.py`

The guard's premise is "the container runs what we tested". That premise
silently fails when the container's Python differs from the venv the versions
were read from. Make the mismatch explicit rather than checking resolution
(which is Task 3's job and needs network).

**Step 1 — write the failing tests.**

```python
# Reuse the existing comment-stripping helper; do not re-parse raw text.
def _base_image(path: Path) -> str:
    """The `FROM` tag, e.g. 'python:3.14-slim'."""
    for line in _instructions(path).splitlines():
        if line.strip().upper().startswith("FROM "):
            return line.split()[1]
    raise AssertionError(f"{path} has no FROM line")


def test_every_image_declares_a_python_base():
    for path in [DOCKERFILE, *OTHER_DOCKERFILES]:
        assert _base_image(path).startswith("python:"), path


def test_pins_are_only_verified_against_a_matching_python():
    """A pin checked against 3.11 says nothing about a 3.14 image.

    Markers make this concrete rather than theoretical: uv.lock holds two
    litellm entries, 1.85.7 for python<3.14 and 1.96.2 for >=3.14, and
    aiplatform[evaluation] flips its litellm range at exactly that boundary.
    So an image whose base moved is an image whose correct pins may have
    moved, while `metadata.version()` keeps reporting the local 3.11 answer
    and every pin test keeps passing.
    """
    import sys
    local = f"{sys.version_info.major}.{sys.version_info.minor}"
    mismatched = {
        path.as_posix(): _base_image(path)
        for path in [DOCKERFILE, *OTHER_DOCKERFILES]
        if not _base_image(path).startswith(f"python:{local}")
    }
    assert not mismatched, (
        f"{mismatched} build on a Python other than the local {local}, so "
        f"test_each_pin_matches_the_lockfile is comparing their pins against "
        f"versions resolved for the wrong interpreter. Either align the base "
        f"image, or add the path to PYTHON_MISMATCH_ACCEPTED with a reason "
        f"and evidence the pin set resolves on its own base."
    )
```

**Step 2 — run it. It must FAIL**, naming the three MCP Dockerfiles on
`python:3.14-slim`. If it passes, the test is wrong — check `_base_image`.

**Step 3 — implement the escape hatch, deliberately narrow.** Add above the
tests:

```python
# Images whose base Python differs from the local venv, accepted with evidence.
# This is an escape hatch, not a default: an entry means somebody resolved that
# image's pin set on its own base and recorded the result. `wrangler preflight`
# (Task 3) is how you produce that evidence.
PYTHON_MISMATCH_ACCEPTED = {
    "examples/multi_model_agents/mcp_servers/search/Dockerfile": (
        "python:3.14-slim via dependabot #44, 2026-09-08. Pin set resolved on "
        "3.14 and the server imports, registers both tools and initialises "
        "OTel there; the deployed rev 00008 build log confirms fastmcp 3.4.7 "
        "and mcp 1.30.0 installed on that base."
    ),
    # ... booking (#42) and expense (#43), same evidence, same date
}
```

Filter `mismatched` by this dict. Keep `Dockerfile.pipeline` **out** of it —
that is the image running the ADK monkey-patches, and it must stay on the
Python the patch probe was run against.

**Step 4 — verify:** `uv run pytest tests/test_pipeline_image_pins.py -q --no-cov`
green. Then temporarily change `Dockerfile.pipeline` to `python:3.13-slim`,
confirm the test **fails**, and revert. A guard never seen firing is not known
to work.

**Step 5 — commit:** `test: the pin guard was blind to the Python it validates against`.

---

## Task 2: Put the seven online-eval commands in the CLI, and fix the guide

**Files:** Modify `wrangler/cli.py` · Modify `wrangler/eval/online_evaluators.py` (`COMMANDS`/`__main__` only) · Modify `docs/online_eval_guide.md` · Test `tests/test_cli_evaluators.py` (new)

**Reuse the existing pattern.** `cli.py:206-232` already defines an
`engines` group with `list` and `prune` subcommands — same shape, same
`--yes`-to-act convention. Copy it; do not invent a second style.

**Step 1 — write the failing tests.**

```python
from click.testing import CliRunner
from wrangler.cli import main

EXPECTED = {"list", "create", "verify", "trace-health", "prune", "delete", "cleanup"}


def test_the_evaluators_group_exposes_every_command():
    """The module's COMMANDS dict is the source of truth for what exists."""
    from wrangler.eval.online_evaluators import COMMANDS
    assert set(COMMANDS) == EXPECTED, "COMMANDS changed; update this test deliberately"

    out = CliRunner().invoke(main, ["evaluators", "--help"])
    assert out.exit_code == 0
    missing = {c for c in EXPECTED if c not in out.output}
    assert not missing, f"{missing} exist in the module but not in `wrangler evaluators`"


def test_prune_is_dry_run_without_yes():
    """Matches `engines prune`. Deleting by default is how you lose an evaluator."""
    out = CliRunner().invoke(main, ["evaluators", "prune", "--help"])
    assert "--yes" in out.output


def test_the_guide_only_references_importable_modules():
    """docs/online_eval_guide.md had 14 refs to a module deleted in the
    package reorganisation, so every documented command failed."""
    import importlib.util, re
    from pathlib import Path
    text = Path("docs/online_eval_guide.md").read_text()
    bad = [
        m for m in set(re.findall(r"python -m (wrangler[\w.]*)", text))
        if importlib.util.find_spec(m) is None
    ]
    assert not bad, f"{bad} are documented but do not exist"
```

**Step 2 — run, watch all three fail** (no `evaluators` group; 14 stale refs).

**Step 3 — implement.** Add an `evaluators` group to `cli.py` mirroring
`engines_group`, one subcommand per entry in `COMMANDS`, importing from
`.eval.online_evaluators` **inside** each function (the file's existing
convention — keeps `--help` fast). Give `prune` a `--yes` flag and dry-run
default; `delete` takes an `evaluator_id` argument.

**Step 4 — make the old entry point a shim.** Rewrite the `__main__` block to
dispatch into the click group, so there is one implementation:

```python
if __name__ == "__main__":
    # Kept working on purpose -- docs, notes and muscle memory use it. It is a
    # shim over the click group, not a second implementation; two
    # implementations drifting is what produced a guide where all 14 commands
    # were wrong.
    import sys
    from wrangler.cli import main
    main(["evaluators", *sys.argv[1:]])
```

**Step 5 — fix the guide.** Replace all 14 `python -m wrangler.online_evaluators`
with `uv run wrangler evaluators ...`, and add one line noting the module form
still works. Check for the same rot in `wrangler.online_monitors`, which the
sweep also found missing.

**Step 6 — verify:** full suite green; `uv run wrangler evaluators --help` lists
seven; `uv run python -m wrangler.eval.online_evaluators list` still works.

**Step 7 — commit:** `feat: the seven online-eval commands were unreachable from the CLI`.

---

## Task 3: Resolve the dependency sets before spending a campaign on them

**Files:** Create `wrangler/tools/preflight.py` · Modify `wrangler/cli.py` · Modify `scripts/validate_then_run.py` · Test `tests/test_preflight.py` (new)

**Step 1 — write the failing tests.** Test the *parsing and verdict* logic
hermetically; the resolver itself is injected, so no test touches the network.

```python
def test_a_conflict_is_reported_with_the_offending_packages():
    def fake_resolver(reqs, python_version):
        return 1, "ERROR: ResolutionImpossible\n  litellm 1.96.2 depends on jinja2>=3.1.6"
    result = check_requirements(["litellm>=1.96.2"], "3.11", resolver=fake_resolver)
    assert not result.ok
    assert "jinja2" in result.detail


def test_a_clean_resolve_passes():
    result = check_requirements(["click"], "3.11", resolver=lambda r, p: (0, "Resolved 1 package"))
    assert result.ok


def test_the_agent_set_is_read_from_deploy_not_duplicated():
    """A second copy of the requirements would drift from the real one."""
    from wrangler.core.deploy import _SOURCE_REQUIREMENTS
    assert agent_requirements() == list(_SOURCE_REQUIREMENTS)


def test_the_image_set_is_read_from_the_dockerfile():
    pins = image_requirements()
    assert any(p.startswith("google-adk") for p in pins)
    assert all("==" in p for p in pins), "floors would make this check meaningless"
```

**Step 2 — run, watch them fail.**

**Step 3 — implement `wrangler/tools/preflight.py`.** Two requirement sets, one
resolver:

- `agent_requirements()` → `list(_SOURCE_REQUIREMENTS)` from
  `wrangler/core/deploy.py`. **Import it; never copy it.**
- `image_requirements()` → the `==` pins from `Dockerfile.pipeline`. Reuse the
  parsing already written in `tests/test_pipeline_image_pins.py` (`_instructions`
  / `_pins`) by lifting those two helpers into this module and having the test
  import them, rather than maintaining two parsers.
- `check_requirements(reqs, python_version, resolver=...)` → default resolver
  shells `uv pip install --dry-run --python-version <v> <reqs>` and returns
  `(returncode, output)`.
- `run_preflight()` → checks both sets at **3.11** (GEAP's runtime and the
  image's base) and returns a list of results.

Surface the resolver's own error text verbatim. The GEAP message
(`Build failed ... or other dependencies`) is useless precisely because it
paraphrases; do not repeat that mistake.

**Step 4 — add `wrangler preflight`** to `cli.py`, non-zero exit on failure.

**Step 5 — gate the campaign.** In `scripts/validate_then_run.py:main`, call
`run_preflight()` before the first `submit(...)` and abort on failure:

```python
    # Both previous c07 launches died in a GEAP build on an unresolvable
    # requirements set, ~20 minutes in, reported only as
    # "Build failed ... or other dependencies". This costs about a second.
    failures = [r for r in run_preflight() if not r.ok]
    if failures:
        for r in failures:
            print(f"\nPREFLIGHT FAILED ({r.name}):\n{r.detail}")
        print("\nNot submitting. Fix the requirements before spending a campaign.")
        return 1
```

Put it on the `--watch-job` path too — that branch releases six arms and
deserves the same gate.

**Step 6 — verify.** `uv run wrangler preflight` passes on `main`. Then
temporarily set `litellm>=1.96.2` in `_SOURCE_REQUIREMENTS`, confirm preflight
**fails naming jinja2**, and revert. That reproduces the exact c07 killer and is
the only evidence that matters here.

**Step 7 — commit:** `feat: resolve the requirement sets before a campaign, not during it`.

---

## Verification

1. `uv run pytest tests/ -q --no-cov` green (1144+ today), plus `ruff check`,
   `ruff format --check`, `ty check wrangler/`.
2. **The suite is still hermetic.** `uv run pytest tests/ -q -p no:cacheprovider`
   with the network unavailable must still pass — Task 3's resolver is injected
   in tests and only the CLI path shells out. This is a hard constraint, not a
   nicety.
3. **Each guard is seen to fail.** Task 1: flip `Dockerfile.pipeline`'s base,
   confirm red, revert. Task 3: set `litellm>=1.96.2`, confirm preflight names
   jinja2, revert.
4. `uv run wrangler evaluators --help` lists all seven; `--help` on the group is
   fast (imports stay inside the functions).
5. Every command in `docs/online_eval_guide.md` runs.
6. `uv run wrangler preflight` completes in about a second.

## Risks

- **The escape hatch in Task 1 becomes a dumping ground.** It exists so a
  deliberate mismatch can be recorded with evidence; the moment entries appear
  without evidence it is worse than no test. `LITERAL_EXCEPTIONS` in
  `test_models.py` is kept empty for the same reason and is the precedent.
- **`uv pip install --dry-run` may not match pip's resolution on the GEAP
  builder.** It uses a different resolver, so preflight can pass where GEAP
  fails. It would have caught the litellm case (that conflict is real under
  both), but it reduces risk rather than eliminating it — say so in the
  command's help text rather than implying a guarantee.
- **Preflight needs network at campaign-launch time.** A PyPI outage would then
  block a launch that would otherwise work. Provide `--skip-preflight` on
  `validate_then_run.py` so the gate can be overridden deliberately, and log
  loudly when it is.
- **Task 2 touches a live diagnostic surface.** `trace-health` and `prune` act
  on real evaluators; the `--yes` default must be dry-run, matching
  `engines prune`.

## Out of scope

- Campaign 07 itself — running, gated, monitored under task `b4usutpzn`.
- Filing the empty-stream escalation (`docs/escalations/2026-08-23-...`) — ready,
  needs a human to file it.
- The patch-1 gap on `RubricContent`/`RubricScore` — recorded in
  `docs/notes/adk-patch-status.md`, never observed firing; widening a
  monkey-patch needs its own evidence.
- Moving the build package's `AdkApp` to `agentplatform` — deliberately deferred.
- Pruning the 12 merged local branches and the stale `pr30` — tidy-up, no code.

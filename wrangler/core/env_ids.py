"""Keep the ``*_ENGINE_ID`` entries in ``.env`` in step with what is deployed.

Engine ids are never pinned in source (CLAUDE.md), but the example ``.env`` is
scratch space that several things read: the online evaluators pick their
targets from it, ``trace-health`` resolves names through it, and
``wrangler engines`` treats an id named there as *referenced* and refuses to
delete it.

So a redeploy that changes an id and does not update ``.env`` leaves evaluators
scoring a dead engine, trace-health reporting on the wrong one, and the prune
policy protecting a corpse. That is not hypothetical: 10 of 28 online
evaluators were pointing at deleted engines on 2026-08-31.

Rewrites the single matching line and nothing else. A .env rewritten by a
config library comes back reordered, unquoted and stripped of comments, which
is a worse problem than the drift it fixes.
"""

from __future__ import annotations

import re
from pathlib import Path

# The five model tiers the example deploys. Kept explicit rather than derived:
# this file should not invent an env var name from an arbitrary string.
ENGINE_LABELS = ("lite", "flash", "pro", "sonnet", "opus")

# Anchored to the repo root, not left as a bare relative string: `wrangler run`
# can be invoked from any cwd, and `set_engine_id` below `mkdir(parents=True)`s
# whatever directory the path resolves to. A relative default silently built a
# brand-new stub `<cwd>/examples/multi_model_agents/.env` on a health-gate
# reroll, printed "Updated ... ->", and left the real .env stale -- exactly
# the drift this module exists to prevent. This file lives at
# wrangler/core/env_ids.py, so parents[2] is the repo root.
DEFAULT_ENV_PATH = str(
    Path(__file__).resolve().parents[2] / "examples" / "multi_model_agents" / ".env"
)


def env_var_for(label: str) -> str:
    return f"{label.upper()}_ENGINE_ID"


def read_engine_ids(path: str = DEFAULT_ENV_PATH) -> dict[str, str]:
    """Label -> engine id for every tier with a non-empty entry."""
    text = _read(path)
    out = {}
    for label in ENGINE_LABELS:
        m = re.search(rf"^{env_var_for(label)}=(.*)$", text, re.MULTILINE)
        if m and m.group(1).strip():
            out[label] = m.group(1).strip()
    return out


def set_engine_id(label: str, engine_id: str, path: str = DEFAULT_ENV_PATH) -> bool:
    """Point ``label`` at ``engine_id``. Returns True if the file changed.

    Only an *uncommented* assignment is replaced; a commented-out line is left
    alone, since those are documentation of the alternative form rather than
    stale values.
    """
    var = env_var_for(label)
    text = _read(path)
    pattern = re.compile(rf"^{var}=(.*)$", re.MULTILINE)

    match = pattern.search(text)
    if match:
        if match.group(1).strip() == engine_id:
            return False
        new = pattern.sub(f"{var}={engine_id}", text, count=1)
    else:
        new = text + ("" if text.endswith("\n") or not text else "\n") + f"{var}={engine_id}\n"

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(new)
    return True


def _read(path: str) -> str:
    p = Path(path)
    return p.read_text() if p.is_file() else ""

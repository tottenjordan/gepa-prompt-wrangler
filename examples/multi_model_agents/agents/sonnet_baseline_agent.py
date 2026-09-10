"""Campaign 08 baseline arm — the *same* agent as `sonnet_agent`, different criteria.

Campaign 08 asks whether weighting instruction-adherence 3/4 (PR #69) removes the
instruction-following regression campaign 07 reproduced on two model families. That needs
two GEPA criteria conditions running concurrently, and the criteria are selected by agent
module name: `components.py` turns `<stem>_agent` into `<stem>_opt/sampler_config.json`.

So the baseline condition needs its own module name, and nothing else. This re-exports
`sonnet_agent` rather than copying its definition, because a copy is a second thing to keep
in sync and any drift in it becomes a second variable in a two-arm experiment. The deployed
artifact is unaffected either way -- `deploy.py` generates its own `app.py` and uses this
path only to locate `config.py` and to name the agent.

Delete this together with `sonnet_baseline_opt/` when campaign 08 reports.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from sonnet_agent import AGENT_DESCRIPTION, INSTRUCTION, sonnet_agent  # noqa: F401

sonnet_baseline_agent = sonnet_agent
root_agent = sonnet_agent

import types as _t

agent = _t.SimpleNamespace(root_agent=sonnet_agent)

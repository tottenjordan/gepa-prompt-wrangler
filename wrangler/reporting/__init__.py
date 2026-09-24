"""Reporting: charts, markdown reports, per-pair analysis, and the MDE arithmetic.

The three report generators are re-exported **lazily**. Eagerly importing them
pulls `analysis` -> `eval.evaluator` -> litellm and matplotlib, ~9 s and two
stderr warnings about a missing botocore, for anyone who touches any module in
this package. `inference` is pure arithmetic over floats and has no business
paying that: `scripts/partition_mde.py` prints a decision gate whose output is
byte-compared, and the eager chain put litellm's warnings into it.

PEP 562 keeps `from wrangler.reporting import generate_report` working
unchanged; submodule imports (`from wrangler.reporting import reporter`) never
went through here in the first place.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analysis import generate_all_charts
    from .report_sections import generate_agent_report, generate_comparison_report
    from .reporter import generate_report

_LAZY = {
    "generate_all_charts": ".analysis",
    "generate_agent_report": ".report_sections",
    "generate_comparison_report": ".report_sections",
    "generate_report": ".reporter",
}

__all__ = [
    "generate_agent_report",
    "generate_all_charts",
    "generate_comparison_report",
    "generate_report",
]


def __getattr__(name: str):
    """Import a re-exported report generator on first use."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module, __name__), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))

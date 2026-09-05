"""Hermes coordinator: async project coordination plugin for hermes-agent.

The package-level register(ctx) is the entrypoint upstream plugin discovery calls
(hermes_cli/plugins.py invokes register_fn(ctx) with a single positional argument
after plugin.yaml satisfies the manifest contract); the explicit-dependency form for
tests and alternative wiring lives in hermes_plugin.register_tools.
"""

from .hermes_plugin import HermesContext, register_tools, wire_runtime

__version__: str = "0.1.0"


def register(ctx: HermesContext) -> list[str]:
    """Wire the runtime SQLite store + system clock and register all tools with ctx."""
    members, checkins, settings, knowledge, clock = wire_runtime()
    return register_tools(
        ctx,
        members=members,
        checkins=checkins,
        settings=settings,
        clock=clock,
        knowledge=knowledge,
    )

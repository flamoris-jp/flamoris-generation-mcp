"""The co-hosted facade delegates its retained domain inspection to Controller."""

from flamoris_generation_controller.updater import inspect_domain
from flamoris_update_core.owner import ApplicationOwner
from flamoris_update_core.owner_cli import serve


def factory(config):
    return ApplicationOwner(config, "flamoris-generation-mcp", "1.0.0", inspect_domain)


def main():
    serve(factory)

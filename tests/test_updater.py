from flamoris_generation_controller.updater import inspect_domain as controller_inspect

from flamoris_generation_mcp.updater import inspect_domain


def test_facade_uses_the_controller_owner_without_a_second_runtime():
    assert inspect_domain is controller_inspect

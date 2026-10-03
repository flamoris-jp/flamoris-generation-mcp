"""Original authority and finite cleanup claims survive malformed/unknown outcomes."""

import asyncio
from contextlib import contextmanager

import pytest
from test_runtime_cleanup import active, cleanup, posts, record, runtime_port

from flamoris_generation_mcp.jobs import JobStore
from flamoris_generation_mcp.runtime_delegation import RuntimeStopReceipt


async def test_runtime_stop_claim_is_shared_and_wrong_receipt_cannot_change_run(stores):
    bridge, handle, _ = await active(stores, provider=False)
    calls, entered, release = [], asyncio.Event(), asyncio.Event()

    async def wrong(scope, admission):
        calls.append(admission)
        entered.set()
        await release.wait()
        return RuntimeStopReceipt(
            admission=admission.model_copy(update={"run": "2"}),
            accepted=True,
            applied=True,
            terminal=True,
        )

    owned = cleanup(bridge, port=runtime_port(cancel=wrong))
    first = asyncio.create_task(owned.stop_runtime_owned(handle.job_id))
    await entered.wait()
    assert (await owned.stop_runtime_owned(handle.job_id))["state"] == "handoff_possible"
    release.set()
    with pytest.raises(ValueError, match="outcome is unknown"):
        await first
    assert (await owned.stop_runtime_owned(handle.job_id))["state"] == "unknown"
    assert len(calls) == 1 and record(bridge.jobs, handle).admission.run == "1"
    assert bridge.jobs.activity()["busy"]


async def test_guard_denial_does_not_claim_stop_or_expose_private_reason(stores, fake):
    bridge, handle, scope = await active(stores)

    @contextmanager
    def denied(*_):
        raise PermissionError("private identity and credentials")
        yield

    path = scope.operations[0].path
    with pytest.raises(ValueError, match="authority is unavailable") as error:
        await cleanup(bridge, guard=denied).stop_provider_owned(handle.job_id, path)
    assert "private" not in str(error.value)
    assert record(bridge.jobs, handle).cleanup_window is None
    assert record(bridge.jobs, handle).operations[path].cleanup.stop is None
    assert not posts(fake, "/queue")


async def test_terminal_runtime_stop_receipt_cannot_be_followed_by_active_observation(stores):
    bridge, handle, _ = await active(stores, provider=False)

    async def terminal(scope, admission):
        return RuntimeStopReceipt(admission=admission, accepted=True, applied=True, terminal=True)

    owned = cleanup(bridge, port=runtime_port(cancel=terminal))
    await owned.stop_runtime_owned(handle.job_id)
    with pytest.raises(ValueError, match="conflicts"):
        await owned.inspect_runtime_owned(handle.job_id)
    assert record(bridge.jobs, handle).cleanup.observation is None
    assert record(bridge.jobs, handle).cleanup.stop.receipt.terminal
    assert bridge.jobs.activity()["busy"]


async def test_read_budget_is_not_recreated_after_restart(stores, fake):
    bridge, handle, scope = await active(stores)
    owned, path = cleanup(bridge), scope.operations[0].path
    for _ in range(4):
        await owned.inspect_provider_owned(handle.job_id, path)
    recovered = JobStore(
        bridge.jobs.workflows,
        bridge.jobs.providers,
        bridge.jobs.capabilities,
        bridge.jobs.output_dir,
    )
    before = len(fake.calls)
    with pytest.raises(ValueError, match="budget exhausted"):
        await cleanup(bridge, jobs=recovered).inspect_provider_owned(handle.job_id, path)
    assert len(fake.calls) == before and recovered.activity()["busy"]


async def test_inspection_journal_failure_never_calls_get(stores, fake, monkeypatch):
    bridge, handle, scope = await active(stores)
    before = len(fake.calls)

    def failed(*_):
        raise OSError("private filesystem detail")

    monkeypatch.setattr(bridge.jobs._authority, "write", failed)
    with pytest.raises(ValueError, match="journal outcome"):
        await cleanup(bridge).inspect_provider_owned(handle.job_id, scope.operations[0].path)
    assert len(fake.calls) == before and bridge.jobs.activity()["busy"]


@pytest.mark.parametrize("corrupt", ["count", "stop", "window", "route", "run", "reopen"])
async def test_corrupt_cleanup_journal_rejects_recovery_before_any_call(stores, fake, corrupt):
    bridge, handle, scope = await active(stores)
    owned, path = cleanup(bridge, port=runtime_port()), scope.operations[0].path
    await owned.inspect_provider_owned(handle.job_id, path)
    await owned.inspect_runtime_owned(handle.job_id)
    raw = bridge.jobs._authority.read("active.json")
    current = raw["active"]["runtime_delegation"]
    operation = current["operations"][path]
    if corrupt == "count":
        operation["cleanup"]["inspections"] = 5
    elif corrupt == "stop":
        operation["cleanup"]["stop"] = {"state": "observed", "receipt": None}
    elif corrupt == "window":
        current["cleanup_window"]["expires_at"] += 1
    elif corrupt == "route":
        operation["provider_id"] = ""
    elif corrupt == "run":
        current["cleanup"]["observation"]["admission"]["run"] = "2"
    else:
        current["closed"] = False
    bridge.jobs._authority.write("active.json", raw)
    before = len(fake.calls)
    with pytest.raises(ValueError, match="Invalid execution reservation"):
        JobStore(
            bridge.jobs.workflows,
            bridge.jobs.providers,
            bridge.jobs.capabilities,
            bridge.jobs.output_dir,
        )
    assert len(fake.calls) == before

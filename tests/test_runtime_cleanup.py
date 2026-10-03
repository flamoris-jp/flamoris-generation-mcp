"""Owned stop/observation never replays useful work or releases root/host debt."""

import asyncio
from contextlib import contextmanager, nullcontext
from dataclasses import replace

import pytest
from test_runtime_delegation import OTHER, TARGET, accepted, admit, bundle, invoke

from flamoris_generation_mcp.durable import CommitUnknown
from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.providers.base import JobSnapshot
from flamoris_generation_mcp.runtime_cleanup import RuntimeCleanupPort, RuntimeOwnedCleanup
from flamoris_generation_mcp.runtime_delegation import RuntimeObservation, RuntimeStopReceipt


def cleanup(bridge, *, jobs=None, guard=None, port=None, clock=lambda: 100.0):
    registrations = tuple(
        replace(x, cleanup_guard=guard or (lambda *args: nullcontext()))
        for x in bridge._registrations.values()
    )
    return RuntimeOwnedCleanup(jobs or bridge.jobs, registrations, runtime_port=port, clock=clock)


def runtime_port(*, cancel=None, inspect=None, guard=None, target=TARGET):
    async def stop(scope, admission):
        return RuntimeStopReceipt(admission=admission, accepted=True, applied=False, terminal=False)

    async def observe(scope, admission):
        return runtime_observation(admission)

    return RuntimeCleanupPort(
        target, guard or (lambda *_: nullcontext()), cancel or stop, inspect or observe
    )


def runtime_observation(admission, *, watermark="10", state="cancelling", **fields):
    return RuntimeObservation(
        admission=admission,
        watermark=watermark,
        state=state,
        dispatch_open=False,
        child_creation_open=False,
        execution_in_flight=fields.pop("execution_in_flight", False),
        cleanup_pending=fields.pop("cleanup_pending", True),
        **fields,
    )


def posts(fake, path):
    return [x for x in fake.calls if x[0] == "POST" and x[1] == path]


def record(jobs, handle):
    return jobs._get(handle.job_id).delegation


async def active(stores, *, provider=True, count=1):
    bridge, workflow_id, scope = bundle(stores, count=count)
    handle = await admit(bridge, workflow_id, scope)
    if provider:
        for i in range(count):
            await invoke(bridge, handle, scope, index=i, key=f"dispatch:{i}")
    await bridge.jobs.cancel(handle.job_id)
    return bridge, handle, scope


async def test_cleanup_requires_closed_root_and_explicit_owned_guard(stores, fake):
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    await invoke(bridge, handle, scope)
    owned = cleanup(bridge)
    with pytest.raises(ValueError, match="must be closed"):
        await owned.stop_provider_owned(handle.job_id, scope.operations[0].path)
    await bridge.jobs.cancel(handle.job_id)
    unregistered = RuntimeOwnedCleanup(
        bridge.jobs, tuple(bridge._registrations.values()), clock=lambda: 100.0
    )
    with pytest.raises(ValueError, match="authority"):
        await unregistered.stop_provider_owned(handle.job_id, scope.operations[0].path)
    with pytest.raises(ValueError, match="target"):
        await owned.stop_runtime_owned(handle.job_id)
    assert not posts(fake, "/queue") and not posts(fake, "/interrupt")
    assert record(bridge.jobs, handle).cleanup_window is None


async def test_queued_stop_is_journaled_once_and_absence_is_unknown(stores, fake, monkeypatch):
    bridge, handle, scope = await active(stores)
    owned, path = cleanup(bridge), scope.operations[0].path
    provider = bridge.jobs.providers.get("comfyui")
    cancel = provider.cancel_owned
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(execution_id):
        raw = bridge.jobs._authority.read("active.json")["active"]["runtime_delegation"]
        assert (
            raw["closed"]
            and raw["operations"][path]["cleanup"]["stop"]["state"] == "handoff_possible"
        )
        assert execution_id == "prompt-1"
        entered.set()
        await release.wait()
        return await cancel(execution_id)

    monkeypatch.setattr(provider, "cancel_owned", blocked)
    first = asyncio.create_task(owned.stop_provider_owned(handle.job_id, path))
    await entered.wait()
    duplicate = await owned.stop_provider_owned(handle.job_id, path)
    assert duplicate == {"state": "handoff_possible", "receipt": None}
    release.set()
    receipt = await first
    assert receipt["receipt"]["status"] == "unknown"
    assert await owned.stop_provider_owned(handle.job_id, path) == receipt
    assert len(posts(fake, "/queue")) == 1 and not posts(fake, "/interrupt")
    assert len(fake.prompts) == 1 and bridge.jobs.activity()["busy"]
    assert (await bridge.jobs.result(handle.job_id))["files"] == []
    with pytest.raises(GenerationBusyError):
        await bridge.jobs.submit(scope.operations[0].workflow_id)


async def test_running_unsupported_stop_never_retries_or_uses_global_interrupt(
    stores, fake, settings
):
    bridge, handle, scope = await active(stores)
    fake.running, fake.pending = fake.pending, []
    owned, path = cleanup(bridge), scope.operations[0].path
    receipt = await owned.stop_provider_owned(handle.job_id, path)
    assert receipt["receipt"] == {"status": "running", "cancel_supported": False}
    settings.targeted_interrupt = True
    assert await owned.stop_provider_owned(handle.job_id, path) == receipt
    assert not posts(fake, "/interrupt")
    fake.finish()
    observation = await owned.inspect_provider_owned(handle.job_id, path)
    assert observation["status"] == "completed"
    assert bridge.jobs.activity()["busy"] and fake.download_count == 0
    assert (await bridge.jobs.status(handle.job_id))["status"] == "cancel_requested"


async def test_targeted_stop_and_terminal_observation_are_distinct_from_release(
    stores, fake, settings
):
    bridge, handle, scope = await active(stores)
    fake.running, fake.pending = fake.pending, []
    settings.targeted_interrupt = True
    owned, path = cleanup(bridge), scope.operations[0].path
    receipt = await owned.stop_provider_owned(handle.job_id, path)
    assert receipt["receipt"]["status"] == "cancel_requested"
    assert posts(fake, "/interrupt") == [("POST", "/interrupt", {"prompt_id": "prompt-1"})]
    fake.finish(state="error", messages=[["execution_interrupted", {}]])
    assert (await owned.inspect_provider_owned(handle.job_id, path))["status"] == "cancelled"
    fake.finish(state="error", messages=[["execution_error", {}]])
    with pytest.raises(ValueError, match="conflicts"):
        await owned.inspect_provider_owned(handle.job_id, path)
    retained = record(bridge.jobs, handle).operations[path].cleanup
    assert retained.observation.status == "cancelled"
    assert retained.last_failure == "observation_conflict"
    assert bridge.jobs.activity()["busy"]


async def test_cancel_during_provider_handoff_preserves_late_identity_for_stop(
    stores, fake, monkeypatch
):
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    provider = bridge.jobs.providers.get("comfyui")
    submit = provider.submit
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(request, provider_job_id):
        entered.set()
        await release.wait()
        return await submit(request, provider_job_id)

    monkeypatch.setattr(provider, "submit", blocked)
    task = asyncio.create_task(invoke(bridge, handle, scope))
    await entered.wait()
    await bridge.jobs.cancel(handle.job_id)
    owned, path = cleanup(bridge), scope.operations[0].path
    with pytest.raises(ValueError, match="route"):
        await owned.stop_provider_owned(handle.job_id, path)
    assert not posts(fake, "/queue")
    release.set()
    assert (await task)["execution_id"] == "prompt-1"
    assert (await owned.stop_provider_owned(handle.job_id, path))["state"] == "observed"
    assert len(fake.prompts) == len(posts(fake, "/queue")) == 1


async def test_cancel_during_runtime_handoff_can_stop_only_matching_late_run(stores):
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(scope):
        entered.set()
        await release.wait()
        return await accepted(scope)

    task = asyncio.create_task(bridge.handoff(handle, "owner", TARGET, blocked))
    await entered.wait()
    await bridge.jobs.cancel(handle.job_id)
    calls = []

    async def stop(scope, admission):
        calls.append(admission)
        raw = bridge.jobs._authority.read("active.json")["active"]["runtime_delegation"]
        assert raw["cleanup"]["stop"]["state"] == "handoff_possible"
        return RuntimeStopReceipt(admission=admission, accepted=True, applied=True, terminal=True)

    owned = cleanup(bridge, port=runtime_port(cancel=stop))
    with pytest.raises(ValueError, match="unknown Runtime admission"):
        await owned.stop_runtime_owned(handle.job_id)
    assert not calls
    release.set()
    await task
    receipt = await owned.stop_runtime_owned(handle.job_id)
    assert receipt["receipt"]["terminal"]
    assert await owned.stop_runtime_owned(handle.job_id) == receipt
    assert len(calls) == 1 and bridge.jobs.activity()["busy"]


async def test_owned_cleanup_survives_peer_revocation_without_disclosing_to_peer(stores, fake):
    bridge, handle, scope = await active(stores)
    bridge._peer_check = lambda *_: False
    with pytest.raises(ValueError, match="authorization"):
        bridge.observation(handle, "owner", TARGET)
    owned, path = cleanup(bridge), scope.operations[0].path
    await owned.stop_provider_owned(handle.job_id, path)
    fake.finish()
    assert (await owned.inspect_provider_owned(handle.job_id, path))["status"] == "completed"
    with pytest.raises(ValueError, match="authorization"):
        await invoke(bridge, handle, scope)
    assert len(fake.prompts) == 1 and bridge.jobs.activity()["busy"]


async def test_stop_before_useful_expiry_is_forbidden_then_owned_expiry_can_close(stores, fake):
    now = [100.0]
    bridge, workflow_id, scope = bundle(stores, count=2, clock=lambda: now[0])
    handle = await admit(bridge, workflow_id, scope)
    await invoke(bridge, handle, scope)
    owned = cleanup(bridge, clock=lambda: now[0])
    now[0] = 201.0
    await owned.stop_provider_owned(handle.job_id, scope.operations[0].path)
    assert record(bridge.jobs, handle).closed
    now[0] = 100.0
    with pytest.raises(ValueError, match="closed"):
        await invoke(bridge, handle, scope, index=1, key="different")
    assert len(fake.prompts) == 1


@pytest.mark.parametrize("owner", ["provider", "runtime"])
async def test_concurrent_inspections_consume_persisted_finite_budget(stores, fake, owner):
    bridge, handle, scope = await active(stores)
    owned = cleanup(bridge, port=runtime_port())
    if owner == "provider":
        path = scope.operations[0].path

        def call():
            return owned.inspect_provider_owned(handle.job_id, path)
    else:
        path = None

        def call():
            return owned.inspect_runtime_owned(handle.job_id)

    results = await asyncio.gather(*(call() for _ in range(5)), return_exceptions=True)
    assert sum(isinstance(x, ValueError) for x in results) == 1
    assert "budget exhausted" in str(next(x for x in results if isinstance(x, ValueError)))
    component = (
        record(bridge.jobs, handle).cleanup
        if path is None
        else record(bridge.jobs, handle).operations[path].cleanup
    )
    assert component.inspections == 4
    raw = bridge.jobs._authority.read("active.json")["active"]["runtime_delegation"]
    persisted = raw["cleanup"] if path is None else raw["operations"][path]["cleanup"]
    assert persisted["inspections"] == 4 and bridge.jobs.activity()["busy"]
    assert len(fake.prompts) == 1


@pytest.mark.parametrize("new_time", [131.0, 99.0])
async def test_cleanup_window_is_persistent_and_clock_rollback_cannot_reopen(
    stores, fake, new_time
):
    bridge, handle, scope = await active(stores)
    now = [100.0]
    owned = cleanup(bridge, clock=lambda: now[0])
    path = scope.operations[0].path
    await owned.inspect_provider_owned(handle.job_id, path)
    before = len(fake.calls)
    now[0] = new_time
    with pytest.raises(ValueError, match="window is closed"):
        await owned.inspect_provider_owned(handle.job_id, path)
    assert record(bridge.jobs, handle).cleanup_window.closed
    now[0] = 100.0
    with pytest.raises(ValueError, match="window is closed"):
        await owned.inspect_provider_owned(handle.job_id, path)
    assert len(fake.calls) == before and bridge.jobs.activity()["busy"]


async def test_restart_observes_old_provider_without_output_or_dispatch_authority(stores, fake):
    bridge, handle, scope = await active(stores)
    path = scope.operations[0].path
    owned = cleanup(bridge)
    await owned.stop_provider_owned(handle.job_id, path)
    await owned.inspect_provider_owned(handle.job_id, path)
    provider = bridge.jobs.providers.get("comfyui")
    provider._output_nodes.clear()
    provider._output_contracts.clear()
    provider._outputs.clear()
    fake.finish()
    recovered = JobStore(
        bridge.jobs.workflows,
        bridge.jobs.providers,
        bridge.jobs.capabilities,
        bridge.jobs.output_dir,
    )
    old = RuntimeOwnedCleanup(recovered, tuple(bridge._registrations.values()), clock=lambda: 100.0)
    # A recovered record alone does not grant cleanup authority.
    with pytest.raises(ValueError, match="authority"):
        await old.inspect_provider_owned(handle.job_id, path)
    owned = cleanup(bridge, jobs=recovered)
    assert (await owned.inspect_provider_owned(handle.job_id, path))["status"] == "completed"
    assert record(recovered, handle).operations[path].cleanup.inspections == 2
    assert (await owned.stop_provider_owned(handle.job_id, path))["state"] == "observed"
    assert len(posts(fake, "/queue")) == 1 and len(fake.prompts) == 1
    assert not provider._output_nodes and not provider._outputs
    assert (await recovered.result(handle.job_id))["files"] == []
    assert (await recovered.status(handle.job_id))["runtime_delegation"]["state"] == "fenced"
    bridge.jobs = recovered
    with pytest.raises(ValueError, match="unavailable"):
        bridge.observation(handle, "owner", TARGET)
    assert recovered.activity()["busy"]


@pytest.mark.parametrize("change", ["target", "pin", "route", "legacy"])
async def test_cleanup_rejects_replacement_target_or_changed_provider_binding(stores, fake, change):
    bridge, handle, scope = await active(stores)
    owned = cleanup(bridge, port=runtime_port(target=OTHER if change == "target" else TARGET))
    path = scope.operations[0].path
    if change == "target":
        with pytest.raises(ValueError, match="target"):
            await owned.stop_runtime_owned(handle.job_id)
    else:
        registration = next(iter(owned.registrations.values()))
        if change == "pin":
            owned.registrations[registration.capability] = replace(
                registration, fingerprint="9" * 64
            )
        elif change == "route":
            owned.registrations[registration.capability] = replace(
                registration, operation="image.other"
            )
        else:
            current = record(bridge.jobs, handle)
            operation = current.operations[path].model_copy(
                update={"provider_id": "", "operation": ""}
            )
            bridge.jobs._get(handle.job_id).delegation = current.model_copy(
                update={"operations": {path: operation}}
            )
        with pytest.raises(ValueError, match="route"):
            await owned.stop_provider_owned(handle.job_id, path)
    assert not posts(fake, "/queue") and not posts(fake, "/interrupt")


@pytest.mark.parametrize("committed", [False, True])
async def test_journal_failure_before_stop_never_calls_provider(
    stores, fake, monkeypatch, committed
):
    bridge, handle, scope = await active(stores)
    owned, path = cleanup(bridge), scope.operations[0].path
    write = bridge.jobs._authority.write

    def failed(name, raw):
        if committed:
            write(name, raw)
            raise CommitUnknown("private filesystem detail")
        raise OSError("private filesystem detail")

    monkeypatch.setattr(bridge.jobs._authority, "write", failed)
    with pytest.raises(ValueError, match="journal outcome"):
        await owned.stop_provider_owned(handle.job_id, path)
    assert not posts(fake, "/queue")
    assert (await owned.stop_provider_owned(handle.job_id, path))["state"] == "handoff_possible"
    if committed:
        monkeypatch.setattr(bridge.jobs._authority, "write", write)
        recovered = JobStore(
            bridge.jobs.workflows,
            bridge.jobs.providers,
            bridge.jobs.capabilities,
            bridge.jobs.output_dir,
        )
        assert (await cleanup(bridge, jobs=recovered).stop_provider_owned(handle.job_id, path))[
            "state"
        ] == "handoff_possible"
    assert bridge.jobs.activity()["busy"]


async def test_journal_failure_after_stop_keeps_possible_claim_across_restart(
    stores, fake, monkeypatch
):
    bridge, handle, scope = await active(stores)
    owned, path = cleanup(bridge), scope.operations[0].path
    write = bridge.jobs._authority.write

    def failed_receipt(name, raw):
        stop = raw["active"]["runtime_delegation"]["operations"][path]["cleanup"]["stop"]
        if stop["state"] == "observed":
            raise OSError("private filesystem detail")
        return write(name, raw)

    monkeypatch.setattr(bridge.jobs._authority, "write", failed_receipt)
    with pytest.raises(ValueError, match="journal outcome"):
        await owned.stop_provider_owned(handle.job_id, path)
    monkeypatch.setattr(bridge.jobs._authority, "write", write)
    recovered = JobStore(
        bridge.jobs.workflows,
        bridge.jobs.providers,
        bridge.jobs.capabilities,
        bridge.jobs.output_dir,
    )
    assert (await cleanup(bridge, jobs=recovered).stop_provider_owned(handle.job_id, path))[
        "state"
    ] == "handoff_possible"
    assert len(posts(fake, "/queue")) == 1 and recovered.activity()["busy"]


@pytest.mark.parametrize("failure", ["lost", "malformed", "timeout", "cancelled"])
async def test_uncertain_stop_is_consumed_without_replay(stores, fake, monkeypatch, failure):
    bridge, handle, scope = await active(stores)
    owned, path = cleanup(bridge), scope.operations[0].path
    provider = bridge.jobs.providers.get("comfyui")
    calls, entered = [], asyncio.Event()

    async def uncertain(execution_id):
        calls.append(execution_id)
        entered.set()
        if failure in {"timeout", "cancelled"}:
            await asyncio.Future()
        if failure == "lost":
            raise OSError("private upstream detail")
        return JobSnapshot(status="invented", metadata={"secret": "private"})

    monkeypatch.setattr(provider, "cancel_owned", uncertain)
    monkeypatch.setattr("flamoris_generation_mcp.runtime_cleanup.PER_CALL_SECONDS", 0.01)
    if failure == "cancelled":
        task = asyncio.create_task(owned.stop_provider_owned(handle.job_id, path))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ValueError, match="outcome is unknown"):
            await owned.stop_provider_owned(handle.job_id, path)
    assert (await owned.stop_provider_owned(handle.job_id, path)) == {
        "state": "unknown",
        "receipt": None,
    }
    assert calls == ["prompt-1"] and bridge.jobs.activity()["busy"]
    assert len(fake.prompts) == 1


async def test_guard_teardown_retains_known_terminal_stop_receipt(stores, fake):
    bridge, handle, scope = await active(stores)
    fake.finish()

    @contextmanager
    def broken(*_):
        yield
        raise RuntimeError("private authority detail")

    path = scope.operations[0].path
    with pytest.raises(ValueError, match="outcome is unknown"):
        await cleanup(bridge, guard=broken).stop_provider_owned(handle.job_id, path)
    retained = record(bridge.jobs, handle).operations[path].cleanup.stop
    assert retained.state == "unknown" and retained.receipt.status == "completed"
    assert bridge.jobs.activity()["busy"] and not posts(fake, "/queue")


@pytest.mark.parametrize(
    "invalid", ["old_watermark", "changed_watermark", "changed_run", "terminal_change"]
)
async def test_runtime_reconciliation_rejects_stale_or_changed_observations(stores, invalid):
    bridge, handle, _ = await active(stores, provider=False)
    admission = record(bridge.jobs, handle).admission
    first = runtime_observation(
        admission,
        watermark="9007199254740993",
        state="failed" if invalid == "terminal_change" else "cancelling",
    )
    second = first
    if invalid == "old_watermark":
        second = first.model_copy(update={"watermark": "9007199254740992"})
    elif invalid == "changed_watermark":
        second = first.model_copy(update={"cleanup_pending": False})
    elif invalid == "changed_run":
        second = first.model_copy(update={"admission": admission.model_copy(update={"run": "2"})})
    else:
        second = first.model_copy(update={"state": "succeeded", "watermark": "9007199254740994"})
    observations = [first, second]

    async def inspect(*_):
        return observations.pop(0)

    owned = cleanup(bridge, port=runtime_port(inspect=inspect))
    await owned.inspect_runtime_owned(handle.job_id)
    with pytest.raises(ValueError, match="conflicts|unknown"):
        await owned.inspect_runtime_owned(handle.job_id)
    assert record(bridge.jobs, handle).cleanup.observation == first
    assert record(bridge.jobs, handle).cleanup.inspections == 2
    assert bridge.jobs.activity()["busy"]


async def test_each_occurrence_stop_uses_its_original_provider_identity(stores, fake):
    bridge, handle, scope = await active(stores, count=2)
    owned = cleanup(bridge)
    for occurrence in scope.operations:
        await owned.stop_provider_owned(handle.job_id, occurrence.path)
    assert posts(fake, "/queue") == [
        ("POST", "/queue", {"delete": ["prompt-1"]}),
        ("POST", "/queue", {"delete": ["prompt-2"]}),
    ]
    assert len(fake.prompts) == 2 and len(bridge.jobs._jobs) == 1
    assert bridge.jobs.activity()["busy"] and not posts(fake, "/interrupt")


async def test_inspection_claim_is_persisted_before_get_and_failed_get_consumes_budget(
    stores, monkeypatch
):
    bridge, handle, scope = await active(stores)
    owned, path = cleanup(bridge), scope.operations[0].path
    provider = bridge.jobs.providers.get("comfyui")
    calls = []

    async def failed(execution_id):
        raw = bridge.jobs._authority.read("active.json")["active"]["runtime_delegation"]
        calls.append(raw["operations"][path]["cleanup"]["inspections"])
        raise RuntimeError("private response")

    monkeypatch.setattr(provider, "inspect_owned", failed)
    for _ in range(4):
        with pytest.raises(ValueError, match="inspection outcome is unknown"):
            await owned.inspect_provider_owned(handle.job_id, path)
    with pytest.raises(ValueError, match="budget exhausted"):
        await owned.inspect_provider_owned(handle.job_id, path)
    assert calls == [1, 2, 3, 4]
    assert record(bridge.jobs, handle).operations[path].cleanup.last_failure == "unknown"


async def test_unknown_provider_acceptance_cannot_be_probed_or_stopped_by_guess(
    stores, monkeypatch
):
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)

    async def lost(*_):
        raise OSError("response lost")

    monkeypatch.setattr(bridge.jobs.providers.get("comfyui"), "submit", lost)
    with pytest.raises(OSError):
        await invoke(bridge, handle, scope)
    await bridge.jobs.cancel(handle.job_id)
    owned, path = cleanup(bridge), scope.operations[0].path
    for call in (owned.stop_provider_owned, owned.inspect_provider_owned):
        with pytest.raises(ValueError, match="route"):
            await call(handle.job_id, path)
    assert bridge.jobs.activity()["busy"]

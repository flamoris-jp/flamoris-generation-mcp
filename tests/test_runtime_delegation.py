"""Real JobStore/ComfyUI adapter boundaries; offline guards are explicit fixtures."""

import asyncio
import json
from contextlib import contextmanager, nullcontext
from dataclasses import replace

import pytest
from pydantic import ValidationError

from flamoris_generation_mcp.durable import CommitUnknown
from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.providers.base import ProviderJob, SubmissionRejected
from flamoris_generation_mcp.runtime_delegation import (
    DelegationHandle,
    DelegationScope,
    InternalProviderOperation,
    Occurrence,
    RuntimeAdmission,
    RuntimeAdmissionRejected,
    RuntimeDelegations,
    RuntimeTarget,
    request_digest,
)
from flamoris_generation_mcp.workflows import Parameters

TARGET = RuntimeTarget(high="99", low="1")
OTHER = RuntimeTarget(high="99", low="2")
PIN = "1" * 64


def bundle(stores, *, guard=None, count=1, workflow_id=None, clock=lambda: 100.0):
    workflows, jobs, _ = stores
    if workflow_id is None:
        workflow_id = workflows.build(
            "text-to-image", Parameters(checkpoint="base.safetensors", positive_prompt="flowers")
        )["workflow_id"]
    request = jobs._validated_request(workflow_id)
    concrete = request_digest(request)
    registration = InternalProviderOperation(
        "generation.internal.image",
        PIN,
        "comfyui",
        "image.generate",
        guard or (lambda *args: nullcontext()),
    )
    operations = tuple(
        Occurrence(
            path=f"root/part_{i}",
            workflow_id=workflow_id,
            capability=registration.capability,
            fingerprint=PIN,
            input_digest=concrete,
            timeout_ms=1000,
        )
        for i in range(count)
    )
    fields = {
        name: "sha256:" + str(i) * 64
        for i, name in enumerate(
            (
                "root_digest",
                "closure_digest",
                "structural_digest",
                "invocation_digest",
                "evidence_digest",
            )
        )
    }
    if request.payload.schema_version == 3:
        fields.update(root_digest=request.payload.definition_digest)
        fields.update(
            {
                name: getattr(request.payload, name)
                for name in ("closure_digest", "structural_digest", "invocation_digest")
            }
        )
    scope = DelegationScope(
        principal="owner",
        target=TARGET,
        **fields,
        root_request_digest=concrete,
        runtime_request_digest="2" * 64,
        runtime_plan_fingerprint="3" * 64,
        expires_at=200.0,
        operations=operations,
    )
    bridge = RuntimeDelegations(jobs, (registration,), peer_check=lambda *_: True, clock=clock)
    return bridge, workflow_id, scope


async def accepted(scope):
    return RuntimeAdmission(
        target=scope.target,
        run="1",
        request_digest=scope.runtime_request_digest,
        plan_fingerprint=scope.runtime_plan_fingerprint,
    )


async def admit(bridge, workflow_id, scope):
    handle = await bridge.prepare(workflow_id, scope)
    await bridge.handoff(handle, "owner", TARGET, accepted)
    return handle


async def invoke(bridge, handle, scope, *, index=0, key="dispatch:1"):
    occurrence = scope.operations[index]
    return await bridge.invoke(
        handle, "owner", TARGET, occurrence.path, key, occurrence.input_digest
    )


async def test_preparation_owns_normal_slot_and_keeps_nonce_private(stores, fake):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)
    assert not fake.prompts
    assert jobs.activity() == {"busy": True, "active_job_id": handle.job_id}
    raw = jobs._authority.read("active.json")["active"]
    assert raw["runtime_delegation"]["state"] == "prepared"
    assert raw["runtime_delegation"]["scope"]["target"] == {"high": "99", "low": "1"}
    assert handle.nonce not in json.dumps(raw)
    public = await jobs.status(handle.job_id)
    assert public["provider_execution_id"] == ""
    assert public["runtime_delegation"] == {"state": "prepared", "closed": False}
    assert handle.nonce not in repr(handle) + json.dumps(public)
    assert "nonce_hash" not in json.dumps(public)
    with pytest.raises(GenerationBusyError):
        await jobs.submit(workflow_id)
    with pytest.raises(GenerationBusyError):
        await bridge.prepare(workflow_id, scope)
    assert (await jobs.result(handle.job_id))["files"] == []


async def test_foreign_peer_forged_nonce_and_unapproved_operation_are_denied(stores, fake):
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    for principal, target, candidate in (
        ("other", TARGET, handle),
        ("owner", OTHER, handle),
        ("owner", TARGET, DelegationHandle(handle.job_id, "guessed")),
    ):
        with pytest.raises(ValueError, match="unavailable"):
            bridge.observation(candidate, principal, target)
        with pytest.raises(ValueError, match="unavailable"):
            await bridge.invoke(
                candidate, principal, target, "root/part_0", "d:1", scope.operations[0].input_digest
            )
    with pytest.raises(ValueError, match="Unapproved"):
        await bridge.invoke(
            handle, "owner", TARGET, "root/unapproved", "d:1", scope.operations[0].input_digest
        )
    with pytest.raises(ValueError, match="changed"):
        await bridge.invoke(handle, "owner", TARGET, "root/part_0", "d:1", "sha256:" + "f" * 64)
    assert not fake.prompts


async def test_single_runtime_handoff_and_cancel_during_lost_response(stores):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def blocked(scope):
        calls.append(scope)
        assert (
            jobs._authority.read("active.json")["active"]["runtime_delegation"]["state"]
            == "handoff_possible"
        )
        entered.set()
        await release.wait()
        return await accepted(scope)

    first = asyncio.create_task(bridge.handoff(handle, "owner", TARGET, blocked))
    await entered.wait()
    duplicate = await bridge.handoff(handle, "owner", TARGET, blocked)
    assert duplicate["state"] == "handoff_possible"
    assert (await jobs.cancel(handle.job_id))["status"] == "cancel_requested"
    release.set()
    result = await first
    assert result["state"] == "accepted" and result["closed"]
    assert len(calls) == 1 and jobs.activity()["busy"]
    with pytest.raises(ValueError, match="closed"):
        await invoke(bridge, handle, scope)
    with pytest.raises(ValueError, match="retains"):
        await bridge.release_unaccepted(handle, "owner", TARGET)


@pytest.mark.parametrize("cancel", [False, True])
async def test_unknown_runtime_handoff_is_never_replayed(stores, cancel):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)
    calls = []

    async def lost(scope):
        calls.append(scope)
        raise RuntimeError("private transport detail")

    with pytest.raises(RuntimeError):
        await bridge.handoff(handle, "owner", TARGET, lost)
    if cancel:
        await jobs.cancel(handle.job_id)
    assert (await bridge.handoff(handle, "owner", TARGET, lost))["state"] == "unknown"
    assert len(calls) == 1 and jobs.activity()["busy"]
    assert "private transport" not in json.dumps(await jobs.status(handle.job_id))
    with pytest.raises(ValueError, match="retains"):
        await bridge.release_unaccepted(handle, "owner", TARGET)


async def test_definite_runtime_rejection_and_pre_handoff_cancel_release_slot(stores):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)

    async def rejected(scope):
        raise RuntimeAdmissionRejected("verified no Run admission")

    with pytest.raises(RuntimeAdmissionRejected):
        await bridge.handoff(handle, "owner", TARGET, rejected)
    assert not jobs.activity()["busy"]
    handle = await bridge.prepare(workflow_id, scope)
    assert (await jobs.cancel(handle.job_id))["status"] == "cancelled"
    assert not jobs.activity()["busy"]
    with pytest.raises(ValueError):
        await bridge.handoff(handle, "owner", TARGET, accepted)


async def test_internal_duplicate_claim_uses_real_provider_once(stores, fake, monkeypatch):
    _, jobs, _ = stores
    held = []

    @contextmanager
    def guard(scope, admission, occurrence, key):
        assert admission.target == scope.target == TARGET
        assert admission.run == "1" and occurrence.path == "root/part_0"
        assert key == "dispatch:1"
        held.append(True)
        yield
        held.pop()

    bridge, workflow_id, scope = bundle(stores, guard=guard)
    handle = await admit(bridge, workflow_id, scope)
    provider = jobs.providers.get("comfyui")
    original = provider.submit
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(request, provider_job_id):
        raw = jobs._authority.read("active.json")["active"]["runtime_delegation"]["operations"][
            "root/part_0"
        ]
        assert raw["state"] == "handoff_possible"
        assert raw["provider_job_id"] == provider_job_id != handle.job_id
        assert held == [True]
        entered.set()
        await release.wait()
        return await original(request, provider_job_id)

    monkeypatch.setattr(provider, "submit", blocked)
    first = asyncio.create_task(invoke(bridge, handle, scope))
    await entered.wait()
    duplicate = await asyncio.wait_for(invoke(bridge, handle, scope), 0.5)
    assert duplicate["state"] == "handoff_possible"
    release.set()
    result = await first
    assert result["state"] == "accepted" and not held
    assert await invoke(bridge, handle, scope) == result
    assert len(fake.prompts) == 1 and len(jobs._jobs) == 1
    with pytest.raises(ValueError, match="different dispatch"):
        await invoke(bridge, handle, scope, key="dispatch:2")
    assert jobs.activity()["busy"]
    # Cancellation is a fence, not a fabricated provider/host settlement receipt.
    await jobs.cancel(handle.job_id)
    assert await invoke(bridge, handle, scope) == result
    assert jobs.activity()["busy"] and (await jobs.result(handle.job_id))["files"] == []


async def test_occurrences_get_separate_provider_staging_identities(stores, fake):
    bridge, workflow_id, scope = bundle(stores, count=2)
    handle = await admit(bridge, workflow_id, scope)
    first = await invoke(bridge, handle, scope)
    with pytest.raises(ValueError, match="different occurrence"):
        await invoke(bridge, handle, scope, index=1)
    second = await invoke(bridge, handle, scope, index=1, key="dispatch:2")
    assert first["provider_job_id"] != second["provider_job_id"]
    assert len(fake.prompts) == 2
    assert len({x["prompt"]["7"]["inputs"]["filename_prefix"] for x in fake.prompts}) == 2


@pytest.mark.parametrize("rejected", [False, True])
async def test_provider_failure_consumes_claim_and_retains_runtime_root(
    stores, fake, monkeypatch, rejected
):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    calls = []

    async def failure(*args):
        calls.append(args)
        if rejected:
            raise SubmissionRejected("no provider acceptance")
        raise RuntimeError("lost response")

    monkeypatch.setattr(jobs.providers.get("comfyui"), "submit", failure)
    with pytest.raises((RuntimeError, SubmissionRejected)):
        await invoke(bridge, handle, scope)
    result = await invoke(bridge, handle, scope)
    assert result["state"] == ("rejected" if rejected else "unknown")
    assert len(calls) == 1 and jobs.activity()["busy"] and not fake.prompts
    with pytest.raises(ValueError, match="retains"):
        await bridge.release_unaccepted(handle, "owner", TARGET)


async def test_guard_exit_cannot_reclassify_accepted_provider_as_rejected(stores, fake):
    @contextmanager
    def bad_exit(*args):
        yield
        raise SubmissionRejected("guard failed after actual acceptance")

    bridge, workflow_id, scope = bundle(stores, guard=bad_exit)
    handle = await admit(bridge, workflow_id, scope)
    with pytest.raises(SubmissionRejected):
        await invoke(bridge, handle, scope)
    assert len(fake.prompts) == 1
    retained = await invoke(bridge, handle, scope)
    assert retained["state"] == "unknown"
    assert retained["execution_id"] == "prompt-1"


async def test_task_cancellation_never_reopens_internal_claim(stores, fake, monkeypatch):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs.providers.get("comfyui"), "submit", blocked)
    task = asyncio.create_task(invoke(bridge, handle, scope))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await invoke(bridge, handle, scope))["state"] == "unknown"
    assert jobs.activity()["busy"] and not fake.prompts


@pytest.mark.parametrize("uncertain", [False, True])
async def test_root_journal_failure_prevents_handoff(stores, fake, monkeypatch, uncertain):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    original = jobs._authority.write

    def failed(*args):
        if uncertain:
            original(*args)
            raise CommitUnknown("directory durability uncertain")
        raise OSError("before publication")

    monkeypatch.setattr(jobs._authority, "write", failed)
    with pytest.raises(OSError):
        await bridge.prepare(workflow_id, scope)
    assert jobs.activity()["busy"] is uncertain
    assert not fake.prompts


@pytest.mark.parametrize("after_provider", [False, True])
async def test_operation_journal_failure_fences_in_memory_and_restart(
    stores, fake, monkeypatch, after_provider
):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    original = jobs._authority.write
    count = 0

    def failed(*args):
        nonlocal count
        count += 1
        if count == (2 if after_provider else 1):
            raise OSError("journal failed")
        original(*args)

    monkeypatch.setattr(jobs._authority, "write", failed)
    with pytest.raises(OSError):
        await invoke(bridge, handle, scope)
    assert len(fake.prompts) == int(after_provider)
    with pytest.raises(ValueError, match="unavailable"):
        await invoke(bridge, handle, scope)
    assert (await jobs.status(handle.job_id))["runtime_delegation"]["state"] == "fenced"
    restarted = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    other = RuntimeDelegations(
        restarted,
        tuple(bridge._registrations.values()),
        peer_check=lambda *_: True,
        clock=lambda: 100.0,
    )
    with pytest.raises(ValueError, match="unavailable"):
        other.observation(handle, "owner", TARGET)
    assert restarted.activity()["busy"]
    await restarted.cancel(handle.job_id)
    assert restarted.activity()["busy"]
    with pytest.raises(GenerationBusyError):
        await restarted.submit(workflow_id)
    assert len(fake.prompts) == int(after_provider)


async def test_expiry_never_reopens_after_clock_rollback(stores, fake):
    now = [100.0]
    bridge, workflow_id, scope = bundle(stores, clock=lambda: now[0])
    handle = await admit(bridge, workflow_id, scope)
    now[0] = 200.0
    assert bridge.observation(handle, "owner", TARGET)["closed"]
    assert (await bridge.jobs.status(handle.job_id))["runtime_delegation"]["closed"]
    now[0] = 100.0
    with pytest.raises(ValueError, match="expired"):
        await invoke(bridge, handle, scope)
    assert not fake.prompts


async def test_changed_inputs_and_stale_pins_deny_dispatch(stores, fake):
    _, jobs, _ = stores
    now = [100.0]
    bridge, workflow_id, scope = bundle(stores, clock=lambda: now[0])
    handle = await admit(bridge, workflow_id, scope)
    original = jobs.workflows._recipes[workflow_id]
    jobs.workflows._recipes[workflow_id] = original.model_copy(
        update={"parameters": original.parameters.model_copy(update={"positive_prompt": "changed"})}
    )
    with pytest.raises(ValueError, match="changed"):
        await invoke(bridge, handle, scope)
    jobs.workflows._recipes[workflow_id] = original
    bridge._registrations[scope.operations[0].capability] = replace(
        next(iter(bridge._registrations.values())), fingerprint="f" * 64
    )
    with pytest.raises(ValueError, match="stale"):
        await invoke(bridge, handle, scope)
    assert not fake.prompts


async def test_malformed_runtime_receipt_retains_unknown_reservation(stores):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)

    async def wrong(scope):
        return (await accepted(scope)).model_copy(update={"target": OTHER})

    with pytest.raises(ValueError, match="receipt"):
        await bridge.handoff(handle, "owner", TARGET, wrong)
    assert bridge.observation(handle, "owner", TARGET)["state"] == "unknown"
    assert jobs.activity()["busy"]


async def test_recursion_cannot_acquire_another_public_root(stores, fake, monkeypatch):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)

    async def recursive(*args):
        return await jobs.submit(workflow_id)

    monkeypatch.setattr(jobs.providers.get("comfyui"), "submit", recursive)
    with pytest.raises(GenerationBusyError):
        await invoke(bridge, handle, scope)
    assert len(jobs._jobs) == 1 and not fake.prompts
    assert (await invoke(bridge, handle, scope))["state"] == "unknown"


async def test_scope_bounds_and_public_root_purpose_cannot_register(stores):
    bridge, _, scope = bundle(stores)
    registration = next(iter(bridge._registrations.values()))
    with pytest.raises(ValueError, match="internal provider"):
        RuntimeDelegations(
            bridge.jobs,
            (replace(registration, purpose="public_root_admission"),),
            peer_check=lambda *_: True,
        )
    with pytest.raises(ValidationError):
        RuntimeTarget(high=str(2**64), low="0")
    assert RuntimeTarget(high=str(2**64 - 1), low="1").high == str(2**64 - 1)
    for update in (
        {"operations": scope.operations * 65},
        {"expires_at": float("nan")},
        {"principal": ""},
    ):
        with pytest.raises(ValidationError):
            DelegationScope.model_validate(scope.model_dump() | update)


async def test_managed_input_receipt_is_retained_and_bounded(stores, monkeypatch):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    receipt = {
        "input_id": "a" * 32,
        "source_asset_id": "b" * 32 + ":000",
        "sha256": "c" * 64,
        "mime_type": "image/png",
        "size_bytes": 100,
    }

    async def provider(*args):
        return ProviderJob("accepted-1", {"initial_image": receipt})

    monkeypatch.setattr(jobs.providers.get("comfyui"), "submit", provider)
    result = await invoke(bridge, handle, scope)
    assert result["managed_inputs"] == {"initial_image": receipt}
    raw = jobs._authority.read("active.json")["active"]["runtime_delegation"]
    assert raw["operations"]["root/part_0"]["managed_inputs"]["initial_image"] == receipt


@pytest.mark.parametrize("kind", ["path", "metadata"])
async def test_malformed_provider_receipt_never_reopens_claim(stores, monkeypatch, kind):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)

    async def bad(*args):
        if kind == "path":
            return ProviderJob("/private/provider/path")
        return ProviderJob("known-1", {"initial_image": {"path": "/private/input"}})

    monkeypatch.setattr(jobs.providers.get("comfyui"), "submit", bad)
    with pytest.raises(ValueError):
        await invoke(bridge, handle, scope)
    result = await invoke(bridge, handle, scope)
    assert result["state"] == "unknown"
    assert result["execution_id"] == ("known-1" if kind == "metadata" else "")
    assert "/private/" not in json.dumps(result)
    assert jobs.activity()["busy"]


async def test_cancel_during_internal_acceptance_preserves_receipt_and_root(stores, monkeypatch):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores, count=2)
    handle = await admit(bridge, workflow_id, scope)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args):
        entered.set()
        await release.wait()
        return ProviderJob("accepted-after-cancel")

    monkeypatch.setattr(jobs.providers.get("comfyui"), "submit", blocked)
    task = asyncio.create_task(invoke(bridge, handle, scope))
    await entered.wait()
    await jobs.cancel(handle.job_id)
    release.set()
    assert (await task)["execution_id"] == "accepted-after-cancel"
    assert (await jobs.status(handle.job_id))["status"] == "cancel_requested"
    assert jobs.activity()["busy"]
    with pytest.raises(ValueError, match="closed"):
        await invoke(bridge, handle, scope, index=1, key="next-dispatch")


async def test_corrupt_recovered_operation_relation_fails_closed(stores):
    _, jobs, _ = stores
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    await invoke(bridge, handle, scope)
    raw = jobs._authority.read("active.json")
    raw["active"]["runtime_delegation"]["operations"]["root/part_0"]["input_digest"] = (
        "sha256:" + "f" * 64
    )
    jobs._authority.write("active.json", raw)
    with pytest.raises(ValueError, match="reservation"):
        JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)


async def test_current_peer_revocation_denies_reads_duplicates_and_new_work(stores, fake):
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)
    await invoke(bridge, handle, scope)
    bridge._peer_check = lambda *_: False
    with pytest.raises(ValueError, match="authorization"):
        bridge.observation(handle, "owner", TARGET)
    with pytest.raises(ValueError, match="authorization"):
        await invoke(bridge, handle, scope)
    with pytest.raises(ValueError, match="authorization"):
        await bridge.handoff(handle, "owner", TARGET, accepted)
    with pytest.raises(ValueError, match="authorization"):
        await bridge.release_unaccepted(handle, "owner", TARGET)
    with pytest.raises(ValueError, match="authorization"):
        await bridge.prepare(workflow_id, scope)
    assert len(fake.prompts) == 1 and bridge.jobs.activity()["busy"]


async def test_revocation_during_response_keeps_receipt_without_returning_access(stores):
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(scope):
        entered.set()
        await release.wait()
        return await accepted(scope)

    task = asyncio.create_task(bridge.handoff(handle, "owner", TARGET, blocked))
    await entered.wait()
    bridge._peer_check = lambda *_: False
    release.set()
    with pytest.raises(ValueError, match="authorization"):
        await task
    record = bridge.jobs._authority.read("active.json")["active"]["runtime_delegation"]
    assert record["admission"]["run"] == "1" and record["state"] == "accepted"
    assert bridge.jobs.activity()["busy"]


async def test_revocation_during_internal_response_keeps_owned_receipt(stores, monkeypatch):
    bridge, workflow_id, scope = bundle(stores)
    handle = await admit(bridge, workflow_id, scope)

    async def provider(*args):
        bridge._peer_check = lambda *_: False
        return ProviderJob("known-after-revocation")

    monkeypatch.setattr(bridge.jobs.providers.get("comfyui"), "submit", provider)
    with pytest.raises(ValueError, match="authorization"):
        await invoke(bridge, handle, scope)
    record = bridge.jobs._authority.read("active.json")["active"]["runtime_delegation"]
    assert record["operations"]["root/part_0"]["execution_id"] == "known-after-revocation"
    assert bridge.jobs.activity()["busy"]


@pytest.mark.parametrize("cancel", [False, True])
async def test_owned_rejection_cleanup_survives_revocation_and_cancel(stores, cancel):
    bridge, workflow_id, scope = bundle(stores)
    handle = await bridge.prepare(workflow_id, scope)

    async def rejected(scope):
        if cancel:
            await bridge.jobs.cancel(handle.job_id)
        bridge._peer_check = lambda *_: False
        raise RuntimeAdmissionRejected("proven no Run")

    with pytest.raises(RuntimeAdmissionRejected):
        await bridge.handoff(handle, "owner", TARGET, rejected)
    assert not bridge.jobs.activity()["busy"]
    assert (await bridge.jobs.status(handle.job_id))["status"] == (
        "cancelled" if cancel else "failed"
    )

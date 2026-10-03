"""Actual JobStore/HTTP/attestation boundaries, without a live GPU."""

import copy
import json
import time

import httpx
import pytest
from conftest import validation_rejection
from mcp import Client
from test_image_v3 import leaf, parent
from test_mcp import TOOL_NAMES
from test_verification import verified as legacy_verified

from flamoris_generation_mcp.image_v3 import ImageV3
from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.providers.base import ProviderError, SubmissionUnknown
from flamoris_generation_mcp.server import create_server
from flamoris_generation_mcp.verification import WorkflowVerification
from flamoris_generation_mcp.workflow_v3 import Definition
from flamoris_generation_mcp.workflows import WorkflowStore

verified = legacy_verified

VALUES = {"checkpoint": "base.safetensors", "positive_prompt": "x"}
V3_TOOLS = {"workflows.v3." + k for k in ("register", "list", "build", "verify", "revoke")}


@pytest.fixture
async def execution(verified):
    store, jobs, verifier, path, client = verified
    store.v3 = ImageV3(store)
    raw = leaf()
    store.v3.versions.register(raw)
    root = parent(raw)
    store.v3.versions.register(root)
    yield store, jobs, verifier, Definition.model_validate(root), path, client
    await jobs.close()


async def ready(execution, fake):
    store, jobs, verifier, root, _, _ = execution
    result = await verifier.verify(root.id, root.version, root.digest, VALUES, v3=True)
    fake.finish(result["provider_execution_id"])
    assert (await jobs.status(result["job_id"]))["verification"]["state"] == "ready"
    return result


def build(execution, *, require_ready=True, **values):
    store, _, _, root, _, _ = execution
    return store.build_v3(root.id, root.version, root.digest, VALUES | values, require_ready)


async def test_parent_attestation_production_roles_busy_and_restart(execution, fake):
    store, jobs, verifier, root, _, _ = execution
    with pytest.raises(ValueError):
        build(execution)
    unqualified = build(execution, require_ready=False)
    with pytest.raises(ValueError, match="requires attestation"):
        await jobs.submit(unqualified["workflow_id"])
    assert not fake.prompts
    smoke = await ready(execution, fake)
    record = verifier.v3_records.read(f"{root.id}-{root.version}.json")
    assert record["smoke_invocation_digest"] == smoke["invocation_digest"]
    assert record["identity"]["media_plan"]["root"]["digest"] == root.digest
    assert record["qualified_domain"]["id"] == "image-v1-bounded-scalars"
    plan = store.v3.versions.compile(root.id, root.version, root.digest)
    assert verifier.v3_descriptor(root, plan)["readiness"]["state"] == "ready"
    assert store.list()["definitions"][0]["readiness"]["state"] == "validated"
    production = build(execution, positive_prompt="different", seed=9)
    store.save(production["workflow_id"])
    active = await jobs.submit(production["workflow_id"])
    with pytest.raises(GenerationBusyError):
        await verifier.verify(root.id, root.version, root.digest, VALUES, v3=True)
    assert verifier.v3_descriptor(root, plan)["readiness"]["state"] == "ready"
    fake.finish(active["provider_execution_id"])
    result = await jobs.result(active["job_id"])
    assert result["outputs"][0]["role"] == "primary_image"
    assert result["outputs"][0]["port"] == "image"
    assert len(result["files"]) == 1
    # A finished job does not retroactively expire on a later retrieval.
    jobs._get(active["job_id"]).execution_deadline = time.time() - 1
    assert (await jobs.result(active["job_id"]))["status"] == "completed"
    newer = copy.deepcopy(leaf())
    newer["version"] = 2
    store.v3.versions.register(newer)
    restarted = WorkflowStore(store.catalog, store.directory, store.registry.root, v3_enabled=True)
    restarted.readiness = WorkflowVerification(restarted, jobs, verifier.evidence)
    captured = restarted.capture(restarted.get(production["workflow_id"]))
    assert captured.v3_identity == store.capture(store.get(production["workflow_id"])).v3_identity
    restarted.v3.versions.revoke("image-leaf", 1, Definition.model_validate(leaf()).digest)
    with pytest.raises(ValueError, match="revoked"):
        restarted.capture(restarted.get(production["workflow_id"]))


async def test_revocation_after_prompt_construction_blocks_post(execution, fake, monkeypatch):
    store, jobs, _, _, _, _ = execution
    await ready(execution, fake)
    production = build(execution)
    original = store.prompt

    def revoke(*args, **kwargs):
        prompt = original(*args, **kwargs)
        store.v3.versions.revoke("image-leaf", 1, Definition.model_validate(leaf()).digest)
        return prompt

    monkeypatch.setattr(store, "prompt", revoke)
    with pytest.raises(ValueError, match="revoked"):
        await jobs.submit(production["workflow_id"])
    assert len(fake.prompts) == 1
    assert not jobs.activity()["busy"]


async def test_runtime_delegation_retains_v3_before_post_guard(execution, fake, monkeypatch):
    from test_runtime_delegation import admit, bundle, invoke

    store, jobs, _, _, _, client = execution
    await ready(execution, fake)
    production = build(execution)
    bridge, workflow_id, scope = bundle(
        (store, jobs, client), workflow_id=production["workflow_id"]
    )
    handle = await admit(bridge, workflow_id, scope)
    original = store.prompt

    def revoke(*args, **kwargs):
        prompt = original(*args, **kwargs)
        store.v3.versions.revoke("image-leaf", 1, Definition.model_validate(leaf()).digest)
        return prompt

    monkeypatch.setattr(store, "prompt", revoke)
    with pytest.raises(ValueError, match="revoked"):
        await invoke(bridge, handle, scope)
    assert len(fake.prompts) == 1  # Only the earlier normal attestation smoke reached HTTP.
    assert (await invoke(bridge, handle, scope))["state"] == "rejected"
    assert jobs.activity()["busy"]  # The accepted Runtime Run still owns the media root.


async def test_runtime_delegation_checks_generation_media_identity(execution, fake):
    from test_runtime_delegation import bundle

    from flamoris_generation_mcp.runtime_delegation import DelegationScope

    store, jobs, _, _, _, client = execution
    await ready(execution, fake)
    production = build(execution)
    bridge, workflow_id, scope = bundle(
        (store, jobs, client), workflow_id=production["workflow_id"]
    )
    changed = DelegationScope.model_validate(
        scope.model_dump() | {"closure_digest": "sha256:" + "f" * 64}
    )
    with pytest.raises(ValueError, match="media relation"):
        await bridge.prepare(workflow_id, changed)
    assert not jobs.activity()["busy"] and len(fake.prompts) == 1


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "foreign", "mime", "payload", "bytes", "dimensions"]
)
async def test_invalid_outputs_never_qualify(execution, fake, change):
    _, jobs, verifier, root, _, client = execution
    result = await verifier.verify(root.id, root.version, root.digest, VALUES, v3=True)
    execution_id = result["provider_execution_id"]
    fake.finish(execution_id)
    outputs = fake.history[execution_id]["outputs"]["7"]["images"]
    if change == "missing":
        outputs.clear()
    elif change == "duplicate":
        outputs.append(dict(outputs[0]))
    elif change == "foreign":
        fake.history[execution_id]["outputs"]["8"] = {"images": [dict(outputs[0])]}
    elif change == "mime":
        outputs[0]["filename"] = "result.webp"
    else:
        content = b"invalid" if change == "payload" else b"x" * (1048576 + 1)
        if change == "dimensions":
            import io

            from PIL import Image

            image = io.BytesIO()
            Image.new("RGB", (256, 256)).save(image, format="PNG")
            content = image.getvalue()
        client.http._transport = httpx.MockTransport(
            lambda req: (
                httpx.Response(200, content=content, headers={"content-type": "image/png"})
                if req.url.path == "/view"
                else fake.handle(req)
            )
        )
    if change == "missing":
        # Malformed history remains uncertain; never infer that work has disappeared.
        with pytest.raises(ProviderError):
            await jobs.status(result["job_id"])
        await verifier._watch(result["job_id"])
        assert jobs.activity()["busy"]
        assert verifier.v3_records.read(f"{root.id}-{root.version}.json")["state"] == "failed"
        return
    status = await jobs.status(result["job_id"])
    assert status["verification"]["state"] == "failed"
    assert verifier.v3_records.read(f"{root.id}-{root.version}.json")["state"] == "failed"
    assert not jobs.activity()["busy"]


async def test_ambiguous_post_keeps_reservation_and_recovery_does_not_replay(execution, fake):
    store, jobs, _, _, _, client = execution
    await ready(execution, fake)
    production = build(execution)

    def timeout(req):
        if req.url.path == "/prompt":
            raise httpx.ReadTimeout("unknown", request=req)
        return fake.handle(req)

    client.http._transport = httpx.MockTransport(timeout)
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(production["workflow_id"])
    assert jobs.activity()["busy"]
    recovered = JobStore(store, jobs.providers, jobs.capabilities, jobs.output_dir)
    assert recovered.activity()["busy"]
    with pytest.raises(GenerationBusyError):
        await recovered.submit(production["workflow_id"])
    assert len(fake.prompts) == 1


@pytest.mark.parametrize("running", [False, True])
async def test_deadline_is_targeted_once_and_unconfirmed_running_stays_reserved(
    execution, fake, running
):
    _, jobs, _, _, _, _ = execution
    await ready(execution, fake)
    active = await jobs.submit(build(execution)["workflow_id"])
    # Keep this test's observation deterministic rather than racing the background poll.
    await jobs.close()
    job = jobs._get(active["job_id"])
    if running:
        fake.running = fake.pending
        fake.pending = []
    job.execution_deadline = time.time() - 1
    first = await jobs.status(job.job_id)
    await jobs.status(job.job_id)
    assert first["status"] == ("running" if running else "unknown")
    assert jobs.activity()["busy"]
    assert len([c for c in fake.calls if c[0] == "POST" and c[1] == "/queue"]) == (
        0 if running else 1
    )
    assert not any(c[1] == "/interrupt" for c in fake.calls)


async def test_runtime_replacement_invalidates_v3_parent(execution, fake):
    store, _, verifier, root, path, _ = execution
    await ready(execution, fake)
    data = json.loads(path.read_text())
    data["provider_epoch"] = "provider-epoch-0002"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        build(execution)
    plan = store.v3.versions.compile(root.id, root.version, root.digest)
    assert verifier.v3_descriptor(root, plan)["readiness"]["state"] == "validated"


async def test_verification_deadline_also_cancels_target_and_never_qualifies(execution):
    _, jobs, verifier, root, _, _ = execution
    result = await verifier.verify(root.id, root.version, root.digest, VALUES, v3=True)
    job = jobs._get(result["job_id"])
    job.execution_deadline = time.time() - 1
    await jobs.status(job.job_id)
    assert job.snapshot.status == "unknown"
    assert job.verification["state"] == "failed"
    assert jobs.activity()["busy"]


async def test_admitted_rejection_supersedes_parent_ready(execution, fake):
    store, jobs, verifier, root, _, client = execution
    await ready(execution, fake)
    client.http._transport = httpx.MockTransport(
        lambda req: validation_rejection() if req.url.path == "/prompt" else fake.handle(req)
    )
    with pytest.raises(ValueError):
        await verifier.verify(root.id, root.version, root.digest, VALUES, v3=True)
    assert not jobs.activity()["busy"]
    plan = store.v3.versions.compile(root.id, root.version, root.digest)
    assert verifier.v3_descriptor(root, plan)["readiness"]["state"] == "validated"


async def test_recipe_invocation_tampering_and_revocation_before_promotion(execution, fake):
    store, jobs, verifier, root, _, _ = execution
    built = build(execution, require_ready=False)
    recipe = store.get(built["workflow_id"])
    altered = recipe.model_copy(update={"invocation_digest": "sha256:" + "a" * 64})
    with pytest.raises(ValueError, match="Stale compiled"):
        store.capture(altered)
    result = await verifier.verify(root.id, root.version, root.digest, VALUES, v3=True)
    store.v3.versions.revoke("image-leaf", 1, Definition.model_validate(leaf()).digest)
    fake.finish(result["provider_execution_id"])
    assert (await jobs.status(result["job_id"]))["verification"]["state"] == "failed"


async def test_opt_in_tools_preserve_legacy_and_expose_graph_free_descriptors(settings, fake):
    settings = settings.model_copy(update={"workflow_v3_enabled": True})
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        assert {t.name for t in (await client.list_tools()).tools} == TOOL_NAMES | V3_TOOLS
        assert not (
            await client.call_tool("workflows.v3.register", {"definition": leaf()})
        ).is_error
        listing = (await client.call_tool("workflows.v3.list")).structured_content
        assert listing["descriptor_revision"] == 3
        item = listing["descriptors"][0]
        assert item["readiness"]["state"] == "validated"
        assert "graph" not in json.dumps(item) and "artifact" not in json.dumps(item)
        args = {
            "workflow_id": "image-leaf",
            "definition_version": 1,
            "definition_digest": Definition.model_validate(leaf()).digest,
            "parameters": VALUES,
        }
        assert (await client.call_tool("workflows.v3.build", args)).is_error
        built = (
            await client.call_tool("workflows.v3.build", args | {"require_ready": False})
        ).structured_content
        assert built["schema_version"] == 3
        for invalid in (
            {"definition_version": True},
            {"definition_version": "1"},
            {"require_ready": "false"},
        ):
            assert (
                await client.call_tool(
                    "workflows.v3.build", args | {"require_ready": False} | invalid
                )
            ).is_error
        assert (
            await client.call_tool("jobs.submit", {"workflow_id": built["workflow_id"]})
        ).is_error
        assert not fake.prompts
        assert not (
            await client.call_tool(
                "workflows.v3.revoke", {k: v for k, v in args.items() if k != "parameters"}
            )
        ).is_error

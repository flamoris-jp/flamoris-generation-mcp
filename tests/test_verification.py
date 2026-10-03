import asyncio
import io
import json
import os
import time

import httpx
import pytest
from conftest import validation_rejection
from PIL import Image
from test_workflow_v2 import definition

from flamoris_generation_mcp.capabilities import Capability, CapabilityRegistry
from flamoris_generation_mcp.comfyui import ComfyUIClient
from flamoris_generation_mcp.durable import CommitUnknown
from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.providers import ProviderRegistry
from flamoris_generation_mcp.providers.base import SubmissionUnknown
from flamoris_generation_mcp.providers.comfyui import ComfyUIProvider
from flamoris_generation_mcp.runtime_evidence import RuntimeEvidence
from flamoris_generation_mcp.verification import WorkflowVerification
from flamoris_generation_mcp.workflows import WorkflowStore


@pytest.fixture
async def verified(settings, fake, tmp_path):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, tmp_path / "defs")
    store.register_definition(definition())
    path = tmp_path / "evidence.json"
    path.with_suffix(".json.lock").touch()
    manifest = {
        "core": "sha256:" + "a" * 64,
        "dependencies": "sha256:" + "b" * 64,
        "config": "sha256:" + "c" * 64,
        "nodes": {
            node["class_type"]: {
                "interface": "sha256:" + "a" * 64,
                "implementation": "sha256:" + "b" * 64,
            }
            for node in definition()["graph"].values()
        },
        "models": {"checkpoint:base.safetensors": "sha256:" + "d" * 64},
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider_url": str(settings.comfyui_url).rstrip("/"),
                "provider_epoch": "provider-epoch-0001",
                "expires_at": time.time() + 240,
                "continuity": "exclusive-mutation-lock-v1",
                "manifest": manifest,
            }
        )
    )
    image = io.BytesIO()
    Image.new("RGB", (512, 512)).save(image, format="PNG")

    def handle(request):
        if request.url.path == "/view":
            return httpx.Response(
                200, content=image.getvalue(), headers={"content-type": "image/png"}
            )
        return fake.handle(request)

    client = ComfyUIClient(settings, httpx.MockTransport(handle))
    provider = ComfyUIProvider(client, store.catalog, store)
    caps = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="test",
                workflow_templates=("image-v2",),
            ),
        )
    )
    jobs = JobStore(store, ProviderRegistry((provider,)), caps, settings.output_dir)
    verifier = WorkflowVerification(store, jobs, RuntimeEvidence(path, settings.comfyui_url))
    store.readiness = verifier
    jobs.verifier = verifier
    yield store, jobs, verifier, path, client
    await verifier.close()
    await client.close()


async def run_verify(verified):
    store, jobs, verifier, _, _ = verified
    item = store.registry.get("image-v2")
    return await verifier.verify(
        item.id,
        item.version,
        item.digest,
        {"checkpoint": "base.safetensors", "positive_prompt": "x"},
    )


async def make_ready(verified, fake):
    job = await run_verify(verified)
    fake.finish(job["provider_execution_id"])
    result = await verified[1].status(job["job_id"])
    assert result["verification"]["state"] == "ready"
    assert verified[0].list()["definitions"][0]["readiness"]["state"] == "ready"
    return job


async def test_success_restart_and_unchanged_name_replacement(verified, fake):
    store, jobs, verifier, path, _ = verified
    await make_ready(verified, fake)
    item = store.registry.get("image-v2")
    values = {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    store.build(item.id, values, item.version, item.digest, True)
    restarted = WorkflowVerification(store, jobs, verifier.evidence)
    restarted.require(item, values)
    data = json.loads(path.read_text())
    data["manifest"]["models"]["checkpoint:base.safetensors"] = "sha256:" + "e" * 64
    path.write_text(json.dumps(data))
    assert store.list()["definitions"][0]["readiness"]["state"] == "validated"
    with pytest.raises(ValueError):
        store.build(item.id, values, item.version, item.digest, True)


async def test_busy_preserves_ready_admitted_failure_supersedes(verified, fake):
    store, jobs, _, _, client = verified
    await make_ready(verified, fake)
    built = store.build("image-v2", {"checkpoint": "base.safetensors", "positive_prompt": "x"})
    active = await jobs.submit(built["workflow_id"])
    with pytest.raises(GenerationBusyError):
        await run_verify(verified)
    assert store.list()["definitions"][0]["readiness"]["state"] == "ready"
    fake.finish(active["provider_execution_id"])
    await jobs.status(active["job_id"])
    client.http._transport = httpx.MockTransport(lambda _: validation_rejection())
    with pytest.raises(ValueError):
        await run_verify(verified)
    assert store.list()["definitions"][0]["readiness"]["state"] == "validated"
    assert not jobs.activity()["busy"]


async def test_gateway_rejection_revokes_ready_and_retains_reservation(verified, fake):
    store, jobs, _, _, client = verified
    await make_ready(verified, fake)
    client.http._transport = httpx.MockTransport(lambda _: httpx.Response(400))
    with pytest.raises(SubmissionUnknown):
        await run_verify(verified)
    assert jobs.activity()["busy"]
    job_id = jobs.activity()["active_job_id"]
    result = await jobs.status(job_id)
    assert result["status"] == "unknown"
    assert result["verification"]["state"] == "failed"
    assert store.list()["definitions"][0]["readiness"]["state"] == "validated"
    with pytest.raises(GenerationBusyError):
        await run_verify(verified)


@pytest.mark.parametrize("change", ["epoch", "nodes", "expired", "output"])
async def test_changed_runtime_or_invalid_output_never_attests(verified, fake, change):
    store, jobs, _, path, client = verified
    job = await run_verify(verified)
    if change == "output":
        client.http._transport = httpx.MockTransport(
            lambda req: (
                httpx.Response(200, content=b"invalid")
                if req.url.path == "/view"
                else fake.handle(req)
            )
        )
    else:
        data = json.loads(path.read_text())
        if change == "epoch":
            data["provider_epoch"] = "provider-epoch-0002"
        elif change == "nodes":
            data["manifest"]["nodes"]["KSampler"]["implementation"] = "sha256:" + "f" * 64
        else:
            data["expires_at"] = 0
        path.write_text(json.dumps(data))
    fake.finish(job["provider_execution_id"])
    assert (await jobs.status(job["job_id"]))["verification"]["state"] == "failed"
    assert store.list()["definitions"][0]["readiness"]["state"] == "validated"


async def test_uncertain_admission_retains_unsubmitted_reservation(verified, fake, monkeypatch):
    store, jobs, verifier, _, _ = verified
    await make_ready(verified, fake)
    original = verifier.records.write

    def uncertain(name, data):
        original(name, data)
        raise CommitUnknown("uncertain")

    monkeypatch.setattr(verifier.records, "write", uncertain)
    with pytest.raises(CommitUnknown):
        await run_verify(verified)
    assert jobs.activity()["busy"]
    assert len(fake.prompts) == 1
    assert store.list()["definitions"][0]["readiness"]["state"] == "validated"


async def test_old_finalizer_cannot_overwrite_new_attempt(verified, fake):
    _, jobs, verifier, _, _ = verified
    result = await run_verify(verified)
    job = jobs._get(result["job_id"])
    record = {**job.verification, "attempt_id": "new-attempt", "state": "failed"}
    verifier.records.write(job.definition.id + ".json", record)
    fake.finish(result["provider_execution_id"])
    await jobs.status(result["job_id"])
    assert verifier.records.read(job.definition.id + ".json") == record


async def test_no_runtime_evidence_rejects_before_provider(verified, fake):
    _, jobs, verifier, _, _ = verified
    verifier.evidence.path = None
    with pytest.raises(ValueError, match="runtime_evidence_unavailable"):
        await run_verify(verified)
    assert not jobs.activity()["busy"]
    assert not fake.prompts


async def test_ready_descriptor_restricts_checkpoint_to_measured_domain(verified, fake):
    await make_ready(verified, fake)
    item = verified[0].list()["definitions"][0]
    assert item["parameters"]["checkpoint"]["enum"] == ["base.safetensors"]


async def test_submission_deadline_keeps_unknown_reservation(verified, monkeypatch):
    import flamoris_generation_mcp.verification as module

    _, jobs, _, _, _ = verified
    provider = jobs.providers.get("comfyui")

    async def stalled(*args):
        await asyncio.Future()

    monkeypatch.setattr(provider, "submit", stalled)
    monkeypatch.setattr(module, "DEADLINE", 0.01)
    with pytest.raises(TimeoutError):
        await run_verify(verified)
    assert jobs.activity()["busy"]
    assert next(iter(jobs._jobs.values())).verification["state"] == "failed"


async def test_observation_deadline_never_attests_or_releases_active_job(verified, monkeypatch):
    import flamoris_generation_mcp.verification as module

    _, jobs, verifier, _, _ = verified
    provider = jobs.providers.get("comfyui")

    async def stalled(*args):
        await asyncio.Future()

    monkeypatch.setattr(provider, "inspect", stalled)
    monkeypatch.setattr(module, "DEADLINE", 0.01)
    result = await run_verify(verified)
    await asyncio.wait_for(asyncio.gather(*list(verifier.tasks)), timeout=1)
    assert jobs._get(result["job_id"]).verification["state"] == "failed"
    assert jobs.activity()["busy"]


@pytest.mark.parametrize(
    "field,value", [("nodes", []), ("models", []), ("nodes", {"KSampler": []})]
)
async def test_malformed_runtime_manifest_is_unavailable(verified, fake, field, value):
    _, jobs, _, path, _ = verified
    data = json.loads(path.read_text())
    data["manifest"][field] = value
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="runtime_evidence_unavailable"):
        await run_verify(verified)
    assert not jobs.activity()["busy"] and not fake.prompts


async def test_nonregular_evidence_paths_never_block(verified):
    _, _, verifier, path, _ = verified
    for target in (path, path.with_suffix(".json.lock")):
        target.unlink()
        os.mkfifo(target)
        with pytest.raises(ValueError, match="runtime_evidence_unavailable"):
            with verifier.evidence.guard():
                pass
        target.unlink()
        target.write_text("{}")


async def test_ready_checkpoint_default_matches_measured_domain_without_changing_digest(
    verified, fake
):
    store, _, _, _, _ = verified
    raw = definition()
    raw["version"] = 2
    raw["parameters"]["checkpoint"].update(required=False, default="other.safetensors")
    store.register_definition(raw)
    item = store.registry.get("image-v2")
    digest = item.digest
    await make_ready(verified, fake)
    public = store.list()["definitions"][0]
    checkpoint = public["parameters"]["checkpoint"]
    assert checkpoint["default"] == "base.safetensors"
    assert checkpoint["enum"] == [checkpoint["default"]]
    assert public["definition_digest"] == digest == item.digest
    assert item.parameters["checkpoint"].default == "other.safetensors"


async def test_previous_profile_attestation_cannot_inherit_readiness(verified, fake):
    store, _, verifier, _, _ = verified
    await make_ready(verified, fake)
    item = store.registry.get("image-v2")
    record = verifier.records.read(item.id + ".json")
    record["identity"]["profile_revision"] = 1
    verifier.records.write(item.id + ".json", record)
    assert store.list()["definitions"][0]["readiness"]["state"] == "validated"
    with pytest.raises(ValueError, match="readiness"):
        store.build(
            item.id,
            {"checkpoint": "base.safetensors", "positive_prompt": "x"},
            item.version,
            item.digest,
            True,
        )

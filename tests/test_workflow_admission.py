import asyncio
import json

import httpx
import pytest
from test_workflow_v2 import definition

from flamoris_generation_mcp.comfyui import ComfyUIClient
from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.providers.base import SubmissionUnknown
from flamoris_generation_mcp.workflows import WorkflowStore


def external_store(settings, tmp_path):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, tmp_path / "defs")
    store.register_definition(definition())
    return store


def test_build_identity_and_production_policy_fail_closed(settings, tmp_path):
    store = external_store(settings, tmp_path)
    item = store.registry.get("image-v2")
    values = {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    with pytest.raises(ValueError):
        store.build(item.id, values, definition_version=2)
    with pytest.raises(ValueError, match="digest"):
        store.build(item.id, values, definition_digest="sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="readiness"):
        store.build(item.id, values, require_ready=True)
    built = store.build(item.id, values, item.version, item.digest)
    assert built["definition_digest"] == item.digest
    store.save(built["workflow_id"])
    path = settings.workflow_dir / (built["workflow_id"] + ".json")
    raw = json.loads(path.read_text())
    raw["definition_digest"] = "sha256:" + "1" * 64
    path.write_text(json.dumps(raw))
    renewed = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, tmp_path / "defs")
    with pytest.raises(ValueError, match="digest"):
        renewed.prompt(renewed.get(built["workflow_id"]))


@pytest.mark.parametrize("kind", ["transport", "malformed", "gateway"])
async def test_post_acceptance_unknown_is_never_replayed_and_survives_restart(stores, kind):
    workflows, jobs, client = stores
    calls = []

    def handle(request):
        calls.append(request)
        if kind == "transport":
            raise httpx.ReadTimeout("after accepted", request=request)
        return (
            httpx.Response(200, content=b"not json") if kind == "malformed" else httpx.Response(502)
        )

    client.http._transport = httpx.MockTransport(handle)
    workflow = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    )
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(workflow["workflow_id"])
    assert jobs.activity()["busy"]
    assert len(calls) == 1
    with pytest.raises(GenerationBusyError):
        await jobs.submit(workflow["workflow_id"])
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    assert restarted.activity() == jobs.activity()
    key = restarted.activity()["active_job_id"]
    assert (await restarted.status(key))["status"] == "unknown"
    assert (await restarted.cancel(key))["status"] == "unknown"
    assert len(calls) == 1


async def test_cancel_during_post_keeps_unknown_reservation(stores):
    workflows, jobs, client = stores
    entered = asyncio.Event()

    async def handle(request):
        entered.set()
        await asyncio.Event().wait()

    client.http._transport = httpx.MockTransport(handle)
    key = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"}
    )["workflow_id"]
    task = asyncio.create_task(jobs.submit(key))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert jobs.activity()["busy"]


async def test_accepted_definition_replacement_retains_output_identity(settings, tmp_path, fake):
    from flamoris_generation_mcp.providers.base import GenerationRequest
    from flamoris_generation_mcp.providers.comfyui import ComfyUIProvider

    store = external_store(settings, tmp_path)
    built = store.build("image-v2", {"checkpoint": "base.safetensors", "positive_prompt": "x"})
    recipe = store.get(built["workflow_id"])
    captured = store.capture(recipe)

    def handle(request):
        response = fake.handle(request)
        if request.url.path == "/prompt":
            raw = definition()
            raw["version"] = 2
            raw["graph"]["77"] = raw["graph"].pop("7")
            raw["output_node"] = "77"
            store.register_definition(raw)
        return response

    client = ComfyUIClient(settings, httpx.MockTransport(handle))
    provider = ComfyUIProvider(client, store.catalog, store)
    try:
        result = await provider.submit(
            GenerationRequest("image.generate", built["workflow_id"], recipe, captured), "a" * 32
        )
        assert provider._output_nodes[result.execution_id] == "7"
        fake.finish()
        assert (await provider.inspect(result.execution_id)).status == "completed"
    finally:
        await client.close()

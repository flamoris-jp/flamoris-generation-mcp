"""Retirement keeps persisted identities and uncertain work fenced without old execution."""

import json

import httpx
import pytest
from mcp import Client

from flamoris_generation_mcp.durable import Records
from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.server import create_server
from flamoris_generation_mcp.workflows import WorkflowStore

RETIRED_TOOLS = {
    "workflows.register",
    "workflows.verify",
    *("workflows.v3." + name for name in ("register", "list", "build", "verify", "revoke")),
}
VALUES = {"checkpoint": "base.safetensors", "positive_prompt": "flowers"}


async def test_retired_catalog_tools_fail_and_builtin_image_still_executes(settings, fake):
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert not names & RETIRED_TOOLS
        for name in RETIRED_TOOLS:
            assert (await client.call_tool(name, {})).is_error
        custom = await client.call_tool(
            "workflows.build", {"template": "old-registered-image", "parameters": VALUES}
        )
        assert custom.is_error
        assert "retired" in custom.content[0].text
        listing = (await client.call_tool("workflows.list")).structured_content
        assert listing["definitions"] == []
        assert {item["id"] for item in listing["descriptors"]} == {
            "text-to-image",
            "text-to-image-lora",
        }
        built = (
            await client.call_tool(
                "workflows.build", {"template": "text-to-image", "parameters": VALUES}
            )
        ).structured_content
        result = await client.call_tool("jobs.submit", {"workflow_id": built["workflow_id"]})
        assert not result.is_error
        assert len(fake.prompts) == 1
        assert fake.prompts[0]["prompt"]["7"]["class_type"] == "SaveImage"


@pytest.mark.parametrize("schema", [2, 3])
def test_retired_saved_recipes_remain_discoverable_and_unchanged(settings, schema):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    settings.workflow_dir.mkdir()
    recipe_id = "a" * 32
    path = settings.workflow_dir / (recipe_id + ".json")
    content = json.dumps({"schema_version": schema, "template": "retired", "parameters": {}})
    path.write_text(content)
    assert recipe_id in store.list()["saved_workflows"]
    with pytest.raises(ValueError, match="retired"):
        store.get(recipe_id)
    with pytest.raises(ValueError, match="retired"):
        store.save(recipe_id)
    assert path.read_text() == content


@pytest.mark.parametrize("kind", ["schema2", "schema3", "delegation", "verification"])
async def test_retired_active_debt_serves_status_without_replay_or_journal_mutation(
    stores, settings, fake, kind
):
    recipes, jobs, _ = stores
    built = recipes.build("text-to-image", VALUES)
    job_id = "b" * 32
    raw = {
        "job_id": job_id,
        "workflow_id": built["workflow_id"],
        "operation": "image.generate",
        "provider_id": "comfyui",
        "execution_id": "possibly-running",
        "recipe": recipes.get(built["workflow_id"]).model_dump(mode="json"),
    }
    if kind.startswith("schema"):
        raw["recipe"]["schema_version"] = int(kind[-1])
    elif kind == "delegation":
        raw["runtime_delegation"] = {"state": "accepted", "operations": {"leaf": {}}}
    else:
        raw["verification"] = {"state": "pending"}
    Records(settings.output_dir, "job-authority").write("active.json", {"active": raw})
    path = settings.output_dir / "job-authority/active.json"
    original = path.read_bytes()
    restored = JobStore(recipes, jobs.providers, jobs.capabilities, settings.output_dir)
    assert restored.activity()["busy"]
    assert restored.activity()["retired_execution_requires_reconciliation"]
    assert (await restored.status(job_id))["status"] == "unknown"
    assert (await restored.status(job_id))["retired_feature"] is True
    for call in (restored.cancel, restored.result):
        with pytest.raises(ValueError, match="Retired execution remains reserved"):
            await call(job_id)
    with pytest.raises(GenerationBusyError):
        await restored.submit(built["workflow_id"])
    assert fake.calls == []
    assert path.read_bytes() == original
    # Another restart must not treat opaque debt as terminal or discard its record.
    restarted = JobStore(recipes, jobs.providers, jobs.capabilities, settings.output_dir)
    assert restarted.activity()["busy"] and path.read_bytes() == original


async def test_retained_builtin_restart_keeps_exclusion_and_no_resubmission(stores, settings, fake):
    recipes, jobs, _ = stores
    built = recipes.build("text-to-image", VALUES)
    job_id = (await jobs.submit(built["workflow_id"]))["job_id"]
    restored = JobStore(recipes, jobs.providers, jobs.capabilities, settings.output_dir)
    assert restored.activity()["busy"]
    with pytest.raises(GenerationBusyError):
        await restored.submit(built["workflow_id"])
    assert (await restored.status(job_id))["status"] == "queued"
    fake.finish()
    assert (await restored.status(job_id))["status"] == "completed"
    assert not restored.activity()["busy"]
    assert len(fake.prompts) == 1


@pytest.mark.parametrize("schema", [2, 3])
async def test_completed_retired_recipe_archives_remain_readable(stores, settings, fake, schema):
    recipes, jobs, _ = stores
    built = recipes.build("text-to-image", VALUES)
    job_id = (await jobs.submit(built["workflow_id"]))["job_id"]
    fake.finish()
    await jobs.result(job_id)
    path = settings.output_dir / job_id / "metadata.json"
    archive = json.loads(path.read_bytes())
    archive.update(schema_version=schema, template="retired-custom-image")
    path.write_text(json.dumps(archive))
    restored = JobStore(recipes, jobs.providers, jobs.capabilities, settings.output_dir)
    assert (await restored.status(job_id))["status"] == "completed"
    asset_id = (await restored.list_assets(job_id))["assets"][0]["asset_id"]
    assert (await restored.get_asset(asset_id))[1] == b"image fixture"
    assert (await restored.delete_asset(asset_id))["deleted"]
    assert path.exists()


@pytest.mark.parametrize(
    "content",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":1,"parameters":{"cfg":NaN}}',
        b'{"schema_version":true}',
    ],
)
def test_retained_saved_recipe_json_keeps_strict_validation(settings, content):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    settings.workflow_dir.mkdir()
    recipe_id = "c" * 32
    (settings.workflow_dir / (recipe_id + ".json")).write_bytes(content)
    with pytest.raises(ValueError):
        store.get(recipe_id)

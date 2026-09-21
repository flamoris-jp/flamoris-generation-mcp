import base64
import sys

import httpx
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from flamoris_generation_mcp.server import create_server

TOOL_NAMES = {
    "system.health",
    "models.list",
    "models.get",
    "workflows.list",
    "workflows.build",
    "workflows.save",
    "jobs.submit",
    "jobs.status",
    "jobs.result",
    "jobs.cancel",
    "assets.list",
    "assets.get",
}


async def test_mcp_protocol_validation_and_generation(settings, fake):
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == TOOL_NAMES
        assert (await client.call_tool("system.health")).structured_content["healthy"]
        models = await client.call_tool("models.list", {"kind": "checkpoint"})
        assert len(models.structured_content["models"]) == 1
        detail = await client.call_tool("models.get", {"model_id": "checkpoint:base.safetensors"})
        assert detail.structured_content["kind"] == "checkpoint"
        assert (await client.call_tool("models.list", {"kind": "invalid"})).is_error
        bad = await client.call_tool(
            "workflows.build",
            {
                "template": "text-to-image",
                "parameters": {
                    "checkpoint": "base.safetensors",
                    "positive_prompt": "x",
                    "width": 513,
                },
            },
        )
        assert bad.is_error
        assert (await client.call_tool("jobs.submit", {"prompt": {}})).is_error
        workflow = await client.call_tool(
            "workflows.build",
            {
                "template": "text-to-image",
                "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
            },
        )
        assert not workflow.is_error
        key = workflow.structured_content["workflow_id"]
        assert not (await client.call_tool("workflows.save", {"workflow_id": key})).is_error
        listing = await client.call_tool("workflows.list")
        assert key in listing.structured_content["saved_workflows"]
        submission = await client.call_tool("jobs.submit", {"workflow_id": key})
        job_id = submission.structured_content["job_id"]
        busy = await client.call_tool("jobs.submit", {"workflow_id": key})
        assert busy.is_error
        assert "Generation is busy" in busy.content[0].text
        status = await client.call_tool("jobs.status", {"job_id": job_id})
        assert status.structured_content["status"] == "queued"
        fake.finish()
        result = await client.call_tool("jobs.result", {"job_id": job_id})
        assert result.structured_content["status"] == "completed"
        assert len(result.structured_content["files"]) == 1

        assets = await client.call_tool("assets.list", {"job_id": job_id})
        asset_id = assets.structured_content["assets"][0]["asset_id"]
        media = await client.call_tool("assets.get", {"asset_id": asset_id})
        assert not media.is_error
        assert media.structured_content is None
        assert len(media.content) == 1
        image = media.content[0]
        assert image.type == "image"
        assert image.mime_type == "image/png"
        assert base64.b64decode(image.data) == b"image fixture"

        assert not (await client.call_tool("jobs.cancel", {"job_id": job_id})).is_error


async def test_real_stdio_startup_and_tools(tmp_path):
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flamoris_generation_mcp.server"],
        cwd=tmp_path,
        env={"FLAMORIS_MODEL_ROOT": str(tmp_path / "models")},
    )
    async with Client(server, read_timeout_seconds=10) as client:
        assert {tool.name for tool in (await client.list_tools()).tools} == TOOL_NAMES
        assert (await client.call_tool("models.list")).structured_content == {"models": []}


async def test_asset_size_limit_is_actionable_over_mcp(settings, fake, monkeypatch):
    monkeypatch.setattr("flamoris_generation_mcp.jobs.MAX_ASSET_BYTES", 4)
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        workflow = await client.call_tool(
            "workflows.build",
            {
                "template": "text-to-image",
                "parameters": {
                    "checkpoint": "base.safetensors",
                    "positive_prompt": "flowers",
                },
            },
        )
        submission = await client.call_tool(
            "jobs.submit", {"workflow_id": workflow.structured_content["workflow_id"]}
        )
        fake.finish()
        assets = await client.call_tool(
            "assets.list", {"job_id": submission.structured_content["job_id"]}
        )
        asset_id = assets.structured_content["assets"][0]["asset_id"]

        result = await client.call_tool("assets.get", {"asset_id": asset_id})

        assert result.is_error
        assert "retrieval limit" in result.content[0].text

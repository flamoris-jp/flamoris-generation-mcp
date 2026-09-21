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
        status = await client.call_tool("jobs.status", {"job_id": job_id})
        assert status.structured_content["status"] == "queued"
        fake.finish()
        result = await client.call_tool("jobs.result", {"job_id": job_id})
        assert result.structured_content["status"] == "completed"
        assert len(result.structured_content["files"]) == 1
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

import asyncio
import base64
import socket
from contextlib import asynccontextmanager
from unittest.mock import Mock

import httpx
import pytest
import uvicorn
from mcp import Client
from pydantic import ValidationError
from test_mcp import TOOL_NAMES

from flamoris_generation_mcp import server as server_module
from flamoris_generation_mcp.config import Settings
from flamoris_generation_mcp.healthcheck import main as probe_liveness
from flamoris_generation_mcp.server import create_server, main


@pytest.fixture
def clean_transport_env(monkeypatch):
    for suffix in ("MCP_TRANSPORT", "HTTP_HOST", "HTTP_PORT", "MCP_PATH"):
        monkeypatch.delenv("FLAMORIS_" + suffix, raising=False)


def test_default_stdio_and_http_defaults(monkeypatch, clean_transport_env):
    factory = Mock()
    monkeypatch.setattr(server_module, "create_server", factory)
    main([])
    factory.assert_called_once()
    settings = factory.call_args.args[0]
    assert settings.mcp_transport == "stdio"
    assert (settings.http_host, settings.http_port, settings.mcp_path) == (
        "127.0.0.1",
        8765,
        "/mcp",
    )
    factory.return_value.run.assert_called_once_with(transport="stdio")
    factory.reset_mock()
    main(["--transport", "streamable-http"])
    factory.return_value.run.assert_called_once_with(
        transport="streamable-http", host="127.0.0.1", port=8765, streamable_http_path="/mcp"
    )


def test_environment_and_cli_precedence(monkeypatch, clean_transport_env):
    monkeypatch.setenv("FLAMORIS_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("FLAMORIS_HTTP_HOST", "localhost")
    monkeypatch.setenv("FLAMORIS_HTTP_PORT", "9876")
    monkeypatch.setenv("FLAMORIS_MCP_PATH", "/generation/mcp")
    monkeypatch.setenv("FLAMORIS_COMFYUI_URL", "http://localhost:8188/provider/")
    factory = Mock()
    monkeypatch.setattr(server_module, "create_server", factory)
    main([])
    factory.return_value.run.assert_called_once_with(
        transport="streamable-http",
        host="localhost",
        port=9876,
        streamable_http_path="/generation/mcp",
    )
    assert str(factory.call_args.args[0].comfyui_url) == "http://localhost:8188/provider/"
    factory.reset_mock()
    main(["--host", "::1", "--port", "8766", "--mcp-path", "/api/mcp"])
    factory.return_value.run.assert_called_once_with(
        transport="streamable-http", host="::1", port=8766, streamable_http_path="/api/mcp"
    )
    factory.reset_mock()
    main(["--transport", "stdio"])
    factory.return_value.run.assert_called_once_with(transport="stdio")


@pytest.mark.parametrize(
    "values",
    [
        {"mcp_transport": "sse"},
        {"http_port": 0},
        {"http_port": 65536},
        {"http_host": ""},
        {"http_host": "http://localhost"},
        {"mcp_path": "mcp"},
        {"mcp_path": "/mcp?x=1"},
        {"mcp_path": "/mcp#fragment"},
        {"mcp_path": "//mcp"},
        {"mcp_path": "/../mcp"},
        {"mcp_path": "/{route}"},
        {"mcp_path": "/mcp/"},
        {"mcp_path": "/healthz"},
        {"mcp_path": "/%6dcp"},
    ],
)
def test_invalid_transport_configuration(values):
    with pytest.raises(ValidationError):
        Settings(**values)


def test_bad_cli_exits_before_constructing_provider(monkeypatch, capsys, clean_transport_env):
    factory = Mock()
    monkeypatch.setattr(server_module, "create_server", factory)
    with pytest.raises(SystemExit) as exc:
        main(["--port", "0"])
    assert exc.value.code == 2
    factory.assert_not_called()
    assert "error:" in capsys.readouterr().err


def test_health_path_is_reserved_before_server_start(monkeypatch, capsys, clean_transport_env):
    factory = Mock()
    monkeypatch.setattr(server_module, "create_server", factory)
    monkeypatch.setenv("FLAMORIS_MCP_PATH", "/healthz")
    with pytest.raises(SystemExit) as exc:
        main(["--transport", "streamable-http"])
    assert exc.value.code == 2
    factory.assert_not_called()
    assert "/healthz is reserved" in capsys.readouterr().err


@asynccontextmanager
async def serve_http(server, settings):
    # Retain the socket to avoid a free-port selection race; all traffic is local.
    with socket.socket() as listener:
        listener.bind((settings.http_host, 0))
        settings.http_port = listener.getsockname()[1]
        app = server.streamable_http_app(
            host=settings.http_host, streamable_http_path=settings.mcp_path
        )
        runner = uvicorn.Server(
            uvicorn.Config(
                app,
                host=settings.http_host,
                port=settings.http_port,
                log_level="warning",
                lifespan="on",
                timeout_graceful_shutdown=2,
            )
        )
        task = asyncio.create_task(runner.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not runner.started:
                    if task.done():
                        await task
                        raise AssertionError("HTTP server exited before startup")
                    await asyncio.sleep(0.01)
            yield f"http://{settings.http_host}:{settings.http_port}"
        finally:
            runner.should_exit = True
            try:
                await asyncio.wait_for(task, timeout=5)
            finally:
                if not task.done():
                    task.cancel()


async def test_http_tools_validation_and_shared_authority(settings, fake, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")  # This test only connects to its loopback server.
    monkeypatch.setenv("no_proxy", "*")
    settings.mcp_path = "/api/generation"
    factories = {}
    for name in ("ModelCatalog", "WorkflowStore", "ComfyUIClient", "JobStore"):
        factory = Mock(wraps=getattr(server_module, name))
        monkeypatch.setattr(server_module, name, factory)
        factories[name] = factory
    provider_transport = httpx.MockTransport(fake.handle)
    server = create_server(settings, transport=provider_transport)
    async with serve_http(server, settings) as base_url:
        monkeypatch.setenv("FLAMORIS_HTTP_PORT", str(settings.http_port))
        await asyncio.to_thread(probe_liveness)
        async with httpx.AsyncClient(trust_env=False) as http:
            live = await http.get(base_url + "/healthz", timeout=2)
            assert live.status_code == 200
            assert live.json() == {"healthy": True}
            assert (await http.post(base_url + "/mcp", json={})).status_code == 404
            response = await http.post(
                base_url + settings.mcp_path, json={}, headers={"Host": "untrusted.example"}
            )
            assert response.status_code == 421  # SDK protection was not disabled.
        async with Client(base_url + settings.mcp_path) as first:
            assert {tool.name for tool in (await first.list_tools()).tools} == TOOL_NAMES
            assert (await first.call_tool("models.list", {"kind": "invalid"})).is_error
            workflow = await first.call_tool(
                "workflows.build",
                {
                    "template": "text-to-image",
                    "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
                },
            )
            key = workflow.structured_content["workflow_id"]
            # A different concurrent HTTP session can use the same unsaved workflow.
            async with Client(base_url + settings.mcp_path) as second:
                listing = await second.call_tool("workflows.list")
                assert key in listing.structured_content["built_workflows"]
                submitted = await second.call_tool("jobs.submit", {"workflow_id": key})
                job_id = submitted.structured_content["job_id"]
            # The process-owned guard is shared across independent HTTP sessions.
            busy = await first.call_tool("jobs.submit", {"workflow_id": key})
            assert busy.is_error
            assert "Generation is busy" in busy.content[0].text
            # Disconnecting that session must not close the shared provider client.
            status = await first.call_tool("jobs.status", {"job_id": job_id})
            assert status.structured_content["status"] == "queued"
        # No live client remains; reconnecting still sees the same job and provider.
        fake.finish()
        async with Client(base_url + settings.mcp_path) as third:
            result = await third.call_tool("jobs.result", {"job_id": job_id})
            assert result.structured_content["status"] == "completed"
            assert len(result.structured_content["files"]) == 1
            assets = await third.call_tool("assets.list", {"job_id": job_id})
            asset_id = assets.structured_content["assets"][0]["asset_id"]
            media = await third.call_tool("assets.get", {"asset_id": asset_id})
            assert not media.is_error
            assert media.structured_content is None
            assert media.content[0].type == "image"
            assert media.content[0].mime_type == "image/png"
            assert base64.b64decode(media.content[0].data) == b"image fixture"
    for factory in factories.values():
        factory.assert_called_once()
    assert len(fake.prompts) == 1
    assert fake.download_count == 1

import asyncio

import httpx
from flamoris_generation_controller.contracts import API_PATH
from mcp import Client
from pydantic import SecretStr
from test_http import serve_http

from flamoris_generation_mcp.config import Settings
from flamoris_generation_mcp.server import create_server

TOKEN = "fixture-controller-service-credential-32"
BUILD = {
    "template": "text-to-image",
    "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
}


def test_empty_controller_token_keeps_external_mcp_configuration_usable(monkeypatch):
    monkeypatch.setenv("FLAMORIS_CONTROLLER_TOKEN", "")
    assert Settings.from_env().controller_token is None


async def test_unconfigured_internal_http_does_not_disable_mcp(settings, fake, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    assert not {"controller_token", "provenance_secret", "mcp_path"} & set(
        type(server.controller.settings).model_fields
    )
    async with serve_http(server, settings) as base_url:
        async with httpx.AsyncClient(trust_env=False) as http:
            response = await http.post(base_url + API_PATH + "/workflows.build", json=BUILD)
            assert response.status_code == 503
        async with Client(base_url + settings.mcp_path) as client:
            assert not (await client.call_tool("system.health")).is_error


async def test_external_mcp_and_internal_http_contend_for_one_authority(
    settings, fake, monkeypatch
):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    settings.controller_token = SecretStr(TOKEN)
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with serve_http(server, settings) as base_url:
        async with httpx.AsyncClient(
            base_url=base_url + API_PATH + "/",
            trust_env=False,
            headers={"Authorization": "Bearer " + TOKEN},
        ) as http:
            async with Client(base_url + settings.mcp_path) as external:
                recipe = (await http.post("workflows.build", json=BUILD)).json()["workflow_id"]
                args = {"workflow_id": recipe}
                first, second = await asyncio.gather(
                    http.post("jobs.submit", json=args), external.call_tool("jobs.submit", args)
                )
                assert (first.status_code == 200) != (not second.is_error)
                assert len(fake.prompts) == 1
                assert first.status_code in {200, 409}
                assert server.controller.jobs.activity()["busy"]
            # MCP session disconnect does not close shared providers or release the owner.
            health = await http.post("system.health", json={})
            assert health.status_code == 200 and health.json()["busy"]
            response = await http.post("jobs.submit", json=args)
            assert response.status_code == 409 and len(fake.prompts) == 1
    assert server.controller._closed


async def test_internal_api_does_not_accept_mcp_credentials_or_context(settings, fake):
    settings.controller_token = SecretStr(TOKEN)
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with serve_http(server, settings) as base_url:
        async with httpx.AsyncClient(base_url=base_url + API_PATH + "/", trust_env=False) as http:
            response = await http.post(
                "workflows.build", json=BUILD, headers={"X-Provenance-Subject": "forged"}
            )
            assert response.status_code == 401
            response = await http.post(
                "workflows.build",
                json={**BUILD, "provenance": {"subject": "forged"}},
                headers={"Authorization": "Bearer " + TOKEN},
            )
            assert response.status_code == 400
            response = await http.post(
                "workflows.register", json={}, headers={"Authorization": "Bearer " + TOKEN}
            )
            assert response.status_code == 404
            assert server.controller.workflows._recipes == {}

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
from flamoris_generation_controller.config import MODEL_FOLDERS
from flamoris_generation_controller.contracts import API_PATH
from mcp import Client
from pydantic import SecretStr
from test_http import serve_http
from test_input_uploads import image

from flamoris_generation_mcp.server import create_server


async def test_qwen_mcp_register_get_and_shared_http_image_execution(
    settings, fake, tmp_path, monkeypatch
):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    fixture = json.loads((Path(__file__).parent / "fixtures/qwen-declared.json").read_text())
    body, info = fixture["registration"], fixture["object_info"]
    shared = tmp_path / "shared"
    shared.mkdir()
    settings = settings.model_copy(
        update={
            "comfyui_input_root": shared,
            "declared_comfy_nodes": list(info),
            "controller_token": SecretStr("fixture-controller-credential-32chars"),
        }
    )
    for spec in body["declaration"]["parameters"].values():
        if spec["role"] == "model":
            path = settings.model_root / MODEL_FOLDERS[spec["model_kind"]] / spec["default"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture only")

    def handle(request):
        if request.url.path.startswith("/object_info/"):
            cls = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={cls: info[cls]})
        return fake.handle(request)

    server = create_server(settings, transport=httpx.MockTransport(handle))
    async with serve_http(server, settings) as base:
        async with (
            Client(base + settings.mcp_path) as client,
            httpx.AsyncClient(
                base_url=base + API_PATH + "/",
                trust_env=False,
                headers={"Authorization": "Bearer fixture-controller-credential-32chars"},
            ) as internal,
        ):
            result = await client.call_tool("comfy.register", body)
            assert not result.is_error
            definition = result.structured_content
            got = (
                await client.call_tool("comfy.get", {"definition_id": definition["id"]})
            ).structured_content
            assert (
                got["graph"] == body["graph"]
                and got["readiness"]["live_provider_verified"] is False
            )
            raw = image()
            digest = hashlib.sha256(raw).hexdigest()
            upload_id = uuid4().hex
            for tool, args in [
                (
                    "inputs.upload.begin",
                    {
                        "upload_id": upload_id,
                        "mime_type": "image/png",
                        "size_bytes": len(raw),
                        "sha256": digest,
                    },
                ),
                (
                    "inputs.upload.write",
                    {
                        "upload_id": upload_id,
                        "offset": 0,
                        "data_base64": base64.b64encode(raw).decode(),
                        "chunk_sha256": digest,
                    },
                ),
            ]:
                assert not (await client.call_tool(tool, args)).is_error
            uploaded = (
                await client.call_tool("inputs.upload.finish", {"upload_id": upload_id})
            ).structured_content
            built = await internal.post(
                "workflows.build",
                json={
                    "template": definition["id"],
                    "parameters": {"reference_image": uploaded["input_id"], "instruction": "Smile"},
                },
            )
            assert built.status_code == 200, built.text
            args = {"workflow_id": built.json()["workflow_id"]}
            job = (await client.call_tool("jobs.submit", args)).structured_content
            assert job["managed_inputs"]["reference_image"]["sha256"] == digest
            assert (await internal.post("jobs.submit", json=args)).status_code == 409
            assert len(fake.prompts) == 1
            prompt = fake.prompts[0]["prompt"]
            assert prompt["5"]["inputs"]["prompt"] == "Smile"
            assert prompt["4"]["inputs"]["image"].startswith("flamoris-inputs/")
            fake.finish()
            fake.history["prompt-1"]["outputs"]["11"] = fake.history["prompt-1"]["outputs"].pop("7")
            result = await internal.post("jobs.result", json={"job_id": job["job_id"]})
            assert result.status_code == 200 and result.json()["status"] == "completed"
            assets = (
                await client.call_tool("assets.list", {"job_id": job["job_id"]})
            ).structured_content
            assert not (
                await client.call_tool("assets.get", {"asset_id": assets["assets"][0]["asset_id"]})
            ).is_error

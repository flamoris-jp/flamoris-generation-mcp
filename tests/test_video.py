"""Synthetic Wan node fixtures exercise the matched MCP and HTTP authority."""

import base64
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4

import av
import httpx
import pytest
from flamoris_generation_controller.contracts import API_PATH
from mcp import Client
from PIL import Image
from pydantic import SecretStr
from test_http import serve_http
from test_input_uploads import image

from flamoris_generation_mcp.server import create_server


def clip(fps=16, frames=5):
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        for _ in range(frames):
            for packet in stream.encode(av.VideoFrame.from_image(Image.new("RGB", (64, 64)))):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


@pytest.mark.parametrize("mode", ["i2v", "flf2v"])
@pytest.mark.parametrize("output_schema", ["legacy", "native_v3"])
@pytest.mark.parametrize("family", ["wan", "h3"])
async def test_mcp_video_and_internal_http_share_inputs_jobs_and_verified_transfer(
    settings, fake, tmp_path, monkeypatch, mode, output_schema, family
):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    fixture = json.loads((Path(__file__).parent / "fixtures/wan-video-contract.json").read_text())[
        mode
    ]
    body, info = fixture["registration"], fixture["object_info"]
    if family == "h3":
        legacy_output = info["SaveVideo"]
        fixture = json.loads(
            (Path(__file__).parent / "fixtures/minimax-h3-contract.json").read_text()
        )
        body, info = fixture["registration"], fixture["object_info"]
        info["SaveVideo"] = legacy_output
        output = body["graph"]["13"]["inputs"]
        output["codec"] = output.pop("format.codec")
        for target in body["declaration"]["constants"]:
            if target == {"node": "13", "input": "format.codec"}:
                target["input"] = "codec"
        for folder in ("diffusion_models", "text_encoders", "vae"):
            path = settings.model_root / folder / "fixture.safetensors"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic fixture, not model weights")
        if mode == "flf2v":
            body["declaration"]["video"]["mode"] = mode
            body["graph"]["14"] = {"class_type": "LoadImage", "inputs": {"image": "$end_image"}}
            body["graph"]["5"]["inputs"]["last_frame"] = ["14", 0]
            body["declaration"]["parameters"]["end_image"] = {
                "type": "string",
                "role": "managed_input",
                "label": "End",
                "required": True,
                "targets": [{"node": "14", "input": "image"}],
            }
    output_id = body["declaration"]["output"]["node"]
    if output_schema == "native_v3":
        native = json.loads((Path(__file__).parent / "fixtures/native-video-v3.json").read_text())
        info.update(native["node_specs"])
        output = body["graph"][output_id]["inputs"]
        output["format.codec"] = output.pop("codec")
        for target in body["declaration"]["constants"]:
            if target == {"node": output_id, "input": "codec"}:
                target["input"] = "format.codec"
    shared = tmp_path / "shared"
    shared.mkdir()
    settings = settings.model_copy(
        update={
            "comfyui_input_root": shared,
            "declared_comfy_nodes": list(info),
            "controller_token": SecretStr("fixture-controller-credential-32chars"),
        }
    )
    data = clip(24, 22) if family == "h3" else clip()

    def handle(request):
        if request.url.path.startswith("/object_info/"):
            cls = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={cls: info[cls]})
        if request.url.path == "/view":
            return httpx.Response(200, content=data)
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
            catalog = (await client.call_tool("capabilities.list", {})).structured_content
            assert any(c["id"] == "video.generate" for c in catalog["capabilities"])
            definition = (await client.call_tool("comfy.register", body)).structured_content
            assert definition["video"]["mode"] == mode
            assert definition["video"]["profile"] == (
                "minimax-h3-video-v1" if family == "h3" else "wan-video-v1"
            )
            assert definition["readiness"]["generation_verified"] is False
            images = {}
            for role in ("start_image", "end_image") if mode == "flf2v" else ("start_image",):
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
                images[role] = (
                    await client.call_tool("inputs.upload.finish", {"upload_id": upload_id})
                ).structured_content["input_id"]
            response = await internal.post(
                "workflows.build",
                json={
                    "template": definition["id"],
                    "definition_digest": definition["definition_digest"],
                    "parameters": images,
                },
            )
            assert response.status_code == 200, response.text
            args = {"workflow_id": response.json()["workflow_id"]}
            submitted = await client.call_tool("jobs.submit", args)
            assert not submitted.is_error
            job = submitted.structured_content
            assert set(job["managed_inputs"]) == set(images)
            assert (await internal.post("jobs.submit", json=args)).status_code == 409
            assert len(fake.prompts) == 1
            output = fake.prompts[0]["prompt"][output_id]["inputs"]
            if output_schema == "native_v3":
                assert output["format.codec"] == "h264" and "codec" not in output
            prefix = output["filename_prefix"]
            fake.finish()
            fake.history["prompt-1"]["outputs"] = {
                output_id: {
                    "images": [
                        {
                            "filename": prefix.split("/")[-1] + "_00001_.mp4",
                            "subfolder": "flamoris",
                            "type": "output",
                        }
                    ]
                }
            }
            result = await internal.post("jobs.result", json={"job_id": job["job_id"]})
            assert result.status_code == 200 and result.json()["status"] == "completed"
            assets = (
                await client.call_tool("assets.list", {"job_id": job["job_id"]})
            ).structured_content
            asset = assets["assets"][0]
            assert asset["mime_type"] == "video/mp4" and asset["role"] == "video_clip"
            receipt = (
                await client.call_tool("assets.prepare", {"asset_id": asset["asset_id"]})
            ).structured_content
            read = await internal.post(
                "assets.read",
                json={"asset_id": asset["asset_id"], "sha256": receipt["sha256"], "offset": 0},
            )
            assert read.status_code == 200
            assert base64.b64decode(read.json()["data_base64"]) == data
            assert hashlib.sha256(data).hexdigest() == receipt["sha256"]
            assert not list(shared.rglob("*.png"))

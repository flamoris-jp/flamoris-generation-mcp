import base64
import hashlib
from uuid import uuid4

import httpx
from flamoris_generation_controller.registered_comfy import image_prompt
from flamoris_generation_controller.workflows import Parameters
from mcp import Client
from test_input_uploads import image

from flamoris_generation_mcp.server import create_server


async def test_chatgpt_register_upload_reference_generate_and_retrieve(settings, fake, tmp_path):
    root = tmp_path / "comfy-input"
    root.mkdir()
    settings = settings.model_copy(update={"comfyui_input_root": root})
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        graph = image_prompt(
            Parameters(checkpoint="base.safetensors", positive_prompt="flowers"), reference=True
        )
        definition = (
            await client.call_tool("comfy.register", {"name": "Reference", "graph": graph})
        ).structured_content
        retrieved = (
            await client.call_tool("comfy.get", {"definition_id": definition["id"]})
        ).structured_content
        assert retrieved["graph"] == graph
        raw = image()
        digest, upload_id = hashlib.sha256(raw).hexdigest(), uuid4().hex
        assert not (
            await client.call_tool(
                "inputs.upload.begin",
                {
                    "upload_id": upload_id,
                    "mime_type": "image/png",
                    "size_bytes": len(raw),
                    "sha256": digest,
                },
            )
        ).is_error
        assert not (
            await client.call_tool(
                "inputs.upload.write",
                {
                    "upload_id": upload_id,
                    "offset": 0,
                    "data_base64": base64.b64encode(raw).decode(),
                    "chunk_sha256": digest,
                },
            )
        ).is_error
        uploaded = (
            await client.call_tool("inputs.upload.finish", {"upload_id": upload_id})
        ).structured_content
        built = (
            await client.call_tool(
                "workflows.build",
                {
                    "template": definition["id"],
                    "parameters": {"reference_image": uploaded["input_id"]},
                },
            )
        ).structured_content
        job = (
            await client.call_tool("jobs.submit", {"workflow_id": built["workflow_id"]})
        ).structured_content
        assert job["managed_inputs"]["reference_image"]["sha256"] == digest
        assert fake.prompts[0]["prompt"]["8"]["inputs"]["image"].startswith("flamoris-inputs/")
        fake.finish()
        result = (
            await client.call_tool("jobs.result", {"job_id": job["job_id"]})
        ).structured_content
        assert result["status"] == "completed"
        assets = (
            await client.call_tool("assets.list", {"job_id": job["job_id"]})
        ).structured_content
        content = await client.call_tool(
            "assets.get", {"asset_id": assets["assets"][0]["asset_id"]}
        )
        assert not content.is_error and content.content[0].type == "image"

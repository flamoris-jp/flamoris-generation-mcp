import base64
import hashlib
from uuid import uuid4

import httpx
from flamoris_generation_controller.reference_comfy import conditioning_prompt
from flamoris_generation_controller.workflows import Conditioning, Parameters
from mcp import Client
from test_input_uploads import image

from flamoris_generation_mcp.server import create_server


async def test_mcp_reference_roles_models_build_submission_and_result(settings, fake, tmp_path):
    root = tmp_path / "comfy-input"
    root.mkdir()
    settings = settings.model_copy(update={"comfyui_input_root": root})
    for folder, name in (
        ("ipadapter", "adapter.safetensors"),
        ("clip_vision", "vision.safetensors"),
        ("controlnet", "control.safetensors"),
    ):
        path = settings.model_root / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"metadata fixture only")
    graph = conditioning_prompt(
        Parameters(checkpoint="base.safetensors", positive_prompt="A new scene"),
        Conditioning(
            character_adapter="adapter.safetensors",
            style_adapter="adapter.safetensors",
            clip_vision="vision.safetensors",
            controlnet="control.safetensors",
        ),
        ("initial_image", "character_image", "style_image", "control_image"),
    )
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        listing = await client.call_tool("models.list", {"kind": "ipadapter"})
        assert listing.structured_content["models"][0]["name"] == "adapter.safetensors"
        registered = await client.call_tool(
            "comfy.register", {"name": "References", "graph": graph}
        )
        assert not registered.is_error
        definition = registered.structured_content
        assert definition["readiness"]["live_provider_verified"] is False
        raw = image()
        digest = hashlib.sha256(raw).hexdigest()
        images = {}
        for role in definition["image"]["reference_roles"]:
            upload_id = uuid4().hex
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
            uploaded = await client.call_tool("inputs.upload.finish", {"upload_id": upload_id})
            assert not uploaded.is_error
            images[role] = uploaded.structured_content["input_id"]
        built = await client.call_tool(
            "workflows.build",
            {
                "template": definition["id"],
                "definition_digest": definition["definition_digest"],
                "parameters": {**images, "character_strength": 0.4, "style_strength": 0.3},
            },
        )
        assert not built.is_error and built.structured_content["schema_version"] == 8
        job = await client.call_tool(
            "jobs.submit", {"workflow_id": built.structured_content["workflow_id"]}
        )
        assert not job.is_error
        assert set(job.structured_content["managed_inputs"]) == set(images)
        assert fake.prompts[0]["prompt"]["22"]["inputs"]["weight"] == 0.4
        assert fake.prompts[0]["prompt"]["32"]["inputs"]["weight"] == 0.3
        assert len(list((root / "flamoris-inputs").glob("*.png"))) == 4
        fake.finish()
        result = await client.call_tool("jobs.result", {"job_id": job.structured_content["job_id"]})
        assert not result.is_error and result.structured_content["status"] == "completed"
        assert not list((root / "flamoris-inputs").glob("*.png"))

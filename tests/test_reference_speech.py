import base64
import hashlib
import io
import json
import re
import wave
from uuid import uuid4

import httpx
from mcp import Client

from flamoris_generation_mcp.server import create_server


async def test_external_mcp_reference_upload_build_and_result(settings):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 2400)
    data = stream.getvalue()
    posted, deleted = [], []

    async def handle(request):
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "runtime": {"loaded": True},
                    "model": {
                        "id": "irodori-tts",
                        "hf_checkpoint": "Aratako/Irodori-TTS-v4.1-Small",
                    },
                },
            )
        if request.url.path == "/v1/audio/voices":
            key = re.search(rb"flamoris_[0-9a-f]{32}", request.content).group().decode()
            assert data in request.content
            return httpx.Response(
                201,
                json={
                    "id": key,
                    "object": "voice_file",
                    "filename": key + ".wav",
                    "bytes": len(data),
                },
            )
        if request.method == "DELETE":
            key = request.url.path.rsplit("/", 1)[-1]
            deleted.append(key)
            return httpx.Response(200, json={"id": key, "deleted": True})
        assert request.url.path == "/v1/audio/speech"
        posted.append(json.loads(request.content))
        return httpx.Response(200, content=data, headers={"content-type": "audio/wav"})

    settings.irodori_url = "http://127.0.0.1:8088"
    server = create_server(settings, transport=httpx.MockTransport(handle))
    async with Client(server) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert (
            "audio/wav"
            in tools["inputs.upload.begin"].input_schema["properties"]["mime_type"]["enum"]
        )
        key = uuid4().hex
        digest = hashlib.sha256(data).hexdigest()
        assert not (
            await client.call_tool(
                "inputs.upload.begin",
                {
                    "upload_id": key,
                    "mime_type": "audio/wav",
                    "size_bytes": len(data),
                    "sha256": digest,
                },
            )
        ).is_error
        assert not (
            await client.call_tool(
                "inputs.upload.write",
                {
                    "upload_id": key,
                    "offset": 0,
                    "data_base64": base64.b64encode(data).decode(),
                    "chunk_sha256": digest,
                },
            )
        ).is_error
        receipt = (
            await client.call_tool("inputs.upload.finish", {"upload_id": key})
        ).structured_content
        assert receipt["media_kind"] == "audio"
        built = (
            await client.call_tool(
                "workflows.build",
                {
                    "template": "speech-reference",
                    "parameters": {"text": "Reference", "reference_audio": key},
                },
            )
        ).structured_content
        assert built["speech"]["reference_audio"] is True
        job = (
            await client.call_tool("jobs.submit", {"workflow_id": built["workflow_id"]})
        ).structured_content
        speech = server.controller.providers.get("irodori")
        await speech._runs[job["provider_execution_id"]].worker
        status = (
            await client.call_tool("jobs.status", {"job_id": job["job_id"]})
        ).structured_content
        assert status["status"] == "completed"
        assert status["managed_inputs"]["reference_audio"]["sha256"] == digest
        asset = (
            await client.call_tool("assets.list", {"job_id": job["job_id"]})
        ).structured_content["assets"][0]
        prepared = (
            await client.call_tool("assets.prepare", {"asset_id": asset["asset_id"]})
        ).structured_content
        chunk = (
            await client.call_tool(
                "assets.read",
                {
                    "asset_id": asset["asset_id"],
                    "sha256": prepared["sha256"],
                    "offset": 0,
                },
            )
        ).structured_content
        assert base64.b64decode(chunk["data_base64"]) == data
        assert posted[0]["irodori"]["no_ref"] is False
        assert deleted == [posted[0]["voice"]]

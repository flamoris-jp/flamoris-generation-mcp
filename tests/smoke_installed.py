"""Run with an installed wheel's Python, outside the source directory; no provider needed."""

import asyncio
import base64
import hashlib
import io
import os
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from uuid import uuid4

import httpx
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from PIL import Image

from flamoris_generation_mcp.providers.comfyui_inputs import ComfyUIInputs


async def check_tools(client):
    tools = await client.list_tools()
    assert len(tools.tools) == 25
    assert {
        "capabilities.list",
        "workflows.register",
        "workflows.verify",
        "jobs.submit",
        "assets.get",
        "assets.delete",
        "assets.prepare",
        "inputs.create",
        "inputs.get",
        "inputs.delete",
        "assets.read",
    } <= {tool.name for tool in tools.tools}
    result = await client.call_tool("models.list")
    assert not result.is_error and result.structured_content == {"models": []}
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "red").save(buffer, "PNG")
    data = buffer.getvalue()
    key, digest = uuid4().hex, hashlib.sha256(data).hexdigest()
    for name, arguments in (
        (
            "inputs.upload.begin",
            dict(upload_id=key, mime_type="image/png", size_bytes=len(data), sha256=digest),
        ),
        (
            "inputs.upload.write",
            dict(
                upload_id=key,
                offset=0,
                data_base64=base64.b64encode(data).decode(),
                chunk_sha256=digest,
            ),
        ),
        ("inputs.upload.finish", dict(upload_id=key)),
    ):
        result = await client.call_tool(name, arguments)
        assert not result.is_error
    assert result.structured_content["source_kind"] == "upload"
    assert result.structured_content["sha256"] == digest
    assert not (await client.call_tool("inputs.delete", {"input_id": key})).is_error


async def smoke():
    executable = Path(sys.executable).parent / (
        "flamoris-generation-mcp.exe" if sys.platform == "win32" else "flamoris-generation-mcp"
    )
    assert executable.is_file(), "Installed console entrypoint is missing"
    # All connections in this smoke are loopback, independent of CI proxy settings.
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
    with TemporaryDirectory() as directory:
        provider_root = Path(directory) / "provider-inputs"
        provider_root.mkdir()
        copies = ComfyUIInputs(provider_root, max_files=1, max_bytes=128)
        name = copies.stage("e" * 32, b"bounded copy fixture", "image/png")
        copies.bind("e" * 32, "smoke-execution")
        copies.observe("smoke-execution", "unknown")
        assert (provider_root / name).exists()
        ComfyUIInputs(provider_root, max_files=1, max_bytes=128).observe(
            "smoke-execution", "completed"
        )
        assert not (provider_root / name).exists()
        env = {
            **os.environ,
            "FLAMORIS_MODEL_ROOT": str(Path(directory) / "models"),
            "FLAMORIS_MCP_TRANSPORT": "stdio",
        }
        async with Client(
            StdioServerParameters(
                command=str(executable),
                cwd=directory,
                env=env,
            ),
            read_timeout_seconds=10,
        ) as client:
            await check_tools(client)

        with socket.socket() as selection:
            selection.bind(("127.0.0.1", 0))
            port = selection.getsockname()[1]
        url = f"http://127.0.0.1:{port}/smoke/mcp"
        with TemporaryFile() as logs:
            process = subprocess.Popen(
                [
                    str(executable),
                    "--transport",
                    "streamable-http",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--mcp-path",
                    "/smoke/mcp",
                ],
                cwd=directory,
                env=env,
                stdout=logs,
                stderr=logs,
            )
            try:
                async with asyncio.timeout(15), httpx.AsyncClient(trust_env=False) as http:
                    while True:
                        assert process.poll() is None, "HTTP entrypoint exited before startup"
                        try:
                            await http.get(url)
                            break
                        except httpx.ConnectError:
                            await asyncio.sleep(0.05)
                async with Client(url, read_timeout_seconds=10) as client:
                    await check_tools(client)
            except BaseException:
                logs.seek(0)
                print(logs.read().decode(errors="replace"), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    print("Installed wheel: stdio + Streamable HTTP tools PASS")


if __name__ == "__main__":
    asyncio.run(smoke())

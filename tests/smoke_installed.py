"""Run with an installed wheel's Python, outside the source directory; no provider needed."""

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

import httpx
from mcp import Client
from mcp.client.stdio import StdioServerParameters


async def check_tools(client):
    tools = await client.list_tools()
    assert len(tools.tools) == 15
    assert {"capabilities.list", "jobs.submit", "assets.get", "assets.delete"} <= {
        tool.name for tool in tools.tools
    }
    result = await client.call_tool("models.list")
    assert not result.is_error and result.structured_content == {"models": []}


async def smoke():
    executable = Path(sys.executable).parent / (
        "flamoris-generation-mcp.exe" if sys.platform == "win32" else "flamoris-generation-mcp"
    )
    assert executable.is_file(), "Installed console entrypoint is missing"
    # All connections in this smoke are loopback, independent of CI proxy settings.
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "*"
    with TemporaryDirectory() as directory:
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

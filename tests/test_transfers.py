import asyncio
import base64
import hashlib
import os

import httpx
import pytest
from mcp import Client

from flamoris_generation_mcp.jobs import JobStore
from flamoris_generation_mcp.server import create_server
from flamoris_generation_mcp.transfers import CHUNK_BYTES, AssetTransfers


async def completed(stores, fake):
    workflows, jobs, _ = stores
    recipe = workflows.build(
        "text-to-image", dict(checkpoint="base.safetensors", positive_prompt="x")
    )
    job_id = (await jobs.submit(recipe["workflow_id"]))["job_id"]
    fake.finish()
    await jobs.list_assets(job_id)
    return jobs, f"{job_id}:000"


async def test_chunks_retry_restart_and_delete(stores, fake):
    jobs, asset = await completed(stores, fake)
    transfers = AssetTransfers(jobs)
    prepared = await transfers.prepare(asset)
    assert prepared["sha256"] == hashlib.sha256(b"image fixture").hexdigest()
    assert fake.download_count == 1
    first = await transfers.read(asset, prepared["sha256"], 0, 5)
    assert base64.b64decode(first["data_base64"]) == b"image"
    assert first == await transfers.read(asset, prepared["sha256"], 0, 5)
    restarted = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    other = AssetTransfers(restarted)
    assert (await other.prepare(asset))["sha256"] == prepared["sha256"]
    last = await other.read(asset, prepared["sha256"], 5)
    assert last["eof"] and base64.b64decode(last["data_base64"]) == b" fixture"
    assert fake.download_count == 1
    await restarted.delete_asset(asset)
    with pytest.raises(ValueError):
        await other.read(asset, prepared["sha256"], 0)


async def test_stream_bounds_failure_cancel_and_disk_budget(stores, fake):
    jobs, asset = await completed(stores, fake)
    provider = jobs.providers.get("comfyui")
    transfers = AssetTransfers(jobs, max_bytes=5)
    with pytest.raises(ValueError, match="limit"):
        await transfers.prepare(asset)
    directory = jobs.output_dir / asset[:32]
    assert not list(directory.glob(".transfer-*")) and not (directory / "000.png").exists()
    transfers.max_bytes = 1024
    transfers.disk_bytes = 1
    with pytest.raises(ValueError, match="budget"):
        await transfers.prepare(asset)
    transfers.disk_bytes = 8 * 1024**3
    entered = asyncio.Event()
    closed = []

    async def interrupted(*_):
        try:
            yield b"abc"
            entered.set()
            await asyncio.Event().wait()
        finally:
            closed.append(True)

    provider.stream_output = interrupted
    task = asyncio.create_task(transfers.prepare(asset))
    await entered.wait()
    with pytest.raises(ValueError, match="busy"):
        await transfers.prepare(asset)
    deletion = asyncio.create_task(jobs.delete_asset(asset))
    await asyncio.sleep(0)
    assert not deletion.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed and not list(directory.glob(".transfer-*"))
    assert (await deletion)["deleted"]


async def test_timeout_and_oversize_chunk_clean_partial(stores, fake):
    jobs, asset = await completed(stores, fake)
    provider = jobs.providers.get("comfyui")
    transfer = AssetTransfers(jobs)

    async def huge(*_):
        yield b"x" * (CHUNK_BYTES + 1)

    provider.stream_output = huge
    with pytest.raises(ValueError, match="chunk"):
        await transfer.prepare(asset)

    async def hangs(*_):
        await asyncio.Event().wait()
        yield b"x"

    provider.stream_output = hangs
    transfer.io_timeout = 0.01
    with pytest.raises(TimeoutError):
        await transfer.prepare(asset)
    assert not list((jobs.output_dir / asset[:32]).glob(".transfer-*"))


async def test_tampering_symlink_hardlink_and_invalid_requests(stores, fake, tmp_path):
    jobs, asset = await completed(stores, fake)
    transfer = AssetTransfers(jobs)
    prepared = await transfer.prepare(asset)
    for offset, length in [(-1, 1), (0, 0), (0, CHUNK_BYTES + 1), (True, 1), (100, 1)]:
        with pytest.raises(ValueError):
            await transfer.read(asset, prepared["sha256"], offset, length)
    with pytest.raises(ValueError):
        await transfer.prepare("../escape:000")
    path = jobs.output_dir / asset[:32] / "000.png"
    path.write_bytes(b"new content")
    with pytest.raises(ValueError, match="identity"):
        await transfer.read(asset, prepared["sha256"], 0)
    os.link(path, tmp_path / "hardlink")
    with pytest.raises(ValueError, match="private regular"):
        await transfer.prepare(asset)
    path.unlink()
    path.symlink_to(tmp_path / "hardlink")
    with pytest.raises((ValueError, OSError)):
        await transfer.prepare(asset)


async def test_stream_memory_and_large_asset(stores, fake):
    # Real >64 MiB asset, generated in bounded chunks; never allocate it in memory.
    jobs, asset = await completed(stores, fake)
    provider = jobs.providers.get("comfyui")

    async def stream(*_):
        for _ in range(260):
            yield b"x" * CHUNK_BYTES

    provider.stream_output = stream
    transfer = AssetTransfers(jobs)
    prepared = await transfer.prepare(asset)
    assert prepared["size_bytes"] == 260 * CHUNK_BYTES
    with pytest.raises(ValueError, match="retrieval limit"):
        await jobs.get_asset(asset)
    chunk = await transfer.read(asset, prepared["sha256"], 259 * CHUNK_BYTES)
    assert chunk["eof"] and len(base64.b64decode(chunk["data_base64"])) == CHUNK_BYTES


async def test_restart_unmaterialized_and_crash_cleanup(stores, fake):
    jobs, asset = await completed(stores, fake)
    other = AssetTransfers(
        JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    )
    with pytest.raises(ValueError, match="mapping lost"):
        await other.prepare(asset)
    leftover = jobs.output_dir / asset[:32] / (".transfer-" + "a" * 32)
    leftover.write_bytes(b"interrupted")
    await AssetTransfers(jobs).prepare(asset)
    assert not leftover.exists()


async def test_mcp_transfer_protocol(settings, fake):
    async with Client(create_server(settings, transport=httpx.MockTransport(fake.handle))) as c:
        w = (
            await c.call_tool(
                "workflows.build",
                dict(
                    template="text-to-image",
                    parameters={"checkpoint": "base.safetensors", "positive_prompt": "x"},
                ),
            )
        ).structured_content
        j = (
            await c.call_tool("jobs.submit", dict(workflow_id=w["workflow_id"]))
        ).structured_content
        fake.finish()
        await c.call_tool("assets.list", dict(job_id=j["job_id"]))
        asset = j["job_id"] + ":000"
        p = (await c.call_tool("assets.prepare", dict(asset_id=asset))).structured_content
        result = await c.call_tool(
            "assets.read", dict(asset_id=asset, sha256=p["sha256"], offset=0)
        )
        assert not result.is_error and result.structured_content["eof"]
        assert (
            await c.call_tool("assets.read", dict(asset_id=asset, sha256="bad", offset=0))
        ).is_error

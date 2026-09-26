import asyncio
import hashlib
import io
import struct
import wave
import zlib

import httpx
import pytest
from flamoris_generation_mcp.jobs import JobStore
from flamoris_generation_mcp.server import create_server
from mcp import Client

from flamoris_generation_mcp.inputs import ManagedInputs
from flamoris_generation_mcp.transfers import CHUNK_BYTES, AssetTransfers


def png():
    def chunk(kind, data):
        return (
            struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\0\xff\0\0"))
        + chunk(b"IEND", b"")
    )


def wav():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        out.writeframes(b"\0\0" * 16)
    return stream.getvalue()


async def setup_input(stores, fake, data=None):
    workflows, jobs, _ = stores
    workflow = workflows.build(
        "text-to-image", dict(checkpoint="base.safetensors", positive_prompt="x")
    )
    key = (await jobs.submit(workflow["workflow_id"]))["job_id"]
    fake.finish()
    await jobs.list_assets(key)

    async def stream(*_):
        yield data if data is not None else png()

    jobs.providers.get("comfyui").stream_output = stream
    managed = ManagedInputs(AssetTransfers(jobs), jobs.output_dir / "managed-inputs")
    return managed, key + ":000"


async def test_snapshot_restart_source_delete_and_input_delete(stores, fake):
    managed, asset = await setup_input(stores, fake)
    result = await managed.create(asset)
    assert result["source_asset_id"] == asset
    assert result["sha256"] == hashlib.sha256(png()).hexdigest()
    assert "identity" not in result and "path" not in result
    await managed.transfers.jobs.delete_asset(asset)
    assert managed.get(result["input_id"]) == result
    jobs = managed.transfers.jobs
    restarted = ManagedInputs(
        AssetTransfers(
            JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
        ),
        managed.root,
    )
    assert restarted.get(result["input_id"]) == result
    assert restarted.delete(result["input_id"])["deleted"]
    assert restarted.delete(result["input_id"])["deleted"]
    with pytest.raises(ValueError):
        restarted.get(result["input_id"])


async def test_small_audio_snapshot(stores, fake):
    managed, asset = await setup_input(stores, fake)
    jobs = managed.transfers.jobs
    job = jobs._jobs[asset[:32]]
    from flamoris_generation_mcp.providers import JobSnapshot, ProviderOutput

    job.snapshot = JobSnapshot(
        status="completed", outputs=(ProviderOutput("000", "sample.wav", "audio", "audio/wav"),)
    )

    async def stream(*_):
        yield wav()

    jobs.providers.get("comfyui").stream_output = stream
    result = await managed.create(asset)
    assert result["mime_type"] == "audio/wav" and result["size_bytes"] == len(wav())


async def test_adapter_lease_bounded_read_provenance_and_delete(stores, fake):
    managed, asset = await setup_input(stores, fake)
    snapshot = await managed.create(asset)
    key = snapshot["input_id"]
    jobs = managed.transfers.jobs
    # Model a provider adapter invoked under the real shared reservation.
    job_id = "a" * 32
    with pytest.raises(ValueError, match="reservation"):
        async with managed.stage(job_id, {"source": key}, {"source": {"image/png"}}):
            pass
    jobs._active_job_id = job_id
    async with managed.stage(job_id, {"source": key}, {"source": {"image/png"}}) as inputs:
        reader = inputs["source"]
        assert reader.metadata["source_asset_id"] == asset
        chunks = [c async for c in reader.chunks()]
        assert b"".join(chunks) == png() and all(len(c) <= CHUNK_BYTES for c in chunks)
        with pytest.raises(ValueError, match="in use"):
            managed.delete(key)
        with pytest.raises(ValueError, match="busy"):
            async with managed.stage(job_id, {"source": key}, {"source": {"image/png"}}):
                pass
    with pytest.raises(ValueError, match="closed"):
        await anext(reader.chunks())
    assert managed.delete(key)["deleted"]


async def test_invalid_media_size_count_and_disk(stores, fake):
    managed, asset = await setup_input(stores, fake, b"not an image")
    with pytest.raises(ValueError, match="signature"):
        await managed.create(asset)
    assert list(managed.root.iterdir()) == []
    managed.max_bytes = 2
    with pytest.raises(ValueError, match="limit"):
        await managed.create(asset)
    managed.disk_bytes = 1
    with pytest.raises(ValueError, match="budget"):
        await managed.create(asset)


async def test_expiry_prune_and_unpublished_recovery(stores, fake, monkeypatch):
    managed, asset = await setup_input(stores, fake)
    managed.max_inputs = 1
    record = await managed.create(asset)
    with pytest.raises(ValueError, match="count limit"):
        await managed.create(asset)
    monkeypatch.setattr(
        "flamoris_generation_mcp.inputs.time.time", lambda: record["expires_at"] + 1
    )
    with pytest.raises(ValueError, match="expired"):
        managed.get(record["input_id"])
    next_record = await managed.create(asset)
    assert not (managed.root / record["input_id"]).exists()
    managed.delete(next_record["input_id"])
    aborted = managed.root / ("f" * 32)
    aborted.mkdir()
    (aborted / "content").write_bytes(b"crashed copy")
    await managed.create(asset)
    assert not aborted.exists()


async def test_cancel_copy_and_concurrent_create_cleanup(stores, fake):
    managed, asset = await setup_input(stores, fake)
    entered = asyncio.Event()
    original = managed.transfers.read

    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        return await original(*args, **kwargs)

    managed.transfers.read = wait
    task = asyncio.create_task(managed.create(asset))
    await entered.wait()
    with pytest.raises(ValueError, match="busy"):
        await managed.create(asset)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert list(managed.root.iterdir()) == []


async def test_tampering_links_unknown_and_binding_rejection(stores, fake, tmp_path):
    managed, asset = await setup_input(stores, fake)
    record = await managed.create(asset)
    key = record["input_id"]
    jobs = managed.transfers.jobs
    job_id = "a" * 32
    jobs._active_job_id = job_id
    for refs, allowed in [
        ({"wrong": key}, {"source": {"image/png"}}),
        ({"source": "../bad"}, {"source": {"image/png"}}),
        ({"source": key}, {"source": {"audio/wav"}}),
        ({"source": "b" * 32}, {"source": {"image/png"}}),
    ]:
        with pytest.raises(ValueError):
            async with managed.stage(job_id, refs, allowed):
                pass
    path = managed.root / key / "content"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="identity"):
        managed.get(key)
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    path.symlink_to(outside)
    with pytest.raises((ValueError, OSError)):
        managed.get(key)
    with pytest.raises(ValueError):
        managed.delete(key)
    assert outside.read_bytes() == b"keep"


async def test_mcp_input_contract(settings, fake):
    def handle(request):
        if request.url.path == "/view":
            return httpx.Response(200, content=png())
        return fake.handle(request)

    async with Client(create_server(settings, transport=httpx.MockTransport(handle))) as c:
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
        result = await c.call_tool("inputs.create", dict(asset_id=j["job_id"] + ":000"))
        assert not result.is_error
        record = result.structured_content
        assert (
            await c.call_tool("inputs.get", dict(input_id=record["input_id"]))
        ).structured_content == record
        assert (await c.call_tool("inputs.create", dict(asset_id="https://example.org/x"))).is_error
        assert not (await c.call_tool("inputs.delete", dict(input_id=record["input_id"]))).is_error


async def test_source_delete_during_copy_and_corrupt_chunk(stores, fake):
    managed, asset = await setup_input(stores, fake)
    original = managed.transfers.read

    async def corrupt(*args, **kwargs):
        result = await original(*args, **kwargs)
        return {**result, "chunk_sha256": "0" * 64}

    managed.transfers.read = corrupt
    with pytest.raises(ValueError, match="chunk"):
        await managed.create(asset)
    assert list(managed.root.iterdir()) == []

    async def deleted(*args, **kwargs):
        await managed.transfers.jobs.delete_asset(asset)
        return await original(*args, **kwargs)

    managed.transfers.read = deleted
    with pytest.raises(ValueError):
        await managed.create(asset)
    assert list(managed.root.iterdir()) == []


async def test_staging_failure_releases_lease_and_aggregate_limit(stores, fake, monkeypatch):
    managed, asset = await setup_input(stores, fake)
    record = await managed.create(asset)
    key = record["input_id"]
    job_id = "a" * 32
    managed.transfers.jobs._active_job_id = job_id
    refs = {"source": key}
    types = {"source": {"image/png"}}
    with pytest.raises(RuntimeError, match="adapter failed"):
        async with managed.stage(job_id, refs, types):
            raise RuntimeError("adapter failed")
    assert not managed._used and not managed._staging
    monkeypatch.setattr("flamoris_generation_mcp.inputs.MAX_JOB_BYTES", 1)
    with pytest.raises(ValueError, match="aggregate"):
        async with managed.stage(job_id, refs, types):
            pass
    assert not managed._used and not managed._staging
    assert managed.delete(key)["deleted"]


async def test_expiry_during_lease_does_not_remove_active_input(stores, fake, monkeypatch):
    managed, asset = await setup_input(stores, fake)
    record = await managed.create(asset)
    key = record["input_id"]
    job_id = "a" * 32
    managed.transfers.jobs._active_job_id = job_id
    async with managed.stage(job_id, {"source": key}, {"source": {"image/png"}}) as readers:
        monkeypatch.setattr(
            "flamoris_generation_mcp.inputs.time.time", lambda: record["expires_at"] + 1
        )
        managed._capacity()
        assert b"".join([chunk async for chunk in readers["source"].chunks()]) == png()
    with pytest.raises(ValueError, match="expired"):
        async with managed.stage(job_id, {"source": key}, {"source": {"image/png"}}):
            pass
    managed._capacity()
    assert not (managed.root / key).exists()


async def test_unsupported_video_rejected_before_provider_stream(stores, fake):
    managed, asset = await setup_input(stores, fake)
    from flamoris_generation_mcp.providers import JobSnapshot, ProviderOutput

    jobs = managed.transfers.jobs
    jobs._jobs[asset[:32]].snapshot = JobSnapshot(
        status="completed",
        outputs=(ProviderOutput("000", "clip.mp4", "video", "video/mp4"),),
    )
    calls = []

    async def stream(*_):
        calls.append(True)
        yield b"never downloaded"

    jobs.providers.get("comfyui").stream_output = stream
    with pytest.raises(ValueError, match="media type"):
        await managed.create(asset)
    assert not calls
    assert not (jobs.output_dir / asset[:32] / "000.mp4").exists()
    assert list(managed.root.iterdir()) == []


async def test_large_allowed_input_stops_at_64_mib_without_publication(stores, fake):
    managed, asset = await setup_input(stores, fake)
    jobs = managed.transfers.jobs
    calls = 0
    chunk = b"x" * CHUNK_BYTES

    async def stream(*_):
        nonlocal calls
        yield png()
        for _ in range(256):
            calls += 1
            yield chunk

    jobs.providers.get("comfyui").stream_output = stream
    with pytest.raises(ValueError, match="limit"):
        await managed.create(asset)
    assert calls == 256
    directory = jobs.output_dir / asset[:32]
    assert not (directory / "000.png").exists()
    assert not list(directory.glob(".transfer-*"))
    assert list(managed.root.iterdir()) == []

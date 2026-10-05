import asyncio
import base64
import hashlib
import io
import os
import threading
from uuid import uuid4

import httpx
import pytest
from flamoris_generation_controller.input_uploads import MAX_UPLOAD_BYTES, InputUploads
from flamoris_generation_controller.inputs import ManagedInputs
from flamoris_generation_controller.jobs import JobStore
from flamoris_generation_controller.transfers import CHUNK_BYTES, AssetTransfers
from mcp import Client
from PIL import Image

from flamoris_generation_mcp.server import create_server


def image(format="PNG", size=16):
    out = io.BytesIO()
    Image.frombytes("RGB", (size, size), os.urandom(size * size * 3)).save(out, format)
    return out.getvalue()


def oversized_image():
    out = io.BytesIO()
    Image.new("RGB", (4097, 1)).save(out, "PNG")
    return out.getvalue()


def animated_image():
    out = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(
        out, "PNG", save_all=True, append_images=[Image.new("RGB", (2, 2), "blue")]
    )
    return out.getvalue()


@pytest.fixture
def uploads(stores, tmp_path):
    return InputUploads(ManagedInputs(AssetTransfers(stores[1]), tmp_path / "inputs"))


async def begin(uploads, data, mime="image/png", key=None):
    key = key or uuid4().hex
    await uploads.begin(key, mime, len(data), hashlib.sha256(data).hexdigest())
    return key


async def write(uploads, key, data, offset=0):
    return await uploads.write(
        key, offset, base64.b64encode(data).decode(), hashlib.sha256(data).hexdigest()
    )


@pytest.mark.parametrize(
    "format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")]
)
async def test_publish_restart_lease_and_uploaded_job_journal(uploads, format, mime):
    data = image(format)
    key = await begin(uploads, data, mime)
    with pytest.raises(ValueError):
        uploads.managed.get(key)
    await write(uploads, key, data)
    result = await uploads.finish(key)
    assert result["source_kind"] == "upload" and result["source_asset_id"] is None
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    assert "identity" not in result and "path" not in result
    assert await uploads.finish(key) == result
    assert (await uploads.begin(key, mime, len(data), result["sha256"]))["offset"] == len(data)
    managed = ManagedInputs(uploads.managed.transfers, uploads.managed.root)
    assert managed.get(key) == result
    jobs = managed.transfers.jobs
    workflow = jobs.workflows.build(
        "text-to-image", dict(checkpoint="base.safetensors", positive_prompt="x")
    )
    job_id = (await jobs.submit(workflow["workflow_id"]))["job_id"]
    async with managed.stage(job_id, {"source": key}, {"source": {mime}}) as readers:
        assert b"".join([part async for part in readers["source"].chunks()]) == data
        with pytest.raises(ValueError, match="in use"):
            managed.delete(key)
        jobs._jobs[job_id].managed_inputs = JobStore._managed_input_metadata(
            {"source": readers["source"].metadata}
        )
    jobs._persist_active(jobs._jobs[job_id])
    restarted = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    assert restarted._active_job_id == job_id
    assert restarted._jobs[job_id].managed_inputs["source"]["source_kind"] == "upload"
    assert managed.delete(key)["deleted"]


async def test_chunk_replay_restart_conflict_and_order(uploads):
    data = image(size=512)
    assert len(data) > CHUNK_BYTES
    key = await begin(uploads, data)
    part = data[:CHUNK_BYTES]
    first = await write(uploads, key, part)
    restarted = InputUploads(ManagedInputs(uploads.managed.transfers, uploads.managed.root))
    assert await write(restarted, key, part) == first
    assert (
        await restarted.begin(key, "image/png", len(data), hashlib.sha256(data).hexdigest())
    ) == first
    with pytest.raises(ValueError, match="conflict"):
        await write(restarted, key, b"changed")
    with pytest.raises(ValueError, match="order"):
        await write(restarted, key, b"x", CHUNK_BYTES + 1)
    with pytest.raises(ValueError, match="incomplete"):
        await restarted.finish(key)
    for offset in range(CHUNK_BYTES, len(data), CHUNK_BYTES):
        await write(restarted, key, data[offset : offset + CHUNK_BYTES], offset)
    assert (await restarted.finish(key))["size_bytes"] == len(data)
    with pytest.raises(ValueError, match="published"):
        await write(restarted, key, part)


@pytest.mark.parametrize(
    "mime,size,digest",
    [
        ("image/svg+xml", 1, "a" * 64),
        ("audio/wav", 1, "a" * 64),
        ("image/png", 0, "a" * 64),
        ("image/png", MAX_UPLOAD_BYTES + 1, "a" * 64),
        ("image/png", True, "a" * 64),
        ("image/png", 1, "wrong"),
    ],
)
async def test_invalid_reservation_has_no_storage(uploads, mime, size, digest):
    with pytest.raises(ValueError):
        await uploads.begin(uuid4().hex, mime, size, digest)
    assert not uploads.managed.root.exists()


async def test_quota_reserves_declared_bytes_and_expired_partial_is_pruned(uploads, monkeypatch):
    managed = uploads.managed
    managed.disk_bytes = MAX_UPLOAD_BYTES + 2 * 8192
    key = uuid4().hex
    await uploads.begin(key, "image/png", MAX_UPLOAD_BYTES, "a" * 64)
    restarted = InputUploads(ManagedInputs(managed.transfers, managed.root))
    restarted.managed.disk_bytes = managed.disk_bytes
    with pytest.raises(ValueError, match="budget"):
        await begin(restarted, b"x")
    original = __import__("time").time()
    monkeypatch.setattr(
        "flamoris_generation_controller.input_uploads.time.time", lambda: original + 601
    )
    with pytest.raises(ValueError, match="expired"):
        await uploads.finish(key)
    await begin(restarted, b"x")
    assert not (managed.root / key).exists()


@pytest.mark.parametrize(
    "data,mime",
    [
        (b"not an image", "image/png"),
        (image(), "image/jpeg"),
        (image()[:-12], "image/png"),
        (oversized_image(), "image/png"),
        (animated_image(), "image/png"),
    ],
)
async def test_invalid_decodes_never_publish(uploads, data, mime):
    # Large encoded bodies also fail the 8 MiB declaration before writing.
    if len(data) > MAX_UPLOAD_BYTES:
        with pytest.raises(ValueError):
            await begin(uploads, data, mime)
        return
    key = await begin(uploads, data, mime)
    for offset in range(0, len(data), CHUNK_BYTES):
        await write(uploads, key, data[offset : offset + CHUNK_BYTES], offset)
    with pytest.raises(ValueError):
        await uploads.finish(key)
    with pytest.raises(ValueError):
        uploads.managed.get(key)
    assert uploads.managed.delete(key)["deleted"]


async def test_tampering_digest_size_and_symlink_fail_closed(uploads, tmp_path):
    data = image()
    key = await begin(uploads, data)
    with pytest.raises(ValueError, match="integrity"):
        await uploads.write(key, 0, base64.b64encode(data).decode(), "a" * 64)
    with pytest.raises(ValueError, match="size"):
        await write(uploads, key, data + b"x")
    await write(uploads, key, data)
    path = uploads.managed.root / key / "content"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="changed"):
        await uploads.finish(key)
    target = tmp_path / "private"
    target.write_bytes(data)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        await uploads.finish(key)
    assert target.read_bytes() == data


async def test_final_digest_and_reused_identity_never_overwrite(uploads):
    data = image()
    key = uuid4().hex
    await uploads.begin(key, "image/png", len(data), "a" * 64)
    await write(uploads, key, data)
    with pytest.raises(ValueError, match="conflict"):
        await uploads.begin(key, "image/png", len(data), hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match="integrity"):
        await uploads.finish(key)
    assert (uploads.managed.root / key / "content").read_bytes() == data


async def test_cancellation_waits_for_decoder_and_protects_delete(uploads, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def decode(*_):
        started.set()
        assert release.wait(5)

    monkeypatch.setattr("flamoris_generation_controller.input_uploads.decode_image", decode)
    key = await begin(uploads, b"x")
    await write(uploads, key, b"x")
    task = asyncio.create_task(uploads.finish(key))
    await asyncio.to_thread(started.wait, 2)
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(ValueError, match="busy"):
            await begin(uploads, b"x")
        with pytest.raises(ValueError, match="in use"):
            uploads.managed.delete(key)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not uploads.managed._creating.locked() and not uploads.managed._used
    with pytest.raises(ValueError):
        uploads.managed.get(key)


async def test_typed_mcp_upload_without_provider_and_private_chunk_errors(settings, fake):
    async with Client(
        create_server(settings, transport=httpx.MockTransport(fake.handle))
    ) as client:
        data, key = image(), uuid4().hex
        args = dict(
            upload_id=key,
            mime_type="image/png",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        assert (
            await client.call_tool("inputs.upload.begin", {**args, "size_bytes": True})
        ).is_error
        assert not (await client.call_tool("inputs.upload.begin", args)).is_error
        sentinel = "PRIVATEUPLOAD" + "a" * 349528
        result = await client.call_tool(
            "inputs.upload.write",
            dict(upload_id=key, offset=0, data_base64=sentinel, chunk_sha256="a" * 64),
        )
        assert result.is_error and "PRIVATEUPLOAD" not in str(result.content)
        assert not (
            await client.call_tool(
                "inputs.upload.write",
                dict(
                    upload_id=key,
                    offset=0,
                    data_base64=base64.b64encode(data).decode(),
                    chunk_sha256=args["sha256"],
                ),
            )
        ).is_error
        assert (
            await client.call_tool("inputs.upload.finish", {"upload_id": key})
        ).structured_content["source_kind"] == "upload"
        assert fake.calls == []

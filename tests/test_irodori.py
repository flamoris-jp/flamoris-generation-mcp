import asyncio
import hashlib
import io
import os
import struct
import sys
import wave

import pytest
from flamoris_generation_controller.providers import irodori
from flamoris_generation_controller.providers.base import (
    GenerationRequest,
    ProviderError,
    SubmissionRejected,
)
from flamoris_generation_controller.speech import SpeechRecipe

STUB = """import argparse, os, signal, sys, time, wave
p = argparse.ArgumentParser()
p.add_argument('--text')
p.add_argument('--output-wav')
p.add_argument('--caption', default='')
a, rest = p.parse_known_args()
assert os.environ['HF_HUB_OFFLINE'] == '1'
assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
assert 'FLAMORIS_PROVENANCE_SECRET' not in os.environ and 'HF_TOKEN' not in os.environ
assert '--no-ref' in rest and '--num-candidates' in rest
if a.text == 'fail': sys.exit(1)
if a.text == '--seed': assert a.caption == '--model-device=cpu'
if a.text == 'sleep': time.sleep(60)
if a.text == 'ignore-term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print('ready', flush=True)
    time.sleep(60)
if a.text == 'spam':
    sys.stdout.write('secret-provider-path ' * 10000)
    sys.stdout.flush()
if a.text == 'invalid':
    open(a.output_wav, 'wb').write(b'not wav')
else:
    with wave.open(a.output_wav, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000)
        w.writeframes(b'\\0' * 4800)
"""


@pytest.fixture
async def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("FLAMORIS_PROVENANCE_SECRET", "never-pass-server-secret")
    monkeypatch.setenv("HF_TOKEN", "never-pass-hub-token")
    source = tmp_path / "source"
    source.mkdir()
    script = source / "infer.py"
    script.write_text(STUB)
    data = script.read_bytes()
    monkeypatch.setattr(
        irodori,
        "SOURCE_BLOBS",
        {"infer.py": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()},
    )
    model = tmp_path / "model"
    model.mkdir()
    (model / "tokenizer").mkdir()
    (model / "tokenizer" / "tokenizer_config.json").write_text("{}")
    (model / "model.safetensors").write_bytes(b"fake checkpoint; never loaded")
    (model / "weights.pth").write_bytes(b"fake codec; never loaded")
    config = irodori.IrodoriConfig(
        python=os.path.abspath(sys.executable),
        source_root=source,
        checkpoint=model / "model.safetensors",
        codec=model / "weights.pth",
        source_revision=irodori.SOURCE_REVISION,
        model_revision="test-fixture",
        model_device="cpu",
        codec_device="cpu",
        timeout_seconds=3,
    )
    result = irodori.IrodoriProvider(config, tmp_path / "stage")
    try:
        yield result
    finally:
        await result.close()


def request(text="こんにちは"):
    return GenerationRequest("speech.generate", "a" * 32, SpeechRecipe(parameters={"text": text}))


async def completed(provider, execution_id):
    async with asyncio.timeout(6):
        while True:
            snapshot = await provider.inspect(execution_id)
            if snapshot.status in {"completed", "failed", "cancelled", "unknown"}:
                return snapshot
            await asyncio.sleep(0.01)


async def test_real_cpu_fixture_and_immutable_wav(provider):
    assert (await provider.health()).available
    assert (await provider.health()).details["runtime_verified"] is False
    accepted = await provider.submit(request(), "b" * 32)
    snapshot = await completed(provider, accepted.execution_id)
    assert snapshot.status == "completed"
    assert snapshot.outputs[0].role.role == "audio"
    data = await provider.materialize(accepted.execution_id, "audio")
    assert irodori.validate_wav(data)["sample_rate"] == 24000
    path = provider.stage_root / accepted.execution_id / "speech.wav"
    path.write_bytes(data[:-1])
    with pytest.raises(ProviderError, match="unavailable"):
        await provider.materialize(accepted.execution_id, "audio")
    await provider.close()


@pytest.mark.parametrize(
    "text,code",
    [
        ("fail", "speech_provider_failed"),
        ("invalid", "speech_output_invalid"),
        ("spam", "log_limit"),
    ],
)
async def test_output_failure_and_bounded_logs(provider, text, code):
    accepted = await provider.submit(request(text), "b" * 32)
    snapshot = await completed(provider, accepted.execution_id)
    assert snapshot.status == "failed"
    assert snapshot.error["code"] == code
    assert "secret-provider-path" not in str(snapshot.as_dict())
    await provider.close()


async def test_owned_process_group_cancel(provider):
    accepted = await provider.submit(request("sleep"), "b" * 32)
    snapshot = await provider.cancel(accepted.execution_id)
    assert snapshot.status == "cancelled"
    assert provider._runs[accepted.execution_id].process.returncode is not None
    assert provider._settled(provider._runs[accepted.execution_id])
    await provider.close()


async def test_term_resistant_owned_process_requires_kill(provider):
    accepted = await provider.submit(request("ignore-term"), "b" * 32)
    run = provider._runs[accepted.execution_id]
    async with asyncio.timeout(2):
        while run.log_bytes == 0:
            await asyncio.sleep(0.01)
    assert (await provider.cancel(accepted.execution_id)).status == "cancelled"
    assert run.process.returncode == -9
    assert provider._settled(run)


async def test_deadline_terminates_owned_process(provider):
    provider.config = provider.config.model_copy(update={"timeout_seconds": 0.1})
    accepted = await provider.submit(request("sleep"), "b" * 32)
    snapshot = await completed(provider, accepted.execution_id)
    assert snapshot.status == "failed"
    assert snapshot.error["code"] == "execution_timeout"
    assert provider._settled(provider._runs[accepted.execution_id])
    await provider.close()


async def test_restart_never_adopts_pid_or_existing_output(provider):
    accepted = await provider.submit(request(), "b" * 32)
    assert (await completed(provider, accepted.execution_id)).status == "completed"
    restarted = irodori.IrodoriProvider(provider.config, provider.stage_root)
    assert (await restarted.inspect(accepted.execution_id)).status == "unknown"
    assert (await restarted.cancel(accepted.execution_id)).status == "unknown"
    with pytest.raises(ProviderError):
        await restarted.materialize(accepted.execution_id, "audio")
    await provider.close()


async def test_hash_mismatch_unavailable_without_spawn(provider):
    (provider.config.source_root / "infer.py").write_text("wrong source")
    assert not (await provider.health()).available
    with pytest.raises(SubmissionRejected, match="unavailable"):
        await provider.submit(request(), "b" * 32)
    assert not provider._runs


async def test_explicit_resource_root_supports_cache_snapshot_links(provider):
    root = provider.config.checkpoint.parent
    blobs = root / "blobs"
    blobs.mkdir()
    resources = (
        (provider.config.checkpoint, "checkpoint"),
        (provider.config.codec, "codec"),
        (root / "tokenizer" / "tokenizer_config.json", "tokenizer-config"),
    )
    for resource, name in resources:
        target = blobs / name
        resource.rename(target)
        resource.symlink_to(os.path.relpath(target, resource.parent))
    assert not (await provider.health()).available
    provider.config = provider.config.model_copy(update={"resource_root": root})
    assert (await provider.health()).available
    # Keep the snapshot checkpoint path: upstream finds tokenizer beside it.
    argv = provider._argv(request().payload, provider.stage_root / "speech.wav")
    assert argv[argv.index("--checkpoint") + 1] == str(provider.config.checkpoint)
    accepted = await provider.submit(request(), "b" * 32)
    assert (await completed(provider, accepted.execution_id)).status == "completed"


@pytest.mark.parametrize("resource", ["checkpoint", "codec", "tokenizer", "tokenizer-data"])
async def test_configured_resource_links_cannot_escape_root(provider, tmp_path, resource):
    root = provider.config.checkpoint.parent
    provider.config = provider.config.model_copy(update={"resource_root": root})
    if resource.startswith("tokenizer"):
        name = "tokenizer_config.json" if resource == "tokenizer" else "tokenizer.json"
        path = root / "tokenizer" / name
    else:
        path = getattr(provider.config, resource)
    outside = tmp_path / "outside-resource"
    outside.write_bytes(b"outside")
    path.unlink(missing_ok=True)
    path.symlink_to(outside)
    assert not (await provider.health()).available
    with pytest.raises(SubmissionRejected, match="unavailable"):
        await provider.submit(request(), "b" * 32)
    assert not provider._runs


async def test_configured_resource_rejects_parent_directory_escape(provider, tmp_path):
    root = provider.config.checkpoint.parent
    provider.config = provider.config.model_copy(update={"resource_root": root})
    tokenizer = root / "tokenizer"
    (tokenizer / "tokenizer_config.json").unlink()
    tokenizer.rmdir()
    outside = tmp_path / "outside-tokenizer"
    outside.mkdir()
    (outside / "tokenizer_config.json").write_text("{}")
    tokenizer.symlink_to(outside, target_is_directory=True)
    assert not (await provider.health()).available


async def test_configured_resource_root_rejects_unrelated_regular_checkpoint(provider):
    provider.config = provider.config.model_copy(
        update={"resource_root": provider.config.source_root}
    )
    assert not (await provider.health()).available


async def test_unconfirmed_group_is_unknown(provider, monkeypatch):
    accepted = await provider.submit(request("sleep"), "b" * 32)
    monkeypatch.setattr(provider, "_wait_settled", lambda *_: asyncio.sleep(0, result=False))
    assert (await provider.cancel(accepted.execution_id)).status == "unknown"
    await asyncio.sleep(0.05)
    await provider.close()


async def test_lost_spawn_acknowledgement_keeps_unknown_and_cleans_owned_child(
    provider, monkeypatch
):
    original = asyncio.create_subprocess_exec
    entered = asyncio.Event()

    async def delayed(*args, **kwargs):
        entered.set()
        await asyncio.sleep(0.03)
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    task = asyncio.create_task(provider.submit(request("sleep"), "b" * 32))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    run = next(iter(provider._runs.values()))
    assert run.snapshot.status == "unknown"
    async with asyncio.timeout(3):
        while run.worker is None:
            await asyncio.sleep(0.01)
        await run.worker
    assert provider._settled(run)
    await provider.close()


async def test_spawn_acknowledgement_timeout_is_bounded_and_late_child_is_owned(
    provider, monkeypatch
):
    original = asyncio.create_subprocess_exec
    release = asyncio.Event()

    async def delayed(*args, **kwargs):
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
    monkeypatch.setattr(irodori, "SPAWN_WAIT_SECONDS", 0.01)
    with pytest.raises(irodori.SubmissionUnknown):
        async with asyncio.timeout(0.1):
            await provider.submit(request("sleep"), "b" * 32)
    run = next(iter(provider._runs.values()))
    assert run.snapshot.status == "unknown" and run.process is None
    release.set()
    async with asyncio.timeout(3):
        while run.worker is None:
            await asyncio.sleep(0.01)
        await run.worker
    assert provider._settled(run)


async def test_option_like_text_and_caption_are_preserved(provider):
    req = GenerationRequest(
        "speech.generate",
        "a" * 32,
        SpeechRecipe(parameters={"text": "--seed", "caption": "--model-device=cpu"}),
    )
    accepted = await provider.submit(req, "b" * 32)
    assert (await completed(provider, accepted.execution_id)).status == "completed"


async def test_cancel_during_final_drains_never_becomes_completed(provider, monkeypatch):
    original = provider._drain
    release, entered = asyncio.Event(), asyncio.Event()
    drained = 0

    async def delayed_drain(run, reader):
        nonlocal drained
        await original(run, reader)
        drained += 1
        if drained == 2:
            entered.set()
        await release.wait()

    monkeypatch.setattr(provider, "_drain", delayed_drain)
    accepted = await provider.submit(request(), "b" * 32)
    run = provider._runs[accepted.execution_id]
    await entered.wait()
    await run.process.wait()
    for _ in range(5):
        await asyncio.sleep(0)
    assert provider._settled(run)
    assert (await provider.cancel(accepted.execution_id)).status == "cancelled"
    release.set()
    await run.worker
    assert (await provider.inspect(accepted.execution_id)).status == "cancelled"
    with pytest.raises(ProviderError):
        await provider.materialize(accepted.execution_id, "audio")


def job_store(provider, tmp_path):
    from flamoris_generation_controller.capabilities import Capability, CapabilityRegistry
    from flamoris_generation_controller.jobs import JobStore
    from flamoris_generation_controller.models import ModelCatalog
    from flamoris_generation_controller.providers import ProviderRegistry
    from flamoris_generation_controller.workflows import WorkflowStore

    from flamoris_generation_mcp.config import Settings

    workflows = WorkflowStore(
        ModelCatalog(Settings(model_root=tmp_path)), tmp_path / "recipes", speech_enabled=True
    )
    capabilities = CapabilityRegistry(
        (
            Capability(
                "speech.generate", "irodori", "irodori-no-reference-v1", ("speech-no-reference",)
            ),
        )
    )
    return JobStore(workflows, ProviderRegistry((provider,)), capabilities, tmp_path / "outputs")


async def test_shared_jobstore_native_pipeline_and_restart(provider, tmp_path):
    from flamoris_generation_controller.jobs import JobStore

    jobs = job_store(provider, tmp_path)
    built = jobs.workflows.build("speech-no-reference", {"text": "こんにちは"})
    assert built["schema_version"] == 4 and built["prompt"] is None
    assert built["speech"]["source_revision"] == irodori.SOURCE_REVISION
    jobs.workflows.save(built["workflow_id"])
    jobs.workflows._recipes.clear()
    assert jobs.workflows.get(built["workflow_id"]).schema_version == 4
    assert any(item["id"] == "speech-no-reference" for item in jobs.workflows.list()["descriptors"])
    accepted = await jobs.submit(built["workflow_id"])
    async with asyncio.timeout(5):
        while (await jobs.status(accepted["job_id"]))["status"] not in {"completed", "failed"}:
            await asyncio.sleep(0.01)
    result = await jobs.result(accepted["job_id"])
    assert result["status"] == "completed"
    assert result["outputs"][0]["role"] == "audio"
    assert (await jobs.list_assets(accepted["job_id"]))["assets"][0]["mime_type"] == "audio/wav"
    assert jobs.activity()["active_job_id"] is None
    restarted = JobStore(jobs.workflows, jobs.providers, jobs.capabilities, jobs.output_dir)
    assert (await restarted.status(accepted["job_id"]))["status"] == "completed"
    await provider.close()


async def test_shared_reservation_recovery_unknown_and_exclusive(provider, tmp_path):
    from flamoris_generation_controller.jobs import GenerationBusyError, JobStore
    from flamoris_generation_controller.providers import ProviderRegistry

    jobs = job_store(provider, tmp_path)
    built = jobs.workflows.build("speech-no-reference", {"text": "sleep"})
    accepted = await jobs.submit(built["workflow_id"])
    fresh = irodori.IrodoriProvider(provider.config, provider.stage_root)
    recovered = JobStore(
        jobs.workflows, ProviderRegistry((fresh,)), jobs.capabilities, jobs.output_dir
    )
    assert (await recovered.status(accepted["job_id"]))["status"] == "unknown"
    with pytest.raises(GenerationBusyError):
        await recovered.submit(built["workflow_id"])
    assert (await recovered.cancel(accepted["job_id"]))["status"] == "unknown"
    assert recovered.activity()["active_job_id"] == accepted["job_id"]
    await jobs.cancel(accepted["job_id"])
    await provider.close()


async def test_definite_resources_failure_releases_shared_reservation(provider, tmp_path):
    jobs = job_store(provider, tmp_path)
    built = jobs.workflows.build("speech-no-reference", {"text": "hello"})
    (provider.config.source_root / "infer.py").write_text("wrong source")
    with pytest.raises(SubmissionRejected):
        await jobs.submit(built["workflow_id"])
    assert jobs.activity()["active_job_id"] is None


async def test_mcp_native_speech_public_flow(provider, settings, fake, tmp_path):
    import base64

    import httpx
    from mcp import Client

    from flamoris_generation_mcp.server import create_server

    config_path = tmp_path / "irodori.json"
    config_path.write_text(provider.config.model_dump_json())
    configured = settings.model_copy(update={"irodori_config": config_path})
    server = create_server(configured, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        health = (await client.call_tool("system.health")).structured_content
        native = next(item for item in health["providers"] if item["id"] == "irodori")
        assert health["healthy"] and native["available"] and native["runtime_verified"] is False
        capabilities = (await client.call_tool("capabilities.list")).structured_content
        assert any(
            item["id"] == "speech.generate" and item["available"]
            for item in capabilities["capabilities"]
        )
        listing = (await client.call_tool("workflows.list")).structured_content
        assert any(item["id"] == "speech-no-reference" for item in listing["descriptors"])
        built = (
            await client.call_tool(
                "workflows.build",
                {"template": "speech-no-reference", "parameters": {"text": "こんにちは😊"}},
            )
        ).structured_content
        assert built["schema_version"] == 4 and built["prompt"] is None
        accepted = (
            await client.call_tool("jobs.submit", {"workflow_id": built["workflow_id"]})
        ).structured_content
        job_id = accepted["job_id"]
        async with asyncio.timeout(5):
            while True:
                status = (
                    await client.call_tool("jobs.status", {"job_id": job_id})
                ).structured_content
                if status["status"] == "completed":
                    break
                assert status["status"] not in {"failed", "unknown"}
                await asyncio.sleep(0.01)
        assets = (await client.call_tool("assets.list", {"job_id": job_id})).structured_content
        assert assets["assets"][0]["role"] == "audio"
        assert assets["assets"][0]["mime_type"] == "audio/wav"
        prepared = (
            await client.call_tool("assets.prepare", {"asset_id": assets["assets"][0]["asset_id"]})
        ).structured_content
        assert prepared["size_bytes"] > 44
        chunk = (
            await client.call_tool(
                "assets.read",
                {"asset_id": prepared["asset_id"], "sha256": prepared["sha256"], "offset": 0},
            )
        ).structured_content
        assert chunk["eof"]
        data = base64.b64decode(chunk["data_base64"])
        assert irodori.validate_wav(data)["channels"] == 1


async def test_mcp_missing_optional_native_resources_keeps_hub_healthy(
    provider, settings, fake, tmp_path
):
    import httpx
    from mcp import Client

    from flamoris_generation_mcp.server import create_server

    config_path = tmp_path / "irodori.json"
    config_path.write_text(provider.config.model_dump_json())
    (provider.config.source_root / "infer.py").write_text("wrong source")
    server = create_server(
        settings.model_copy(update={"irodori_config": config_path}),
        transport=httpx.MockTransport(fake.handle),
    )
    async with Client(server) as client:
        health = (await client.call_tool("system.health")).structured_content
        assert health["healthy"]
        assert (
            next(item for item in health["providers"] if item["id"] == "irodori")["available"]
            is False
        )
        assert (
            next(item for item in health["providers"] if item["id"] == "comfyui")["available"]
            is True
        )


def test_wav_domain():
    output = io.BytesIO()
    with wave.open(output, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\0" * 100)
    data = output.getvalue()
    assert irodori.validate_wav(data)["frames"] == 50
    for invalid in (data[:-1], data + b"x", data[:4] + b"\0" * 4 + data[8:]):
        with pytest.raises(ValueError):
            irodori.validate_wav(invalid)
    float_fmt = struct.pack("<HHIIHH", 3, 1, 24000, 96000, 4, 32)
    body = (
        b"WAVEfmt "
        + struct.pack("<I", 16)
        + float_fmt
        + b"data"
        + struct.pack("<I", 4)
        + struct.pack("<f", float("nan"))
    )
    with pytest.raises(ValueError, match="Non-finite"):
        irodori.validate_wav(b"RIFF" + struct.pack("<I", len(body)) + body)

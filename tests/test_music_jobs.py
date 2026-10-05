"""Native Music uses the existing job, immutable input and asset authorities."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from mcp import Client

from flamoris_generation_mcp.capabilities import Capability, CapabilityRegistry
from flamoris_generation_mcp.config import Settings
from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.music import MusicRecipe
from flamoris_generation_mcp.provenance import ExternalProvenance
from flamoris_generation_mcp.providers import (
    JobSnapshot,
    OutputRole,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
    ProviderRegistry,
)
from flamoris_generation_mcp.providers.base import SubmissionRejected, SubmissionUnknown
from flamoris_generation_mcp.server import create_server
from flamoris_generation_mcp.transcription import TranscriptionRecipe
from flamoris_generation_mcp.transfers import AssetTransfers
from flamoris_generation_mcp.workflows import WorkflowStore


class NativeFixture:
    def __init__(self, provider_id):
        self.provider_id = provider_id
        self.requests = []
        self.state = JobSnapshot(status="running")
        self.cancel_state = JobSnapshot(status="cancel_requested")
        self.submit_error = None
        self.managed_inputs = {}
        self.reads = []
        self.entered = None
        self.release = None

    async def health(self):
        return ProviderHealth(True)

    async def submit(self, request, job_id):
        self.requests.append((request, job_id))
        if self.entered is not None:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.submit_error is not None:
            raise self.submit_error
        return ProviderJob("a" * 32, managed_inputs=self.managed_inputs)

    async def inspect(self, execution_id):
        return self.state

    async def cancel(self, execution_id):
        self.state = self.cancel_state
        return self.state

    async def materialize(self, execution_id, output_id):
        self.reads.append(output_id)
        return {
            "audio": b"small provider WAV fixture",
            "score": b"X:1\nK:C\nC D E F|\n",
            "metadata": b'{"format":"fixture"}',
            "midi": b"MThd fixture",
        }[output_id]

    async def stream_output(self, execution_id, output_id):
        content = await self.materialize(execution_id, output_id)
        yield content

    async def close(self):
        pass


def context(settings):
    workflows = WorkflowStore(
        ModelCatalog(settings),
        settings.workflow_dir,
        speech_enabled=True,
        music_enabled=True,
        transcription_enabled=True,
    )
    identities = (
        ("comfyui", "image.generate", "text-to-image"),
        ("irodori", "speech.generate", "speech-no-reference"),
        ("yue2", "music.generate", "music-generate"),
        ("sheetsage2", "music.transcribe", "music-transcribe"),
    )
    fixtures = {provider: NativeFixture(provider) for provider, _, _ in identities}
    providers = ProviderRegistry(tuple(fixtures.values()))
    capabilities = CapabilityRegistry(
        tuple(
            Capability(operation, provider, provider + "-fixture", (template,))
            for provider, operation, template in identities
        )
    )
    jobs = JobStore(workflows, providers, capabilities, settings.output_dir)
    values = (
        {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
        {"text": "hello"},
        {"style": "gentle piano"},
        {"audio": "f" * 32},
    )
    built = {
        provider: workflows.build(template, params)["workflow_id"]
        for (provider, _, template), params in zip(identities, values, strict=True)
    }
    return workflows, fixtures, providers, capabilities, jobs, built


@pytest.mark.parametrize(
    ("template", "parameters", "flag", "recipe_type", "schema", "route"),
    [
        (
            "music-generate",
            {"style": "piano"},
            "music_enabled",
            MusicRecipe,
            5,
            ("yue2", "music.generate"),
        ),
        (
            "music-transcribe",
            {"audio": "f" * 32},
            "transcription_enabled",
            TranscriptionRecipe,
            6,
            ("sheetsage2", "music.transcribe"),
        ),
    ],
)
def test_native_workflows_are_independent_opt_in_saved_recipes(
    settings, template, parameters, flag, recipe_type, schema, route
):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    with pytest.raises(ValueError, match="disabled"):
        store.build(template, parameters)
    assert template not in {item["id"] for item in store.list()["descriptors"]}
    setattr(store, flag, True)
    built = store.build(template, parameters)
    assert built["schema_version"] == schema and built["prompt"] is None
    recipe = store.get(built["workflow_id"])
    assert isinstance(recipe, recipe_type)
    assert store.routing(recipe) == route and store.capture(recipe) is None
    with pytest.raises(ValueError, match="no ComfyUI"):
        store.prompt(recipe)
    for preconditions in ({"require_ready": True}, {"definition_version": 1}):
        with pytest.raises(ValueError, match="attestation"):
            store.build(template, parameters, **preconditions)
    store.save(built["workflow_id"])
    store._recipes.clear()
    assert store.get(built["workflow_id"]) == recipe
    setattr(store, flag, False)
    with pytest.raises(ValueError, match="disabled"):
        store.get(built["workflow_id"])


def test_configuration_uses_explicit_independent_paths(monkeypatch):
    assert Settings().yue2_config is None and Settings().sheetsage2_config is None
    monkeypatch.setenv("FLAMORIS_YUE2_CONFIG", "/configured/yue2.json")
    monkeypatch.setenv("FLAMORIS_SHEETSAGE2_CONFIG", "/configured/sheetsage2.json")
    settings = Settings.from_env()
    assert settings.yue2_config == Path("/configured/yue2.json")
    assert settings.sheetsage2_config == Path("/configured/sheetsage2.json")
    assert settings.irodori_config is None


async def test_configured_unavailable_music_keeps_image_and_tool_schema(
    settings, fake, tmp_path, monkeypatch
):
    from flamoris_generation_mcp.music import YUE2_SOURCE_REVISION
    from flamoris_generation_mcp.transcription import SHEETSAGE2_ENTRYPOINT_SHA256

    yue = tmp_path / "yue2.json"
    yue.write_text(
        json.dumps(
            {
                "url": "http://127.0.0.1:1",
                "source_revision": YUE2_SOURCE_REVISION,
                "model_revision": "fixture",
                "timeout_seconds": 1,
            }
        )
    )
    # Health checks validate the resource manifest without loading a model.
    sheet = tmp_path / "sheetsage2.json"
    sheet.write_text(
        json.dumps(
            {
                "python": str(tmp_path / "missing-python"),
                "source_root": str(tmp_path / "missing-model"),
                "source_revision": "fixture",
                "source_hashes": {"infer.py": SHEETSAGE2_ENTRYPOINT_SHA256},
            }
        )
    )
    baseline = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(baseline) as client:
        schemas = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
    configured = settings.model_copy(update={"yue2_config": yue, "sheetsage2_config": sheet})
    from flamoris_generation_mcp.providers.yue2 import Yue2Provider

    def offline(request):
        raise httpx.ConnectError("fixture unavailable", request=request)

    monkeypatch.setattr(
        "flamoris_generation_mcp.server.Yue2Provider",
        lambda config, root: Yue2Provider(
            config, root, client=httpx.AsyncClient(transport=httpx.MockTransport(offline))
        ),
    )
    server = create_server(configured, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        assert {
            tool.name: tool.input_schema for tool in (await client.list_tools()).tools
        } == schemas
        health = (await client.call_tool("system.health")).structured_content
        assert health["healthy"] is True
        availability = {item["id"]: item["available"] for item in health["providers"]}
        assert availability == {"comfyui": True, "yue2": False, "sheetsage2": False}
        listing = (await client.call_tool("workflows.list")).structured_content
        native = {item["id"]: item for item in listing["descriptors"] if item["kind"] == "native"}
        assert set(native) == {"music-generate", "music-transcribe"}
        assert all(item["readiness"] == {"status": "not-attested"} for item in native.values())
        capabilities = (await client.call_tool("capabilities.list")).structured_content
        assert {item["id"]: item["available"] for item in capabilities["capabilities"]} == {
            "image.generate": True,
            "music.generate": False,
            "music.transcribe": False,
        }


@pytest.mark.parametrize("provider_id", ["yue2", "sheetsage2"])
async def test_music_shares_reservation_with_image_speech_and_transcription(settings, provider_id):
    _, fixtures, _, _, jobs, built = context(settings)
    submitted = await jobs.submit(built[provider_id])
    request = fixtures[provider_id].requests[0][0]
    assert request.definition is None
    for other in built.values():
        with pytest.raises(GenerationBusyError):
            await jobs.submit(other)
    fixtures[provider_id].state = JobSnapshot(status="cancelled")
    assert (await jobs.status(submitted["job_id"]))["status"] == "cancelled"
    assert (await jobs.submit(built["comfyui"]))["provider_id"] == "comfyui"


async def test_submission_race_uses_one_existing_authority(settings):
    _, fixtures, _, _, jobs, built = context(settings)
    fixtures["yue2"].entered, fixtures["yue2"].release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(jobs.submit(built["yue2"]))
    await fixtures["yue2"].entered.wait()
    with pytest.raises(GenerationBusyError, match="submitting"):
        await jobs.submit(built["sheetsage2"])
    fixtures["yue2"].release.set()
    assert (await task)["provider_id"] == "yue2"


@pytest.mark.parametrize("provider_id", ["yue2", "sheetsage2"])
async def test_music_rejection_releases_and_ambiguity_is_durable_without_retry(
    settings, provider_id
):
    workflows, fixtures, providers, capabilities, jobs, built = context(settings)
    provider = fixtures[provider_id]
    provider.submit_error = SubmissionRejected("local validation rejected")
    with pytest.raises(SubmissionRejected):
        await jobs.submit(built[provider_id])
    provider.submit_error = SubmissionUnknown("acceptance uncertain")
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(built[provider_id])
    assert len(provider.requests) == 2
    restarted = JobStore(workflows, providers, capabilities, settings.output_dir)
    recovered = next(iter(restarted._jobs.values()))
    assert recovered.provider_id == provider_id and recovered.snapshot.status == "unknown"
    assert recovered.provider_execution_id == ""
    with pytest.raises(GenerationBusyError):
        await restarted.submit(built["comfyui"])
    assert len(provider.requests) == 2


@pytest.mark.parametrize("provider_id", ["yue2", "sheetsage2"])
async def test_restart_does_not_clear_cancel_pending_or_accept_disabled_native(
    settings, provider_id
):
    workflows, _, providers, capabilities, jobs, built = context(settings)
    submitted = await jobs.submit(built[provider_id])
    assert (await jobs.cancel(submitted["job_id"]))["status"] == "cancel_requested"
    restarted = JobStore(workflows, providers, capabilities, settings.output_dir)
    with pytest.raises(GenerationBusyError):
        await restarted.submit(built["comfyui"])
    setattr(workflows, "music_enabled" if provider_id == "yue2" else "transcription_enabled", False)
    with pytest.raises(ValueError, match="Invalid execution reservation"):
        JobStore(workflows, providers, capabilities, settings.output_dir)


@pytest.mark.parametrize("name", ["audio", "reference-image"])
async def test_transcription_input_identity_survives_active_restart_and_strips_paths(
    settings, name
):
    workflows, fixtures, providers, capabilities, jobs, built = context(settings)
    metadata = {
        "input_id": "f" * 32,
        "source_asset_id": "d" * 32 + ":000",
        "sha256": "e" * 64,
        "mime_type": "audio/wav",
        "size_bytes": 44,
    }
    fixtures["sheetsage2"].managed_inputs = {name: {**metadata, "local_path": "/private/stage"}}
    submitted = await jobs.submit(built["sheetsage2"])
    assert submitted["managed_inputs"] == {name: metadata}
    restarted = JobStore(workflows, providers, capabilities, settings.output_dir)
    assert (await restarted.status(submitted["job_id"]))["managed_inputs"] == {name: metadata}


async def test_malformed_accepted_input_identity_keeps_capacity(settings):
    _, fixtures, _, _, jobs, built = context(settings)
    fixtures["sheetsage2"].managed_inputs = {"audio": {"input_id": "../../escape"}}
    with pytest.raises(SubmissionUnknown):
        await jobs.submit(built["sheetsage2"])
    with pytest.raises(GenerationBusyError):
        await jobs.submit(built["comfyui"])


async def test_completed_transcription_recovers_validated_input_receipt_without_execution(settings):
    workflows, fixtures, providers, capabilities, jobs, built = context(settings)
    metadata = {
        "input_id": "f" * 32,
        "source_asset_id": "d" * 32 + ":000",
        "sha256": "e" * 64,
        "mime_type": "audio/wav",
        "size_bytes": 44,
    }
    provider = fixtures["sheetsage2"]
    provider.managed_inputs = {"audio": metadata}
    submitted = await jobs.submit(built["sheetsage2"])
    provider.state = JobSnapshot(
        status="completed",
        outputs=(ProviderOutput("midi", "transcription.mid", "midi", "audio/midi"),),
    )
    assert (await jobs.result(submitted["job_id"]))["managed_inputs"] == {"audio": metadata}
    restarted = JobStore(workflows, providers, capabilities, settings.output_dir)
    restored = await restarted.status(submitted["job_id"])
    assert restored["archived"] is True and restored["managed_inputs"] == {"audio": metadata}
    assert restarted._active_job_id is None and len(provider.requests) == 1

    # An archive is untrusted metadata too: malformed provenance must not pass
    # merely because it no longer owns live execution.
    path = settings.output_dir / submitted["job_id"] / "metadata.json"
    record = json.loads(path.read_text())
    record["managed_inputs"] = {"audio": {**metadata, "sha256": "invalid"}}
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="Invalid or unavailable archived completed job"):
        await restarted.status(submitted["job_id"])


async def test_music_multiple_assets_have_stable_roles_and_provenance_after_restart(settings):
    workflows, fixtures, providers, capabilities, jobs, built = context(settings)
    provenance = ExternalProvenance(issuer="hub", subject="creative-client")
    submitted = await jobs.submit(built["yue2"], provenance=provenance)
    provider = fixtures["yue2"]
    provider.state = JobSnapshot(
        status="completed",
        metadata={"external_provenance": {"issuer": "evil", "subject": "forged"}},
        outputs=(
            ProviderOutput(
                "audio", "audio.wav", "audio", "audio/wav", OutputRole("audio", "audio")
            ),
            ProviderOutput(
                "score", "score.abc", "score", "text/vnd.abc", OutputRole("score", "score")
            ),
            ProviderOutput(
                "metadata",
                "replay.json",
                "metadata",
                "application/json",
                OutputRole("metadata", "metadata"),
            ),
        ),
    )
    listing = await jobs.list_assets(submitted["job_id"])
    assert provider.reads == []
    assert [asset["role"] for asset in listing["assets"]] == ["audio", "score", "metadata"]
    transfers = AssetTransfers(jobs)
    for asset in listing["assets"]:
        assert asset["external_provenance"] == provenance.model_dump()
        receipt = await transfers.prepare(asset["asset_id"])
        part = await transfers.read(asset["asset_id"], receipt["sha256"], 0)
        assert (
            hashlib.sha256(base64.b64decode(part["data_base64"])).hexdigest() == receipt["sha256"]
        )
    assert len(provider.reads) == 3
    restarted = JobStore(workflows, providers, capabilities, settings.output_dir)
    archived = await restarted.list_assets(submitted["job_id"])
    assert archived == listing | {
        "assets": [
            dict(
                asset,
                materialized=True,
                size_bytes=len(
                    {
                        "audio": b"small provider WAV fixture",
                        "score": b"X:1\nK:C\nC D E F|\n",
                        "metadata": b'{"format":"fixture"}',
                    }[asset["role"]]
                ),
            )
            for asset in listing["assets"]
        ]
    }
    assert len(provider.reads) == 3

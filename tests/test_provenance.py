import asyncio
import hashlib
import hmac
import json
import time
from uuid import uuid4

import httpx
import pytest
from flamoris_generation_controller.durable import CommitUnknown
from flamoris_generation_controller.jobs import JobStore
from flamoris_generation_controller.transfers import AssetTransfers
from mcp import Client
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError
from test_http import serve_http

from flamoris_generation_mcp.config import Settings
from flamoris_generation_mcp.provenance import (
    META_KEY,
    ExternalProvenance,
    ProvenanceIngress,
    current_provenance,
)
from flamoris_generation_mcp.server import create_server

SECRET = "test-internal-signing-key-0000000000000000"
ISSUER = "configured-hub"


@pytest.fixture(autouse=True)
def isolated_nonce_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def configured(settings=None):
    values = settings.model_dump() if settings else {}
    return Settings(**{**values, "provenance_secret": SECRET, "provenance_issuer": ISSUER})


def signed(tool, arguments, *, subject="client-a", issued_at=None, nonce=None, secret=SECRET):
    envelope = {
        "version": 1,
        "issuer": ISSUER,
        "subject": subject,
        "issued_at": int(time.time()) if issued_at is None else issued_at,
        "nonce": uuid4().hex if nonce is None else nonce,
    }
    data = json.dumps(
        {**envelope, "tool": tool, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    envelope["signature"] = hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
    return {META_KEY: envelope}


def test_strict_non_secret_provenance_and_configuration(monkeypatch):
    value = ExternalProvenance(issuer=ISSUER, subject="client-a")
    with pytest.raises(ValidationError):
        value.subject = "client-b"
    for raw in (
        {"issuer": ISSUER, "subject": "../../owner"},
        {"issuer": ISSUER, "subject": "a" * 129},
        {"issuer": ISSUER, "subject": 42},
        {"issuer": ISSUER, "subject": "valid", "studio_user_id": "owner"},
    ):
        with pytest.raises(ValidationError):
            ExternalProvenance.model_validate(raw)
    for values in (
        {"provenance_secret": SECRET},
        {"provenance_issuer": ISSUER},
        {"provenance_secret": "short", "provenance_issuer": ISSUER},
        {"provenance_secret": "x" * 32 + "\n", "provenance_issuer": ISSUER},
    ):
        with pytest.raises(ValidationError):
            Settings(**values)
    monkeypatch.setenv("FLAMORIS_PROVENANCE_SECRET", SECRET)
    monkeypatch.setenv("FLAMORIS_PROVENANCE_ISSUER", ISSUER)
    settings = Settings.from_env()
    assert SECRET not in repr(settings)
    assert "provenance_secret" not in settings.model_dump()


def test_secret_validation_errors_hide_input_material():
    secret = "sensitive-prefix-0000000000000000-sensitive-suffix"
    for config in (
        {"provenance_secret": secret},
        {"provenance_secret": secret + "\n", "provenance_issuer": ISSUER},
    ):
        with pytest.raises(ValidationError) as error:
            Settings(**config)
        assert secret not in str(error.value)
        assert "sensitive-prefix" not in str(error.value)
        assert "sensitive-suffix" not in str(error.value)


def test_raw_argument_binding_replay_and_unsigned_compatibility():
    ingress = ProvenanceIngress(configured())
    params = {"name": "jobs.submit", "arguments": {"workflow_id": "a" * 32}}
    meta = signed(params["name"], params["arguments"])
    assert ingress.authenticate(meta, params) == ExternalProvenance(
        issuer=ISSUER, subject="client-a"
    )
    with pytest.raises(ValueError):
        ingress.authenticate(meta, params)
    for changed in (
        {"name": "jobs.cancel", "arguments": params["arguments"]},
        {"name": params["name"], "arguments": {"workflow_id": "b" * 32}},
    ):
        with pytest.raises(ValueError):
            ingress.authenticate(signed(params["name"], params["arguments"]), changed)
    assert ingress.authenticate(None, params) is None
    assert ingress.authenticate({"unrelated": "client metadata"}, params) is None
    with pytest.raises(ValueError):
        ProvenanceIngress(Settings()).authenticate(
            signed(params["name"], params["arguments"]), params
        )


def test_cross_repository_canonical_signing_vector():
    meta = signed("jobs.submit", {"workflow_id": "a" * 32}, issued_at=2000000000, nonce="0" * 32)
    assert meta[META_KEY]["signature"] == (
        "470bfa0724cb50f32b226845940ce132711391ebab0fb5f7a29ec3a2d667b7e8"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"version": True},
        {"version": 2},
        {"issuer": "other-hub"},
        {"subject": "../owner"},
        {"issued_at": True},
        {"issued_at": "0"},
        {"nonce": "A" * 32},
        {"signature": "0" * 64},
        {"studio_user_id": "owner"},
    ],
)
def test_malformed_context_is_never_downgraded(changes):
    ingress = ProvenanceIngress(configured())
    params = {"name": "system.health", "arguments": {}}
    meta = signed(params["name"], {})
    meta[META_KEY].update(changes)
    with pytest.raises(ValueError):
        ingress.authenticate(meta, params)


def test_freshness_and_bounded_nonce_authority(monkeypatch):
    monkeypatch.setattr("flamoris_generation_mcp.provenance.time.time", lambda: 1000)
    monkeypatch.setattr("flamoris_generation_mcp.provenance.MAX_NONCES", 1)
    ingress = ProvenanceIngress(configured())
    params = {"name": "system.health", "arguments": {}}
    for timestamp in (969, 1031):
        with pytest.raises(ValueError):
            ingress.authenticate(signed("system.health", {}, issued_at=timestamp), params)
    accepted = signed("system.health", {}, issued_at=970)
    ingress.authenticate(accepted, params)
    with pytest.raises(ValueError):
        ingress.authenticate(signed("system.health", {}, issued_at=1000), params)
    nonces = ingress._journal.read("nonces.json")["nonces"]
    assert len(nonces) == 1
    assert accepted[META_KEY]["nonce"] in nonces
    monkeypatch.setattr("flamoris_generation_mcp.provenance.time.time", lambda: 1062)
    ingress.authenticate(signed("system.health", {}, issued_at=1062), params)
    assert len(ingress._journal.read("nonces.json")["nonces"]) == 1


def test_restart_replay_with_future_timestamp_and_clock_rollback(monkeypatch):
    monkeypatch.setattr("flamoris_generation_mcp.provenance.time.time", lambda: 1000)
    settings = configured()
    ingress = ProvenanceIngress(settings)
    params = {"name": "system.health", "arguments": {}}
    meta = signed("system.health", {}, issued_at=1020)
    ingress.authenticate(meta, params)
    restarted = ProvenanceIngress(settings)
    with pytest.raises(ValueError):
        restarted.authenticate(meta, params)
    monkeypatch.setattr("flamoris_generation_mcp.provenance.time.time", lambda: 999)
    with pytest.raises(ValueError):
        restarted.authenticate(signed("system.health", {}, issued_at=999), params)
    assert restarted.authenticate(None, params) is None
    monkeypatch.setattr("flamoris_generation_mcp.provenance.time.time", lambda: 1001)
    with pytest.raises(ValueError):
        restarted.authenticate(signed("system.health", {}, issued_at=1001), params)


@pytest.mark.parametrize("failure", [OSError("disk unavailable"), CommitUnknown("fsync unknown")])
async def test_journal_failure_rejects_before_tool_side_effects(
    settings, fake, monkeypatch, failure
):
    server = create_server(configured(settings), transport=httpx.MockTransport(fake.handle))
    ingress = next(item for item in server.middleware if isinstance(item, ProvenanceIngress))
    original_write = ingress._journal.write

    def fail_write(*args):
        if isinstance(failure, CommitUnknown):
            original_write(*args)
        raise failure

    monkeypatch.setattr(ingress._journal, "write", fail_write)
    async with Client(server) as client:
        args = {
            "template": "text-to-image",
            "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flower"},
        }
        meta = signed("workflows.build", args)
        with pytest.raises(MCPError, match="Untrusted external provenance"):
            await client.session.call_tool("workflows.build", args, meta=meta)
        listing = await client.call_tool("workflows.list")
        assert listing.structured_content["built_workflows"] == []
        assert not fake.prompts
        if isinstance(failure, CommitUnknown):
            restarted = ProvenanceIngress(configured(settings))
            with pytest.raises(ValueError):
                restarted.authenticate(meta, {"name": "workflows.build", "arguments": args})


def test_corrupt_durable_nonce_authority_rejects_only_claimed_context():
    settings = configured()
    ingress = ProvenanceIngress(settings)
    params = {"name": "system.health", "arguments": {}}
    ingress.authenticate(signed("system.health", {}), params)
    ingress._journal.write("nonces.json", {"version": 1, "nonces": {}})
    restarted = ProvenanceIngress(settings)
    with pytest.raises(ValueError):
        restarted.authenticate(signed("system.health", {}), params)
    assert restarted.authenticate(None, params) is None


def test_missing_nonce_journal_is_not_pristine_after_restart():
    settings = configured()
    ingress = ProvenanceIngress(settings)
    params = {"name": "system.health", "arguments": {}}
    meta = signed("system.health", {})
    ingress.authenticate(meta, params)
    (settings.output_dir / "external-provenance" / "nonces.json").unlink()
    restarted = ProvenanceIngress(settings)
    with pytest.raises(ValueError):
        restarted.authenticate(meta, params)
    assert restarted.authenticate(None, params) is None


async def test_job_journal_and_completed_archive_preserve_provenance(stores, fake, settings):
    workflows, jobs, _ = stores
    workflow = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "flower"}
    )
    provenance = ExternalProvenance(issuer=ISSUER, subject="client-a")
    job_id = (await jobs.submit(workflow["workflow_id"], provenance=provenance))["job_id"]
    restarted = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    assert restarted._jobs[job_id].external_provenance == provenance
    fake.finish()
    assert (await jobs.status(job_id))["external_provenance"] == provenance.model_dump()
    assert (await jobs.result(job_id))["external_provenance"] == provenance.model_dump()
    archived = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    assert (await archived.status(job_id)) == {
        "job_id": job_id,
        "status": "completed",
        "provider_id": "comfyui",
        "provider": "comfyui",
        "operation": "image.generate",
        "workflow_id": workflow["workflow_id"],
        "archived": True,
        "output_count": 1,
        "external_provenance": provenance.model_dump(),
    }
    asset = (await archived.list_assets(job_id))["assets"][0]
    assert asset["external_provenance"] == provenance.model_dump()
    assert (await archived.get_asset(asset["asset_id"]))[0][
        "external_provenance"
    ] == provenance.model_dump()
    transfer = await AssetTransfers(archived).prepare(asset["asset_id"])
    assert transfer["external_provenance"] == provenance.model_dump()
    manifest = settings.output_dir / job_id / "metadata.json"
    record = json.loads(manifest.read_text())
    assert SECRET not in manifest.read_text()
    record["external_provenance"]["studio_user_id"] = "forged"
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="external provenance"):
        await archived.list_assets(job_id)
    with pytest.raises(ValueError, match="external provenance"):
        await AssetTransfers(archived).prepare(asset["asset_id"])
    with pytest.raises(ValueError, match="archived completed job"):
        await archived.status(job_id)


async def test_completed_archive_status_needs_no_provider_or_media_bytes(stores, fake, settings):
    workflows, jobs, _ = stores
    workflow = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "flower"}
    )
    provenance = ExternalProvenance(issuer=ISSUER, subject="client-a")
    job_id = (await jobs.submit(workflow["workflow_id"], provenance=provenance))["job_id"]
    fake.finish()
    await jobs.status(job_id)
    provider_calls = len(fake.calls)
    archived = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    assert (await archived.status(job_id))["external_provenance"] == provenance.model_dump()
    asset = (await archived.list_assets(job_id))["assets"][0]
    assert asset["materialized"] is False
    assert asset["size_bytes"] is None
    assert len(fake.calls) == provider_calls
    assert fake.download_count == 0


@pytest.mark.parametrize("identified", [False, True])
async def test_provider_metadata_cannot_forge_external_provenance(
    stores, fake, settings, monkeypatch, identified
):
    from dataclasses import replace

    workflows, jobs, _ = stores
    provider = jobs.providers.get("comfyui")
    original = provider.inspect

    async def forged_metadata(execution_id):
        snapshot = await original(execution_id)
        return replace(
            snapshot,
            metadata={"external_provenance": {"issuer": "forged-provider", "subject": "owner"}},
        )

    monkeypatch.setattr(provider, "inspect", forged_metadata)
    workflow = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "flower"}
    )
    provenance = ExternalProvenance(issuer=ISSUER, subject="client-a") if identified else None
    job_id = (await jobs.submit(workflow["workflow_id"], provenance=provenance))["job_id"]
    fake.finish()
    metadata = await jobs.status(job_id)
    archived = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    archived_metadata = await archived.status(job_id)
    asset = (await archived.list_assets(job_id))["assets"][0]
    for value in (metadata, archived_metadata, asset):
        if identified:
            assert value["external_provenance"] == provenance.model_dump()
        else:
            assert "external_provenance" not in value


@pytest.mark.parametrize("change", ["status", "job_id", "provider_id", "outputs", "deleted"])
async def test_archived_status_rejects_incomplete_or_malformed_records(
    stores, fake, settings, change
):
    workflows, jobs, _ = stores
    workflow = workflows.build(
        "text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "flower"}
    )
    job_id = (await jobs.submit(workflow["workflow_id"]))["job_id"]
    fake.finish()
    await jobs.status(job_id)
    archived = JobStore(workflows, jobs.providers, jobs.capabilities, settings.output_dir)
    manifest = settings.output_dir / job_id / "metadata.json"
    record = json.loads(manifest.read_text())
    if change == "deleted":
        await archived.delete_asset(f"{job_id}:000")
    else:
        record[change] = {
            "status": "unknown",
            "job_id": "0" * 32,
            "provider_id": "../",
            "outputs": [],
        }[change]
        manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="archived completed job"):
        await archived.status(job_id)
    assert fake.download_count == 0


async def test_public_arguments_and_headers_cannot_supply_provenance(settings, fake):
    server = create_server(configured(settings), transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        for tool in tools:
            assert "external_provenance" not in tool.input_schema.get("properties", {})
            assert "ctx" not in tool.input_schema.get("properties", {})
        built = await client.call_tool(
            "workflows.build",
            {
                "template": "text-to-image",
                "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flower"},
            },
        )
        result = await client.call_tool(
            "jobs.submit",
            {
                "workflow_id": built.structured_content["workflow_id"],
                "external_provenance": {"issuer": ISSUER, "subject": "forged"},
                "ctx": {"external_provenance": {"issuer": ISSUER, "subject": "forged"}},
            },
        )
        assert not result.is_error
        assert "external_provenance" not in result.structured_content


async def test_http_same_session_principals_are_request_scoped(settings, fake, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    settings = configured(settings)
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))

    @server.tool(name="test.provenance")
    async def inspect_context() -> dict[str, object]:
        before = current_provenance()
        await asyncio.sleep(0.005)
        after = current_provenance()
        return {
            "before": before.model_dump() if before else None,
            "after": after.model_dump() if after else None,
        }

    async with serve_http(server, settings) as url:
        async with Client(url + settings.mcp_path) as client:
            replies = await asyncio.gather(
                client.session.call_tool(
                    "test.provenance", {}, meta=signed("test.provenance", {}, subject="client-a")
                ),
                client.session.call_tool(
                    "test.provenance", {}, meta=signed("test.provenance", {}, subject="client-b")
                ),
                client.session.call_tool("test.provenance", {}),
            )
            for reply, subject in zip(replies, ("client-a", "client-b", None), strict=True):
                expected = {"issuer": ISSUER, "subject": subject} if subject else None
                assert reply.structured_content == {"before": expected, "after": expected}
            assert current_provenance() is None
            with pytest.raises(MCPError, match="Untrusted external provenance"):
                await client.session.call_tool(
                    "workflows.build", {}, meta={META_KEY: {"subject": "forged"}}
                )
            built = await client.call_tool(
                "workflows.build",
                {
                    "template": "text-to-image",
                    "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flower"},
                },
            )
            arguments = {"workflow_id": built.structured_content["workflow_id"]}
            for index, subject in enumerate(("client-a", "client-b", None), start=1):
                submitted = await client.session.call_tool(
                    "jobs.submit",
                    arguments,
                    meta=signed("jobs.submit", arguments, subject=subject) if subject else None,
                )
                job_id = submitted.structured_content["job_id"]
                fake.finish(f"prompt-{index}")
                status = await client.call_tool("jobs.status", {"job_id": job_id})
                if subject:
                    assert status.structured_content["external_provenance"] == {
                        "issuer": ISSUER,
                        "subject": subject,
                    }
                else:
                    assert "external_provenance" not in status.structured_content
            assert len(fake.prompts) == 3

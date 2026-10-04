import fcntl
import json
import os
from contextlib import asynccontextmanager

import httpx
import pytest
from conftest import validation_rejection
from mcp import Client
from test_inputs import png
from test_workflow_registry import configured

from flamoris_generation_mcp.comfyui import ComfyUIClient
from flamoris_generation_mcp.config import Settings
from flamoris_generation_mcp.durable import CommitUnknown
from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.providers.base import (
    GenerationRequest,
    SubmissionRejected,
    SubmissionUnknown,
)
from flamoris_generation_mcp.providers.comfyui import ComfyUIProvider
from flamoris_generation_mcp.providers.comfyui_inputs import ComfyUIInputs
from flamoris_generation_mcp.server import create_server
from flamoris_generation_mcp.workflows import WorkflowStore

JOB = "a" * 32
OTHER = "b" * 32


def ledger(root):
    return json.loads((root / "flamoris-inputs/.ledger.json").read_text())["files"]


@pytest.mark.parametrize("status", ["queued", "running", "unknown", "cancel_requested"])
def test_restart_and_nonterminal_observation_preserve_charge(tmp_path, status):
    store = ComfyUIInputs(tmp_path, max_files=1, max_bytes=4)
    name = store.stage(JOB, b"data", "image/png")
    store.bind(JOB, "execution-1")
    restarted = ComfyUIInputs(tmp_path, max_files=1, max_bytes=4)
    restarted.observe("execution-1", status)
    assert (tmp_path / name).read_bytes() == b"data"
    with pytest.raises(ValueError, match="budget exhausted"):
        restarted.stage(OTHER, b"x", "image/png")


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_only_matching_terminal_execution_releases_multiple_inputs(tmp_path, status):
    store = ComfyUIInputs(tmp_path, max_files=2, max_bytes=8)
    names = [store.stage(JOB, b"data", "image/png") for _ in range(2)]
    store.bind(JOB, "execution-1")
    store.observe("unrelated", status)
    assert all((tmp_path / name).exists() for name in names)
    ComfyUIInputs(tmp_path, max_files=2, max_bytes=8).observe("execution-1", status)
    assert all(not (tmp_path / name).exists() for name in names)
    assert ledger(tmp_path) == {}
    store.stage(OTHER, b"new", "image/png")


def test_acceptance_crash_without_binding_stays_protected(tmp_path):
    store = ComfyUIInputs(tmp_path, max_files=1)
    name = store.stage(JOB, b"data", "image/png")
    restarted = ComfyUIInputs(tmp_path, max_files=1)
    restarted.observe("unrecoverable-execution", "completed")
    assert (tmp_path / name).exists()
    with pytest.raises(ValueError, match="budget exhausted"):
        restarted.stage(OTHER, b"x", "image/png")


def test_definite_prepost_rejection_releases_partial_multi_input_batch(tmp_path):
    store = ComfyUIInputs(tmp_path, max_bytes=5)
    name = store.stage(JOB, b"data", "image/png")
    with pytest.raises(ValueError, match="budget exhausted"):
        store.stage(JOB, b"data", "image/png")
    store.rejected(JOB)
    assert not (tmp_path / name).exists()
    assert ledger(tmp_path) == {}


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "replace", "modify"])
def test_changed_files_are_never_deleted(tmp_path, kind):
    store = ComfyUIInputs(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    candidate = tmp_path / name
    foreign = tmp_path / "foreign.png"
    foreign.write_bytes(b"safe")
    if kind == "symlink":
        candidate.unlink()
        candidate.symlink_to(foreign)
    elif kind == "hardlink":
        candidate.unlink()
        os.link(foreign, candidate)
    elif kind == "replace":
        os.replace(foreign, candidate)
    else:
        candidate.write_bytes(b"edit")
    with pytest.raises(ValueError):
        store.before_post(JOB)
    with pytest.raises(ValueError):
        store.rejected(JOB)
    assert candidate.read_bytes() == (b"edit" if kind == "modify" else b"safe")


def test_before_post_checks_content_even_with_restored_timestamp(tmp_path):
    store = ComfyUIInputs(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    candidate = tmp_path / name
    info = candidate.stat()
    candidate.write_bytes(b"edit")
    os.utime(candidate, ns=(info.st_atime_ns, info.st_mtime_ns))
    with pytest.raises(ValueError, match="changed before submission"):
        store.before_post(JOB)


def test_replacement_during_prepost_read_is_rejected(tmp_path, monkeypatch):
    store = ComfyUIInputs(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    read = os.read

    def swap_during_read(fd, count):
        data = read(fd, count)
        if data == b"data":
            foreign = tmp_path / "replacement.png"
            foreign.write_bytes(b"edit")
            os.rename(tmp_path / name, tmp_path / "preserved-original.png")
            os.replace(foreign, tmp_path / name)
        return data

    monkeypatch.setattr(os, "read", swap_during_read)
    with pytest.raises(ValueError, match="changed before submission"):
        store.before_post(JOB)


@pytest.mark.parametrize("root_kind", ["unset", "missing", "symlink", "accessible"])
async def test_readiness_requires_configured_accessible_shared_root(
    settings, tmp_path, fake, root_kind
):
    root = tmp_path / "input-root"
    if root_kind in {"symlink", "accessible"}:
        root.mkdir()
    if root_kind == "symlink":
        link = tmp_path / "input-link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    settings = settings.model_copy(
        update={
            "managed_input_ready": True,
            "comfyui_input_root": None if root_kind == "unset" else root,
        }
    )
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        health = await client.call_tool("system.health")
        assert health.structured_content["managed_input_support"]["ready"] is (
            root_kind == "accessible"
        )
        if root_kind == "accessible":
            (root / "flamoris-inputs/unrecorded.png").write_bytes(b"keep")
            health = await client.call_tool("system.health")
            assert health.structured_content["managed_input_support"]["ready"] is False


@pytest.mark.parametrize(
    "field,value",
    [("provider_input_max_files", 129), ("provider_input_max_bytes", 512 * 1024**2 + 1)],
)
def test_provider_input_limits_cannot_exceed_hard_budget(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


def test_provider_input_env_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("FLAMORIS_COMFYUI_INPUT_ROOT", str(tmp_path))
    monkeypatch.setenv("FLAMORIS_PROVIDER_INPUT_MAX_FILES", "3")
    monkeypatch.setenv("FLAMORIS_PROVIDER_INPUT_MAX_BYTES", "1024")
    settings = Settings.from_env()
    assert settings.comfyui_input_root == tmp_path
    assert settings.provider_input_max_files == 3 and settings.provider_input_max_bytes == 1024


def test_release_recovers_crash_after_quarantine_rename(tmp_path, monkeypatch):
    store = ComfyUIInputs(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    store.bind(JOB, "execution-1")
    unlink = os.unlink

    def crash(path, **kwargs):
        if str(path).startswith(".cleanup-"):
            raise OSError("simulated crash before unlink")
        return unlink(path, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", crash)
        with pytest.raises(OSError):
            store.observe("execution-1", "completed")
    assert not (tmp_path / name).exists()
    assert next(iter(ledger(tmp_path).values()))["state"] == "released"
    ComfyUIInputs(tmp_path).observe("execution-1", "completed")
    assert ledger(tmp_path) == {}
    assert not list((tmp_path / "flamoris-inputs").glob(".cleanup-*"))


def test_replacement_during_cleanup_is_quarantined_not_deleted(tmp_path, monkeypatch):
    store = ComfyUIInputs(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    rename = os.rename
    foreign = tmp_path / "foreign.png"
    foreign.write_bytes(b"safe")

    def replace_before_rename(source, target, **kwargs):
        os.replace(foreign, tmp_path / name)
        return rename(source, target, **kwargs)

    monkeypatch.setattr(os, "rename", replace_before_rename)
    with pytest.raises(ValueError, match="changed during cleanup"):
        store.rejected(JOB)
    quarantine = next((tmp_path / "flamoris-inputs").glob(".cleanup-*"))
    assert quarantine.read_bytes() == b"safe"
    assert len(ledger(tmp_path)) == 1


def test_unrecorded_and_old_http_uploads_are_not_swept(tmp_path):
    legacy = tmp_path / "flamoris-old-job.png"
    legacy.write_bytes(b"old")
    store = ComfyUIInputs(tmp_path)
    assert store.available()
    unmanaged = tmp_path / "flamoris-inputs/unrecorded.png"
    unmanaged.write_bytes(b"keep")
    with pytest.raises(ValueError, match="Unrecorded"):
        store.stage(JOB, b"data", "image/png")
    assert legacy.read_bytes() == b"old" and unmanaged.read_bytes() == b"keep"


def test_symlinked_parent_and_busy_lock_reject_immediately(tmp_path):
    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    assert not ComfyUIInputs(link).available()
    store = ComfyUIInputs(actual)
    assert store.available()
    with (actual / "flamoris-inputs/.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not store.available()
        with pytest.raises(BlockingIOError):
            store.stage(JOB, b"data", "image/png")


def test_group_writable_namespace_is_not_adopted(tmp_path):
    namespace = tmp_path / "flamoris-inputs"
    namespace.mkdir()
    namespace.chmod(0o770)
    assert not ComfyUIInputs(tmp_path).available()
    assert not list(namespace.iterdir())


def test_uncertain_reservation_commit_never_creates_unbudgeted_payload(tmp_path, monkeypatch):
    store = ComfyUIInputs(tmp_path, max_files=1)
    original = store._write

    def uncertain(*args):
        original(*args)
        raise CommitUnknown("simulated directory durability fault")

    monkeypatch.setattr(store, "_write", uncertain)
    with pytest.raises(CommitUnknown):
        store.stage(JOB, b"data", "image/png")
    assert len(ledger(tmp_path)) == 1
    assert not list((tmp_path / "flamoris-inputs").glob("*.png"))
    with pytest.raises(ValueError, match="budget exhausted"):
        ComfyUIInputs(tmp_path, max_files=1).stage(OTHER, b"x", "image/png")


def test_crash_after_payload_before_identity_commit_preserves_copy(tmp_path, monkeypatch):
    store = ComfyUIInputs(tmp_path)
    original = store._write
    calls = 0

    def crash(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated crash")
        return original(*args)

    monkeypatch.setattr(store, "_write", crash)
    with pytest.raises(OSError):
        store.stage(JOB, b"data", "image/png")
    with pytest.raises(ValueError, match="identity changed"):
        ComfyUIInputs(tmp_path).rejected(JOB)
    assert next((tmp_path / "flamoris-inputs").glob("*.png")).read_bytes() == b"data"


def adapter(settings, tmp_path, handler, *, extra_input=False):
    settings, definitions = configured(settings, tmp_path)
    path = definitions / "basic-image.json"
    data = json.loads(path.read_text())
    for name, node in [("source", "8"), *([("second", "9")] if extra_input else [])]:
        data["graph"][node] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        data["parameters"][name] = {
            "type": "managed_input",
            "node": node,
            "input": "image",
            "media_types": ["image/png"],
        }
    path.write_text(json.dumps(data))
    settings = settings.model_copy(update={"comfyui_input_root": tmp_path})
    catalog = ModelCatalog(settings)
    workflows = WorkflowStore(catalog, settings.workflow_dir, definitions)
    values = {"checkpoint": "base.safetensors", "positive_prompt": "reference", "source": JOB}
    if extra_input:
        values["second"] = OTHER
    key = workflows.build("basic-image", values)["workflow_id"]
    recipe = workflows.get(key)

    class Reader:
        metadata = {
            "mime_type": "image/png",
            "input_id": JOB,
            "source_asset_id": None,
            "size_bytes": len(png()),
            "sha256": "a" * 64,
            "source_kind": "upload",
        }

        def __init__(self, content):
            self.content = content

        async def chunks(self):
            yield self.content

    class Inputs:
        @asynccontextmanager
        async def stage(self, *_args):
            yield {
                "source": Reader(png()),
                **({"second": Reader(b"invalid")} if extra_input else {}),
            }

    provider = ComfyUIProvider(
        ComfyUIClient(settings, httpx.MockTransport(handler)), catalog, workflows, Inputs()
    )
    return provider, GenerationRequest(operation="image.generate", workflow_id=key, payload=recipe)


@pytest.mark.parametrize("outcome", ["lost_ack", "bad_ack", "definite_rejection", "binding_fault"])
async def test_adapter_post_faults_preserve_unknown_and_release_only_definite_rejection(
    settings, tmp_path, fake, monkeypatch, outcome
):
    def handler(request):
        assert request.url.path != "/upload/image"
        if request.url.path == "/prompt":
            if outcome == "definite_rejection":
                return validation_rejection()
            fake.handle(request)
            if outcome == "lost_ack":
                raise httpx.ReadTimeout("acknowledgement lost", request=request)
            if outcome == "bad_ack":
                return httpx.Response(200, json={"prompt_id": None})
            return httpx.Response(200, json={"prompt_id": "prompt-1"})
        return fake.handle(request)

    provider, request = adapter(settings, tmp_path, handler)
    if outcome == "binding_fault":
        monkeypatch.setattr(
            provider.input_copies, "bind", lambda *_: (_ for _ in ()).throw(OSError("fault"))
        )
    error = (
        SubmissionRejected
        if outcome == "definite_rejection"
        else OSError
        if outcome == "binding_fault"
        else SubmissionUnknown
    )
    try:
        with pytest.raises(error):
            await provider.submit(request, JOB)
        copies = list((tmp_path / "flamoris-inputs").glob("*.png"))
        assert len(copies) == (0 if outcome == "definite_rejection" else 1)
        if copies:
            fake.finish()
            ComfyUIInputs(tmp_path).observe("prompt-1", "completed")
            assert copies[0].exists()  # The missing binding never grants cleanup authority.
        assert len(fake.prompts) == (0 if outcome == "definite_rejection" else 1)
    finally:
        await provider.close()


async def test_adapter_validation_after_first_copy_releases_batch(settings, tmp_path, fake):
    provider, request = adapter(settings, tmp_path, fake.handle, extra_input=True)
    try:
        with pytest.raises(SubmissionRejected):
            await provider.submit(request, JOB)
        assert ledger(tmp_path) == {}
        assert not fake.prompts
    finally:
        await provider.close()


async def test_recovered_adapter_queue_absence_and_cancel_request_never_release(
    settings, tmp_path, fake
):
    provider, request = adapter(settings, tmp_path, fake.handle)
    try:
        ack = await provider.submit(request, JOB)
        copy = next((tmp_path / "flamoris-inputs").glob("*.png"))
        # An adapter restored without output publication authority still owns status
        # observation. Queue deletion/absence has no terminal acknowledgement.
        assert (await provider.cancel_owned(ack.execution_id)).status == "unknown"
        assert copy.exists()
        fake.running = [[1, ack.execution_id]]
        provider.client.settings.targeted_interrupt = True
        assert (await provider.cancel_owned(ack.execution_id)).status == "cancel_requested"
        assert copy.exists()
        fake.finish(ack.execution_id, "error", [["execution_interrupted", {}]])
        provider.input_copies = ComfyUIInputs(tmp_path)  # Persistent ledger, new adapter state.
        assert (await provider.inspect_owned(ack.execution_id)).status == "cancelled"
        assert not copy.exists()
    finally:
        await provider.close()

import fcntl
import json
import os

import httpx
import pytest
from flamoris_generation_controller.durable import CommitUnknown
from flamoris_generation_controller.providers.comfyui_inputs import ComfyUIInputs
from mcp import Client

from flamoris_generation_mcp.config import Settings
from flamoris_generation_mcp.server import create_server

JOB = "a" * 32
OTHER = "b" * 32


def copies(root, **kwargs):
    state_root = kwargs.pop("state_root", root.parent / (root.name + "-generation-state"))
    return ComfyUIInputs(root, state_root=state_root, **kwargs)


def ledger(root):
    return json.loads((root / "flamoris-inputs/.ledger.json").read_text())["files"]


@pytest.mark.parametrize("status", ["queued", "running", "unknown", "cancel_requested"])
def test_restart_and_nonterminal_observation_preserve_charge(tmp_path, status):
    store = copies(tmp_path, max_files=1, max_bytes=4)
    name = store.stage(JOB, b"data", "image/png")
    store.bind(JOB, "execution-1")
    restarted = copies(tmp_path, max_files=1, max_bytes=4)
    restarted.observe("execution-1", status)
    assert (tmp_path / name).read_bytes() == b"data"
    with pytest.raises(ValueError, match="budget exhausted"):
        restarted.stage(OTHER, b"x", "image/png")


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_only_matching_terminal_execution_releases_multiple_inputs(tmp_path, status):
    store = copies(tmp_path, max_files=2, max_bytes=8)
    names = [store.stage(JOB, b"data", "image/png") for _ in range(2)]
    store.bind(JOB, "execution-1")
    store.observe("unrelated", status)
    assert all((tmp_path / name).exists() for name in names)
    copies(tmp_path, max_files=2, max_bytes=8).observe("execution-1", status)
    assert all(not (tmp_path / name).exists() for name in names)
    assert ledger(tmp_path) == {}
    store.stage(OTHER, b"new", "image/png")


def test_acceptance_crash_without_binding_stays_protected(tmp_path):
    store = copies(tmp_path, max_files=1)
    name = store.stage(JOB, b"data", "image/png")
    restarted = copies(tmp_path, max_files=1)
    restarted.observe("unrecoverable-execution", "completed")
    assert (tmp_path / name).exists()
    with pytest.raises(ValueError, match="budget exhausted"):
        restarted.stage(OTHER, b"x", "image/png")


@pytest.mark.parametrize("target", ["root", "namespace", "lock"])
def test_replaced_storage_cannot_reset_protected_charges_after_restart(tmp_path, target):
    root = tmp_path / "inputs"
    root.mkdir()
    state = tmp_path / "generation"
    store = copies(root, state_root=state, max_files=1)
    name = store.stage(JOB, b"data", "image/png")
    store.bind(JOB, "execution-1")
    old = tmp_path / "old"
    if target == "root":
        root.rename(old)
        root.mkdir()
        namespace = root / "flamoris-inputs"
        namespace.mkdir()
        (namespace / ".lock").touch()
        saved = old / name
    elif target == "namespace":
        (root / "flamoris-inputs").rename(old)
        namespace = root / "flamoris-inputs"
        namespace.mkdir()
        (namespace / ".lock").touch()
        saved = old / name.split("/")[1]
    else:
        (root / "flamoris-inputs/.lock").rename(old)
        (root / "flamoris-inputs/.lock").touch()
        saved = root / name
    restarted = copies(root, state_root=state, max_files=1)
    assert not restarted.available()
    with pytest.raises(ValueError, match="storage identity changed"):
        restarted.stage(OTHER, b"x", "image/png")
    assert saved.read_bytes() == b"data"
    # Restoring the actual old identity restores terminal reconciliation, no replay.
    if target == "root":
        (root / "flamoris-inputs/.lock").unlink()
        (root / "flamoris-inputs").rmdir()
        root.rmdir()
        old.rename(root)
    elif target == "namespace":
        (root / "flamoris-inputs/.lock").unlink()
        (root / "flamoris-inputs").rmdir()
        old.rename(root / "flamoris-inputs")
    else:
        (root / "flamoris-inputs/.lock").unlink()
        old.rename(root / "flamoris-inputs/.lock")
    restarted.observe("execution-1", "completed")
    assert ledger(root) == {}


def test_missing_stable_lock_is_never_recreated(tmp_path):
    store = copies(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    lock = tmp_path / "flamoris-inputs/.lock"
    lock.unlink()
    assert not store.available()
    assert not lock.exists()
    assert (tmp_path / name).read_bytes() == b"data"


def test_lock_replaced_during_receipt_write_aborts_before_copy(tmp_path, monkeypatch):
    store = copies(tmp_path)
    assert store.available()
    current = store._current
    called = 0

    def replace_lock(root_fd, fd, lock):
        nonlocal called
        called += 1
        if called == 3:
            (tmp_path / "flamoris-inputs/.lock").rename(tmp_path / "old-lock")
            (tmp_path / "flamoris-inputs/.lock").touch()
        return current(root_fd, fd, lock)

    monkeypatch.setattr(store, "_current", replace_lock)
    with pytest.raises(ValueError, match="lock changed"):
        store.stage(JOB, b"data", "image/png")
    assert not list((tmp_path / "flamoris-inputs").glob("*.png"))
    assert not (tmp_path / "flamoris-inputs/.ledger.json").exists()


def test_input_authority_cannot_be_lost_with_replaced_input_root(tmp_path):
    with pytest.raises(ValueError, match="outside"):
        copies(tmp_path, state_root=tmp_path / "state")
    link = tmp_path.parent / (tmp_path.name + "-alias")
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        copies(tmp_path, state_root=link / "state")


@pytest.mark.parametrize("kind", ["incomplete", "missing"])
def test_unresolved_copy_never_reports_ready(tmp_path, kind):
    store = copies(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    if kind == "incomplete":
        raw = ledger(tmp_path)
        raw[name.split("/")[1]]["identity"] = None
        (tmp_path / "flamoris-inputs/.ledger.json").write_text(
            json.dumps({"schema_version": 1, "files": raw})
        )
    else:
        (tmp_path / name).unlink()
    assert not copies(tmp_path).available()
    assert len(ledger(tmp_path)) == 1


def test_uncertain_storage_anchor_never_creates_a_payload(tmp_path, monkeypatch):
    store = copies(tmp_path)
    original = store.authority.write

    def uncertain(*args):
        original(*args)
        raise CommitUnknown("simulated anchor directory durability fault")

    monkeypatch.setattr(store.authority, "write", uncertain)
    with pytest.raises(CommitUnknown):
        store.stage(JOB, b"data", "image/png")
    assert not list((tmp_path / "flamoris-inputs").glob("*.png"))
    assert copies(tmp_path).available()


@pytest.mark.parametrize("kind", ["boolean_schema", "float_identity", "extra_key"])
def test_invalid_storage_authority_fails_closed(tmp_path, kind):
    store = copies(tmp_path)
    assert store.available()
    anchor = store.authority.read("root.json")
    if kind == "boolean_schema":
        anchor["schema_version"] = True
    elif kind == "float_identity":
        anchor["root"][0] = float(anchor["root"][0])
    else:
        anchor["unexpected"] = "value"
    store.authority.write("root.json", anchor)
    with pytest.raises(ValueError, match="Invalid.*authority"):
        copies(tmp_path).stage(JOB, b"data", "image/png")
    assert not list((tmp_path / "flamoris-inputs").glob("*.png"))


def test_definite_prepost_rejection_releases_partial_multi_input_batch(tmp_path):
    store = copies(tmp_path, max_bytes=5)
    name = store.stage(JOB, b"data", "image/png")
    with pytest.raises(ValueError, match="budget exhausted"):
        store.stage(JOB, b"data", "image/png")
    store.rejected(JOB)
    assert not (tmp_path / name).exists()
    assert ledger(tmp_path) == {}


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "replace", "modify"])
def test_changed_files_are_never_deleted(tmp_path, kind):
    store = copies(tmp_path)
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
    store = copies(tmp_path)
    name = store.stage(JOB, b"data", "image/png")
    candidate = tmp_path / name
    info = candidate.stat()
    candidate.write_bytes(b"edit")
    os.utime(candidate, ns=(info.st_atime_ns, info.st_mtime_ns))
    with pytest.raises(ValueError, match="changed before submission"):
        store.before_post(JOB)


def test_replacement_during_prepost_read_is_rejected(tmp_path, monkeypatch):
    store = copies(tmp_path)
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
            "comfyui_input_root": None if root_kind == "unset" else root,
        }
    )
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        health = await client.call_tool("system.health")
        support = health.structured_content["managed_input_support"]
        assert support["ready"] is (kind == "accessible")
        assert support["reference_execution"] == "checkpoint-comfy-v1"
        assert support["retained_copy_store_available"] is (root_kind == "accessible")
        if root_kind == "accessible":
            (root / "flamoris-inputs/unrecorded.png").write_bytes(b"keep")
            health = await client.call_tool("system.health")
            assert (
                health.structured_content["managed_input_support"]["retained_copy_store_available"]
                is False
            )


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
    store = copies(tmp_path)
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
    copies(tmp_path).observe("execution-1", "completed")
    assert ledger(tmp_path) == {}
    assert not list((tmp_path / "flamoris-inputs").glob(".cleanup-*"))


def test_replacement_during_cleanup_is_quarantined_not_deleted(tmp_path, monkeypatch):
    store = copies(tmp_path)
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
    store = copies(tmp_path)
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
    assert not copies(link).available()
    store = copies(actual)
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
    assert not copies(tmp_path).available()
    assert not list(namespace.iterdir())


def test_uncertain_reservation_commit_never_creates_unbudgeted_payload(tmp_path, monkeypatch):
    store = copies(tmp_path, max_files=1)
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
        copies(tmp_path, max_files=1).stage(OTHER, b"x", "image/png")


def test_crash_after_payload_before_identity_commit_preserves_copy(tmp_path, monkeypatch):
    store = copies(tmp_path)
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
        copies(tmp_path).rejected(JOB)
    assert next((tmp_path / "flamoris-inputs").glob("*.png")).read_bytes() == b"data"

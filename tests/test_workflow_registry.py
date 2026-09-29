import json
import shutil
from pathlib import Path

import httpx
import pytest
from mcp import Client

from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.server import create_server
from flamoris_generation_mcp.workflows import WorkflowStore

EXAMPLE = (
    Path(__file__).parents[1] / "src/flamoris_generation_mcp/example_definitions/basic-image.json"
)


def configured(settings, tmp_path):
    root = tmp_path / "definitions"
    root.mkdir()
    shutil.copy(EXAMPLE, root / "basic-image.json")
    return settings.model_copy(update={"workflow_definition_dir": root}), root


def test_discovery_binding_save_and_restart(settings, tmp_path):
    settings, root = configured(settings, tmp_path)
    original = (root / "basic-image.json").read_bytes()
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    definition = store.list()["definitions"][0]
    assert definition["id"] == "basic-image" and definition["version"] == 1
    assert "graph" not in definition
    assert "node" not in definition["parameters"]["positive_prompt"]
    assert definition["parameters"]["positive_prompt"]["required"]
    values = {"checkpoint": "base.safetensors", "positive_prompt": "flowers", "cfg": 2.5}
    first = store.build("basic-image", values)
    second = store.build("basic-image", values)
    assert first["workflow_id"] != second["workflow_id"]
    assert first["prompt"] == second["prompt"]
    assert first["prompt"]["5"]["inputs"]["cfg"] == 2.5
    assert first["prompt"]["5"]["inputs"]["steps"] == 20
    first["prompt"]["2"]["inputs"]["text"] = "tampered"
    store.save(first["workflow_id"])
    renewed = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    recipe = renewed.get(first["workflow_id"])
    assert renewed.prompt(recipe)["2"]["inputs"]["text"] == "flowers"
    assert renewed.list()["saved_workflows"] == [first["workflow_id"]]
    assert (root / "basic-image.json").read_bytes() == original


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"positive_prompt": None}, "positive_prompt"),
        ({"positive_prompt": ""}, "positive_prompt"),
        ({"checkpoint": "../other.safetensors"}, "checkpoint"),
        ({"checkpoint": "missing.safetensors"}, "checkpoint"),
        ({"steps": 0}, "steps"),
        ({"steps": True}, "steps"),
        ({"cfg": float("inf")}, "cfg"),
        ({"sampler": "invalid"}, "sampler"),
        ({"graph": {"7": {}}}, "Unknown"),
    ],
)
def test_invalid_values(settings, tmp_path, changes, message):
    settings, root = configured(settings, tmp_path)
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    with pytest.raises(ValueError, match=message):
        store.build(
            "basic-image",
            {"checkpoint": "base.safetensors", "positive_prompt": "flower", **changes},
        )
    with pytest.raises(ValueError, match="Missing required"):
        store.build("basic-image", {"checkpoint": "base.safetensors"})


@pytest.mark.parametrize("tamper", ["outside", "binding", "duplicate", "version", "graph"])
def test_fail_closed_definition_loading(settings, tmp_path, tamper):
    settings, root = configured(settings, tmp_path)
    path = root / "basic-image.json"
    data = json.loads(path.read_text())
    if tamper == "outside":
        path.unlink()
        path.symlink_to(EXAMPLE)
    elif tamper == "binding":
        data["parameters"]["steps"]["node"] = "999"
    elif tamper == "duplicate":
        data["parameters"]["steps"]["input"] = "cfg"
        data["parameters"]["steps"]["node"] = "5"
    elif tamper == "version":
        data["schema_version"] = 999
    elif tamper == "graph":
        data["graph"]["7"]["inputs"].pop("filename_prefix")
    if tamper != "outside":
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)


async def test_runtime_registration_is_immediate_persistent_and_versioned(
    settings, tmp_path, fake
):
    root = tmp_path / "definitions"
    root.mkdir()
    settings = settings.model_copy(update={"workflow_definition_dir": root})
    definition = json.loads(EXAMPLE.read_text())
    definition["id"] = "runtime-image"
    definition["name"] = "Runtime image"

    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        registered = await client.call_tool("workflows.register", {"definition": definition})
        assert not registered.is_error
        assert registered.structured_content["id"] == "runtime-image"
        assert registered.structured_content["version"] == 1
        assert (root / "runtime-image.json").is_file()

        listed = await client.call_tool("workflows.list")
        metadata = next(
            item
            for item in listed.structured_content["definitions"]
            if item["id"] == "runtime-image"
        )
        assert metadata["version"] == 1

        capabilities = await client.call_tool("capabilities.get", {"capability_id": "image.generate"})
        assert "runtime-image" in capabilities.structured_content["workflow_templates"]

        built = await client.call_tool(
            "workflows.build",
            {
                "template": "runtime-image",
                "parameters": {
                    "checkpoint": "base.safetensors",
                    "positive_prompt": "registered now",
                },
            },
        )
        assert not built.is_error

        stale = await client.call_tool("workflows.register", {"definition": definition})
        assert stale.is_error

        definition["version"] = 2
        definition["description"] = "Updated without restart."
        updated = await client.call_tool("workflows.register", {"definition": definition})
        assert not updated.is_error
        assert updated.structured_content["version"] == 2

    restarted = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(restarted) as client:
        listed = await client.call_tool("workflows.list")
        metadata = next(
            item
            for item in listed.structured_content["definitions"]
            if item["id"] == "runtime-image"
        )
        assert metadata["version"] == 2


def test_runtime_registration_failure_keeps_previous_definition(settings, tmp_path, monkeypatch):
    root = tmp_path / "definitions"
    root.mkdir()
    settings = settings.model_copy(update={"workflow_definition_dir": root})
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    definition = json.loads(EXAMPLE.read_text())
    definition["id"] = "runtime-image"
    assert store.register_definition(definition)["version"] == 1
    before = (root / "runtime-image.json").read_bytes()

    definition["version"] = 2

    def fail_replace(*_args, **_kwargs):
        raise OSError("simulated publication failure")

    monkeypatch.setattr("flamoris_generation_mcp.workflow_registry.os.replace", fail_replace)
    with pytest.raises(OSError, match="publication"):
        store.register_definition(definition)

    assert store.registry.get("runtime-image").version == 1
    assert (root / "runtime-image.json").read_bytes() == before
    assert not list(root.glob(".definition-*.tmp"))


def test_runtime_registration_rejects_unsafe_definition_without_mutation(settings, tmp_path):
    root = tmp_path / "definitions"
    root.mkdir()
    settings = settings.model_copy(update={"workflow_definition_dir": root})
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    definition = json.loads(EXAMPLE.read_text())
    definition["id"] = "runtime-image"
    definition["graph"]["8"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["6", 0], "filename_prefix": "unexpected"},
    }

    with pytest.raises(ValueError, match="Invalid workflow definition"):
        store.register_definition(definition)
    assert "runtime-image" not in store.registry.definitions
    assert not (root / "runtime-image.json").exists()


def test_definition_version_pins_saved_recipe(settings, tmp_path):
    settings, root = configured(settings, tmp_path)
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    key = store.build("basic-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"})[
        "workflow_id"
    ]
    store.save(key)
    path = root / "basic-image.json"
    data = json.loads(path.read_text())
    data["version"] = 2
    path.write_text(json.dumps(data))
    new_store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    with pytest.raises(ValueError, match="version"):
        new_store.prompt(new_store.get(key))


def test_definition_environment_root_and_malformed_recipe(settings, tmp_path, monkeypatch):
    settings, root = configured(settings, tmp_path)
    monkeypatch.setenv("FLAMORIS_WORKFLOW_DEFINITION_DIR", str(root))
    assert settings.from_env().workflow_definition_dir == root
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    key = store.build("basic-image", {"checkpoint": "base.safetensors", "positive_prompt": "x"})[
        "workflow_id"
    ]
    store.save(key)
    path = settings.workflow_dir / f"{key}.json"
    data = json.loads(path.read_text())
    data["parameters"]["graph"] = {"unsafe": True}
    path.write_text(json.dumps(data))
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)
    with pytest.raises(ValueError, match="Unknown workflow parameter"):
        store.prompt(store.get(key))


async def test_mcp_build_and_shared_job_guard(settings, tmp_path, fake):
    settings, _ = configured(settings, tmp_path)
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        definitions = (await client.call_tool("workflows.list")).structured_content["definitions"]
        assert definitions[0]["id"] == "basic-image"
        built = await client.call_tool(
            "workflows.build",
            {
                "template": "basic-image",
                "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
            },
        )
        assert not built.is_error
        key = built.structured_content["workflow_id"]
        assert not (await client.call_tool("workflows.save", {"workflow_id": key})).is_error
        job = await client.call_tool("jobs.submit", {"workflow_id": key})
        assert not job.is_error
        assert fake.prompts[0]["prompt"]["7"]["inputs"]["filename_prefix"].startswith("flamoris/")
        busy = await client.call_tool("jobs.submit", {"workflow_id": key})
        assert busy.is_error and "busy" in busy.content[0].text
        fake.finish()
        done = await client.call_tool("jobs.result", {"job_id": job.structured_content["job_id"]})
        assert done.structured_content["status"] == "completed"


async def test_external_provider_rejection_releases_reservation(settings, tmp_path, fake):
    settings, _ = configured(settings, tmp_path)
    rejected = True

    def handler(request):
        nonlocal rejected
        if request.url.path == "/prompt" and rejected:
            return httpx.Response(400, json={"error": {"message": "invalid workflow"}})
        return fake.handle(request)

    server = create_server(settings, transport=httpx.MockTransport(handler))
    async with Client(server) as client:
        built = await client.call_tool(
            "workflows.build",
            {
                "template": "basic-image",
                "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
            },
        )
        key = built.structured_content["workflow_id"]
        failure = await client.call_tool("jobs.submit", {"workflow_id": key})
        assert failure.is_error
        assert "ComfyUI" in failure.content[0].text
        rejected = False
        success = await client.call_tool("jobs.submit", {"workflow_id": key})
        assert not success.is_error


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "string"},
        {"type": "string", "model_kind": "checkpoint"},
        {"type": "integer"},
    ],
)
def test_file_input_binding_fails_closed(settings, tmp_path, spec):
    settings, root = configured(settings, tmp_path)
    path = root / "basic-image.json"
    data = json.loads(path.read_text())
    data["graph"]["8"] = {
        "class_type": "LoadImage",
        "inputs": {"image": "sample.png"},
    }
    data["parameters"]["source"] = {"node": "8", "input": "image", **spec}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Malformed workflow definition"):
        WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)


async def test_managed_image_input_uploads_and_rewrites_only_declared_binding(
    settings, tmp_path, fake
):
    settings, root = configured(settings, tmp_path)
    path = root / "basic-image.json"
    data = json.loads(path.read_text())
    data["graph"]["8"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
    data["parameters"]["source"] = {
        "type": "managed_input",
        "node": "8",
        "input": "image",
        "media_types": ["image/png"],
    }
    path.write_text(json.dumps(data))

    uploads = []

    def handler(request):
        if request.url.path == "/view":
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfixture")
        if request.url.path == "/upload/image":
            uploads.append(request.content)
            return httpx.Response(
                200,
                json={"name": "managed-reference.png", "subfolder": "", "type": "input"},
            )
        return fake.handle(request)

    server = create_server(settings, transport=httpx.MockTransport(handler))
    async with Client(server) as client:
        source = await client.call_tool(
            "workflows.build",
            {
                "template": "text-to-image",
                "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "source"},
            },
        )
        source_job = await client.call_tool(
            "jobs.submit", {"workflow_id": source.structured_content["workflow_id"]}
        )
        fake.finish()
        managed = await client.call_tool(
            "inputs.create", {"asset_id": source_job.structured_content["job_id"] + ":000"}
        )
        assert not managed.is_error

        built = await client.call_tool(
            "workflows.build",
            {
                "template": "basic-image",
                "parameters": {
                    "checkpoint": "base.safetensors",
                    "positive_prompt": "reference portrait",
                    "source": managed.structured_content["input_id"],
                },
            },
        )
        assert not built.is_error
        assert built.structured_content["prompt"]["8"]["inputs"]["image"] == ""

        submitted = await client.call_tool(
            "jobs.submit", {"workflow_id": built.structured_content["workflow_id"]}
        )
        assert not submitted.is_error
        assert len(uploads) == 1
        assert fake.prompts[-1]["prompt"]["8"]["inputs"]["image"] == "managed-reference.png"
        assert managed.structured_content["input_id"].encode() not in uploads[0]
        job_id = submitted.structured_content["job_id"]
        expected = {
            key: managed.structured_content[key]
            for key in ("input_id", "source_asset_id", "sha256", "mime_type", "size_bytes")
        }
        for tool in ("jobs.status", "jobs.result"):
            response = await client.call_tool(tool, {"job_id": job_id})
            assert not response.is_error
            assert response.structured_content["managed_inputs"] == {"source": expected}
            assert "managed-reference.png" not in json.dumps(response.structured_content)
        fake.finish("prompt-2")
        result = await client.call_tool("jobs.result", {"job_id": job_id})
        assert result.structured_content["status"] == "completed"
        assert result.structured_content["managed_inputs"] == {"source": expected}
        archived = json.loads((settings.output_dir / job_id / "metadata.json").read_text())
        assert archived["managed_inputs"] == {"source": expected}


@pytest.mark.parametrize("failure", ["wrong_media", "unknown", "deleted", "expired", "upload"])
async def test_managed_image_submit_failures_release_lease_and_reservation(
    settings, tmp_path, fake, monkeypatch, failure
):
    settings, root = configured(settings, tmp_path)
    path = root / "basic-image.json"
    data = json.loads(path.read_text())
    data["graph"]["8"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
    data["parameters"]["source"] = {
        "type": "managed_input",
        "node": "8",
        "input": "image",
        "media_types": ["image/jpeg"] if failure == "wrong_media" else ["image/png"],
    }
    path.write_text(json.dumps(data))

    uploads = []
    reject_upload = failure == "upload"

    def handler(request):
        if request.url.path == "/view":
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nfixture")
        if request.url.path == "/upload/image":
            uploads.append(request.content)
            if reject_upload:
                return httpx.Response(503, json={"error": "unavailable"})
            return httpx.Response(
                200, json={"name": "managed-reference.png", "subfolder": "", "type": "input"}
            )
        return fake.handle(request)

    server = create_server(settings, transport=httpx.MockTransport(handler))
    async with Client(server) as client:
        source = await client.call_tool(
            "workflows.build",
            {
                "template": "text-to-image",
                "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "source"},
            },
        )
        source_job = await client.call_tool(
            "jobs.submit", {"workflow_id": source.structured_content["workflow_id"]}
        )
        fake.finish()
        asset_id = source_job.structured_content["job_id"] + ":000"
        managed = await client.call_tool("inputs.create", {"asset_id": asset_id})
        assert not managed.is_error
        input_id = managed.structured_content["input_id"]
        failing_id = "f" * 32 if failure == "unknown" else input_id
        if failure == "deleted":
            assert not (await client.call_tool("inputs.delete", {"input_id": input_id})).is_error
        if failure == "expired":
            monkeypatch.setattr(
                "flamoris_generation_mcp.inputs.time.time",
                lambda: managed.structured_content["expires_at"] + 1,
            )

        built = await client.call_tool(
            "workflows.build",
            {
                "template": "basic-image",
                "parameters": {
                    "checkpoint": "base.safetensors",
                    "positive_prompt": "reference portrait",
                    "source": failing_id,
                },
            },
        )
        assert not built.is_error
        workflow_id = built.structured_content["workflow_id"]
        failed = await client.call_tool("jobs.submit", {"workflow_id": workflow_id})
        assert failed.is_error
        assert len(uploads) == (1 if failure == "upload" else 0)
        assert len(fake.prompts) == 1  # No prompt was submitted for the failed reference job.

        # A failed upload must close the lease; deletion would fail with "in use" otherwise.
        if failure == "upload":
            deleted = await client.call_tool("inputs.delete", {"input_id": input_id})
            assert not deleted.is_error
            reject_upload = False
        elif failure == "expired":
            monkeypatch.undo()

        # The same server must accept another job: the Hub reservation was released.
        retry = await client.call_tool(
            "jobs.submit", {"workflow_id": source.structured_content["workflow_id"]}
        )
        assert not retry.is_error
        assert len(fake.prompts) == 2


def test_undeclared_save_node_fails_closed(settings, tmp_path):
    settings, root = configured(settings, tmp_path)
    path = root / "basic-image.json"
    data = json.loads(path.read_text())
    data["graph"]["8"] = {
        "class_type": "SaveImage",
        "inputs": {"images": ["6", 0], "filename_prefix": "unscoped"},
    }
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Malformed workflow definition"):
        WorkflowStore(ModelCatalog(settings), settings.workflow_dir, root)


async def test_only_declared_history_node_becomes_asset(settings, tmp_path, fake):
    settings, _ = configured(settings, tmp_path)
    server = create_server(settings, transport=httpx.MockTransport(fake.handle))
    async with Client(server) as client:
        built = await client.call_tool(
            "workflows.build",
            {
                "template": "basic-image",
                "parameters": {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
            },
        )
        submitted = await client.call_tool(
            "jobs.submit", {"workflow_id": built.structured_content["workflow_id"]}
        )
        job_id = submitted.structured_content["job_id"]
        fake.finish()
        fake.history["prompt-1"]["outputs"]["999"] = {
            "images": [{"filename": "unexpected.png", "subfolder": "", "type": "output"}]
        }
        assets = await client.call_tool("assets.list", {"job_id": job_id})
        assert [asset["output_index"] for asset in assets.structured_content["assets"]] == [0]
        assert assets.structured_content["assets"][0]["filename"] == "000.png"

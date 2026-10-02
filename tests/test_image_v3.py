import copy

import pytest
from test_workflow_v2 import definition

from flamoris_generation_mcp.image_v3 import ImageV3
from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.workflow_v3 import Definition
from flamoris_generation_mcp.workflows import WorkflowStore


def leaf():
    native = definition()
    inputs = {}
    for name, p in native["parameters"].items():
        if name in {"sampler", "scheduler"}:
            continue  # Native enum selectors remain pinned constants in this profile.
        inputs[name] = {
            "type": p["type"],
            "schema_id": "image-" + p["role"].replace("_", "-"),
            "schema_revision": 1,
            "role": p["role"],
            "max_bytes": 20000 if p["type"] == "string" else 64,
            "min_count": 1 if p.get("required", True) else 0,
        }
        for source, target in (
            ("default", "default"),
            ("minimum", "minimum"),
            ("maximum", "maximum"),
        ):
            if source in p:
                inputs[name][target] = p[source]
    return {
        "schema_version": 3,
        "id": "image-leaf",
        "version": 1,
        "name": "Image leaf",
        "capability_id": "image.generate",
        "profile": {"id": "image-generate-v1", "revision": 1},
        "inputs": inputs,
        "outputs": {
            "image": {
                "type": "asset",
                "schema_id": "image-v1",
                "schema_revision": 1,
                "role": "primary_image",
                "media_kind": "image",
                "mime_types": ["image/png"],
                "max_bytes": 1048576,
            }
        },
        "budget": {
            "steps": 7,
            "deadline_seconds": 300,
            "output_assets": 1,
            "output_bytes": 1048576,
        },
        "effects": ["external", "write"],
        "execution": {
            "kind": "provider",
            "provider_id": "comfyui",
            "adapter_revision": 1,
            "artifact": {
                "graph": native["graph"],
                "input_bindings": {
                    k: {"node": p["node"], "input": p["input"]}
                    for k, p in native["parameters"].items()
                    if k in inputs
                },
                "output_bindings": {"image": {"node": "7", "index": 0}},
            },
        },
    }


def parent(child, workflow_id="image-parent"):
    raw = copy.deepcopy(child)
    raw.update(id=workflow_id, name="Pinned Image", execution={"kind": "composition"})
    raw["includes"] = [
        {
            "alias": "render",
            "workflow_id": child["id"],
            "version": child["version"],
            "digest": Definition.model_validate(child).digest,
        }
    ]
    raw["bindings"] = [{"from": "inputs." + k, "to": "render.inputs." + k} for k in child["inputs"]]
    raw["bindings"].append({"from": "render.outputs.image", "to": "outputs.image"})
    return raw


@pytest.fixture
def v3(settings, tmp_path):
    store = WorkflowStore(
        ModelCatalog(settings), settings.workflow_dir, tmp_path / "defs", v3_enabled=True
    )
    assert isinstance(store.v3, ImageV3)
    return store


def test_native_lowering_keeps_structural_and_invocation_identity_separate(v3):
    raw = leaf()
    v3.v3.versions.register(raw)
    root = parent(raw)
    v3.v3.versions.register(root)
    pin = Definition.model_validate(root)
    a = v3.build_v3(
        pin.id,
        pin.version,
        pin.digest,
        {"checkpoint": "base.safetensors", "positive_prompt": "one"},
        False,
    )
    b = v3.build_v3(
        pin.id,
        pin.version,
        pin.digest,
        {"checkpoint": "base.safetensors", "positive_prompt": "two", "seed": 4},
        False,
    )
    assert a["structural_digest"] == b["structural_digest"]
    assert a["closure_digest"] == b["closure_digest"]
    assert a["invocation_digest"] != b["invocation_digest"]
    assert a["prompt"]["2"]["inputs"]["text"] == "one"
    assert b["prompt"]["5"]["inputs"]["seed"] == 4
    assert v3.capture(v3.get(a["workflow_id"])).digest == pin.digest


def test_history_restart_and_revocation_do_not_follow_latest(v3):
    raw = leaf()
    v3.v3.versions.register(raw)
    root = parent(raw)
    v3.v3.versions.register(root)
    pin = Definition.model_validate(root)
    values = {"checkpoint": "base.safetensors", "positive_prompt": "saved"}
    built = v3.build_v3(pin.id, pin.version, pin.digest, values, False)
    v3.save(built["workflow_id"])
    newer = copy.deepcopy(raw)
    newer["version"] = 2
    newer["execution"]["artifact"]["graph"]["5"]["inputs"]["steps"] = 12
    v3.v3.versions.register(newer)
    restarted = WorkflowStore(v3.catalog, v3.directory, v3.registry.root, v3_enabled=True)
    old = restarted.capture(restarted.get(built["workflow_id"]))
    assert (
        old.graph["5"]["inputs"]["steps"]
        == raw["execution"]["artifact"]["graph"]["5"]["inputs"]["steps"]
    )
    restarted.v3.versions.revoke(raw["id"], 1, Definition.model_validate(raw).digest)
    with pytest.raises(ValueError, match="revoked"):
        restarted.capture(restarted.get(built["workflow_id"]))


@pytest.mark.parametrize(
    "mode", ["profile", "role", "hidden", "budget", "constant", "output_index", "default"]
)
def test_unsupported_or_unsafe_definition_is_rejected_before_publication(v3, mode):
    raw = leaf()
    if mode == "profile":
        raw["profile"]["id"] = "music-generate-v1"
    elif mode == "role":
        raw["inputs"]["positive_prompt"]["role"] = "sampler"
    elif mode == "hidden":
        raw["execution"]["artifact"]["graph"]["8"] = {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": "hidden"},
        }
    elif mode == "budget":
        raw["budget"]["steps"] = 1
    elif mode == "constant":
        raw["execution"]["artifact"]["graph"]["5"]["inputs"]["steps"] = 100000
    elif mode == "output_index":
        raw["execution"]["artifact"]["output_bindings"]["image"]["index"] = 1
    else:
        raw["inputs"]["width"]["default"] = 65
    with pytest.raises(ValueError):
        v3.v3.versions.register(raw)
    assert not v3.v3.versions.state["versions"]

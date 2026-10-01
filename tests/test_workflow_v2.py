import copy
import json
from pathlib import Path

import pytest

from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.workflow_registry import ParameterSpec, WorkflowDefinition
from flamoris_generation_mcp.workflows import WorkflowStore


def definition(mode="txt2img"):
    data = json.loads(
        (
            Path(__file__).parents[1]
            / "src/flamoris_generation_mcp/example_definitions/basic-image.json"
        ).read_text()
    )
    data.update(
        schema_version=2,
        id="image-v2",
        image={
            "profile": "image-v1",
            "mode": mode,
            "dimensions": {"mode": "parameters"},
        },
    )
    for key, spec in data["parameters"].items():
        spec["role"] = key
    for role in ("width", "height"):
        data["parameters"][role] = {
            "type": "integer",
            "role": role,
            "node": "4",
            "input": role,
            "required": False,
            "default": 512,
            "minimum": 64,
            "maximum": 4096,
            "multiple_of": 8,
        }
    data["parameters"]["seed"] = {
        "type": "integer",
        "role": "seed",
        "node": "5",
        "input": "seed",
        "required": False,
        "default": 0,
        "minimum": 0,
        "maximum": 4294967295,
    }
    if mode == "img2img":
        data["image"].update(
            reference_semantics="initial_image", resize_policy="center-crop-resize"
        )
        data["graph"]["4"] = {
            "class_type": "ImageScale",
            "inputs": {
                "image": ["8", 0],
                "width": 512,
                "height": 512,
                "upscale_method": "lanczos",
                "crop": "center",
            },
        }
        data["graph"]["8"] = {"class_type": "LoadImage", "inputs": {"image": ""}}
        data["graph"]["9"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["4", 0], "vae": ["1", 2]},
        }
        data["graph"]["5"]["inputs"]["latent_image"] = ["9", 0]
        data["parameters"]["source"] = {
            "type": "managed_input",
            "role": "initial_image",
            "node": "8",
            "input": "image",
            "media_types": ["image/png", "image/jpeg", "image/webp"],
        }
        data["parameters"]["denoise"] = {
            "type": "number",
            "role": "denoise",
            "node": "5",
            "input": "denoise",
            "required": False,
            "default": 0.6,
            "minimum": 0,
            "maximum": 1,
        }
    return data


def test_v2_graph_free_metadata_and_canonical_digest():
    raw = definition()
    item = WorkflowDefinition.model_validate(raw)
    other = WorkflowDefinition.model_validate(json.loads(json.dumps(raw, sort_keys=True)))
    assert item.digest == other.digest
    metadata = item.metadata()
    assert "graph" not in metadata
    assert all("node" not in p and "input" not in p for p in metadata["parameters"].values())
    assert metadata["image"]["mode"] == "txt2img"
    changed = copy.deepcopy(raw)
    changed["graph"]["5"]["inputs"]["steps"] = 11
    assert WorkflowDefinition.model_validate(changed).digest != item.digest


@pytest.mark.parametrize("value", [True, 0, -8, 0.5, "8", 8.0])
def test_multiple_of_strict_positive_integer(value):
    with pytest.raises(ValueError):
        ParameterSpec(type="integer", node="1", input="seed", multiple_of=value)


def test_multiple_of_rejects_number_defaults_enums_and_values(settings):
    with pytest.raises(ValueError):
        ParameterSpec(type="number", node="1", input="cfg", multiple_of=8)
    data = definition()
    data["parameters"]["width"]["default"] = 513
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(data)
    data["parameters"]["width"]["default"] = 512
    data["parameters"]["width"]["enum"] = [512, 513]
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(data)
    spec = ParameterSpec(type="integer", node="1", input="seed", multiple_of=8)
    with pytest.raises(ValueError, match="multiple_of"):
        spec.validate_value(3, ModelCatalog(settings))


@pytest.mark.parametrize("field", ["production_ready", "readiness", "attestation"])
def test_definition_cannot_self_attest(field):
    data = definition()
    data[field] = True
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(data)


def test_builtin_descriptor_preserves_ordered_lora_contract(settings):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    descriptors = store.list()["descriptors"]
    assert descriptors[0]["readiness"]["basis"] == "builtin_compatibility"
    assert descriptors[0]["parameters"]["loras"]["max_items"] == 0
    assert descriptors[1]["parameters"]["loras"]["min_items"] == 1
    assert descriptors[1]["parameters"]["loras"]["max_items"] == 16
    assert descriptors[1]["parameters"]["width"]["multiple_of"] == 8


@pytest.mark.parametrize("mode", ["txt2img", "img2img"])
def test_effective_size_seed_and_arbitrary_public_keys(settings, tmp_path, mode):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, tmp_path / "definitions")
    data = definition(mode)
    data["parameters"]["horizontal"] = data["parameters"].pop("width")
    store.register_definition(data)
    values = {
        "checkpoint": "base.safetensors",
        "positive_prompt": "flowers",
        "horizontal": 320,
        "height": 384,
        "seed": 123,
    }
    if mode == "img2img":
        values["source"] = "a" * 32
    graph = store.build(data["id"], values)["prompt"]
    assert graph["4"]["inputs"]["width"] == 320
    assert graph["4"]["inputs"]["height"] == 384
    assert graph["5"]["inputs"]["seed"] == 123


@pytest.mark.parametrize("role", ["width", "seed", "steps", "source"])
def test_disconnected_or_wrong_semantic_role_rejected(role):
    raw = definition("img2img")
    spec = raw["parameters"][role]
    raw["graph"]["99"] = copy.deepcopy(raw["graph"][spec["node"]])
    spec["node"] = "99"
    with pytest.raises(ValueError, match="semantic input"):
        WorkflowDefinition.model_validate(raw)


@pytest.mark.parametrize("tamper", ["cycle", "index", "source", "crop", "batch", "seed"])
def test_unsupported_reference_topology_rejected(tamper):
    raw = definition("img2img")
    if tamper == "cycle":
        raw["graph"]["4"]["inputs"]["image"] = ["4", 0]
    elif tamper == "index":
        raw["graph"]["4"]["inputs"]["image"] = ["8", 1]
    elif tamper == "source":
        raw["graph"]["9"]["inputs"]["pixels"] = ["8", 0]
    elif tamper == "crop":
        raw["graph"]["4"]["inputs"]["crop"] = "disabled"
    elif tamper == "batch":
        raw = definition()
        raw["graph"]["4"]["inputs"]["batch_size"] = 2
    else:
        raw["parameters"]["seed"]["input"] = "steps"
        del raw["parameters"]["steps"]
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(raw)


@pytest.mark.parametrize("tamper", ["width", "steps", "batch"])
def test_smoke_bounds_hidden_effective_literals(tamper):
    from flamoris_generation_mcp.image_profile import smoke_budget

    raw = definition()
    if tamper == "width":
        raw["image"]["dimensions"] = {"mode": "fixed", "width": 1024, "height": 512}
        del raw["parameters"]["width"]
        del raw["parameters"]["height"]
        raw["graph"]["4"]["inputs"]["width"] = 1024
    elif tamper == "steps":
        del raw["parameters"]["steps"]
        raw["graph"]["5"]["inputs"]["steps"] = 100
    else:
        raw["graph"]["4"]["inputs"]["batch_size"] = 2
    with pytest.raises(ValueError):
        item = WorkflowDefinition.model_validate(raw)
        smoke_budget(item, item.graph)


@pytest.mark.parametrize("mode", ["txt2img", "img2img"])
@pytest.mark.parametrize("axis", ["width", "height"])
def test_fixed_size_cannot_be_changed_through_roleless_parameters(mode, axis):
    raw = definition(mode)
    raw["image"]["dimensions"] = {"mode": "fixed", "width": 512, "height": 512}
    spec = raw["parameters"].pop(axis)
    raw["parameters"].pop("height" if axis == "width" else "width")
    spec.pop("role")
    raw["parameters"]["advanced_size"] = spec
    with pytest.raises(ValueError, match="Dimension bindings"):
        WorkflowDefinition.model_validate(raw)


def test_materialized_dimensions_are_rechecked_and_production_size_is_not_smoke_size(
    settings, tmp_path
):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, tmp_path / "defs")
    store.register_definition(definition())
    values = {"checkpoint": "base.safetensors", "positive_prompt": "x", "width": 4096}
    assert store.build("image-v2", values)["prompt"]["4"]["inputs"]["width"] == 4096
    # Defense in depth if an internal caller supplies a modified captured Definition.
    item = store.registry.get("image-v2").model_copy(deep=True)
    item.parameters["width"].maximum = None
    with pytest.raises(ValueError, match="Effective Image dimensions"):
        store.registry.materialize_definition(item, {**values, "width": 10**9})


def test_discovery_byte_limit_includes_legacy_aliases_and_unicode(settings, tmp_path, monkeypatch):
    import flamoris_generation_mcp.workflows as workflows

    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, tmp_path / "defs")
    for index in range(8):
        raw = definition()
        raw["id"] = f"large-{index}"
        raw["parameters"]["positive_prompt"].update(required=False, default="花" * 10000)
        store.register_definition(raw)
    with pytest.raises(ValueError, match="discovery exceeds byte limit"):
        store.list()
    store.registry.definitions.clear()
    payload = store.list()
    size = len(json.dumps(payload, indent=2).encode("utf-8"))
    monkeypatch.setattr(workflows, "MAX_DISCOVERY_BYTES", size)
    assert store.list() == payload
    monkeypatch.setattr(workflows, "MAX_DISCOVERY_BYTES", size - 1)
    with pytest.raises(ValueError, match="byte limit"):
        store.list()


def test_discovery_entry_limit_applies_to_loaded_definitions(settings, tmp_path):
    store = WorkflowStore(ModelCatalog(settings), settings.workflow_dir, tmp_path / "defs")
    item = WorkflowDefinition.model_validate(definition())
    store.registry.definitions.update({f"loaded-{i}": item for i in range(127)})
    with pytest.raises(ValueError, match="entry limit"):
        store.list()

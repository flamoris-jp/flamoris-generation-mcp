from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from flamoris_generation_mcp.capabilities import Capability, CapabilityRegistry


def test_capability_identity_is_independent_from_provider_and_runtime():
    registry = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=("text-to-image",),
            ),
        )
    )

    capability = registry.get("image.generate", {"comfyui": True})

    assert capability["id"] == "image.generate"
    assert capability["provider_id"] == "comfyui"
    assert capability["runtime_id"] == "janku"
    assert capability["available"] is True
    assert registry.resolve_workflow("text-to-image").capability_id == "image.generate"


def test_capability_availability_defaults_false_and_unknown_is_rejected():
    registry = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=("text-to-image",),
            ),
        )
    )

    assert registry.list({})[0]["available"] is False
    with pytest.raises(ValueError, match="Unknown capability"):
        registry.get("music.generate", {})


@pytest.mark.parametrize("capability_id", ["image", "Image.generate", "image/generate"])
def test_invalid_capability_identity_is_rejected(capability_id):
    with pytest.raises(ValueError, match="Capability ID"):
        CapabilityRegistry(
            (
                Capability(
                    capability_id=capability_id,
                    provider_id="comfyui",
                    runtime_id="janku",
                    workflow_templates=("text-to-image",),
                ),
            )
        )


def test_workflow_template_cannot_route_to_multiple_capabilities():
    with pytest.raises(ValueError, match="already assigned"):
        CapabilityRegistry(
            (
                Capability(
                    capability_id="image.generate",
                    provider_id="comfyui",
                    runtime_id="janku",
                    workflow_templates=("text-to-image",),
                ),
                Capability(
                    capability_id="image.alternate",
                    provider_id="alternate",
                    runtime_id="alternate",
                    workflow_templates=("text-to-image",),
                ),
            )
        )


def test_parallel_workflow_assignment_preserves_every_template():
    registry = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=("text-to-image",),
            ),
        )
    )
    count = 24
    start = Barrier(count)

    def assign(index):
        start.wait()
        template = f"runtime-{index}"
        registry.assign_workflow("image.generate", "comfyui", template)
        return template

    with ThreadPoolExecutor(max_workers=count) as pool:
        templates = set(pool.map(assign, range(count)))

    expected = {"text-to-image", *templates}
    assert set(registry.get("image.generate", {})["workflow_templates"]) == expected
    assert set(registry.list({})[0]["workflow_templates"]) == expected
    for template in templates:
        assert registry.resolve_workflow(template).capability_id == "image.generate"

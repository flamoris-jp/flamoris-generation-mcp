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

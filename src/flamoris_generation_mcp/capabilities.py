"""Provider-independent capability discovery for the Generation Hub."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    capability_id: str
    provider_id: str
    runtime_id: str
    workflow_templates: tuple[str, ...]

    def as_dict(self, *, available: bool) -> dict[str, object]:
        return {
            "id": self.capability_id,
            "provider_id": self.provider_id,
            "runtime_id": self.runtime_id,
            "workflow_templates": list(self.workflow_templates),
            "available": available,
        }


class CapabilityRegistry:
    def __init__(self, capabilities: tuple[Capability, ...] = ()):
        self._capabilities: dict[str, Capability] = {}
        for capability in capabilities:
            self.register(capability)

    def register(self, capability: Capability) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+", capability.capability_id):
            raise ValueError("Capability ID must be a stable dotted lowercase identifier")
        if capability.capability_id in self._capabilities:
            raise ValueError(f"Duplicate capability ID: {capability.capability_id}")
        self._capabilities[capability.capability_id] = capability

    def list(self, availability: dict[str, bool]) -> list[dict[str, object]]:
        return [
            capability.as_dict(available=availability.get(capability.provider_id, False))
            for capability in self._capabilities.values()
        ]

    def get(self, capability_id: str, availability: dict[str, bool]) -> dict[str, object]:
        try:
            capability = self._capabilities[capability_id]
        except KeyError:
            raise ValueError(f"Unknown capability ID: {capability_id}") from None
        return capability.as_dict(available=availability.get(capability.provider_id, False))

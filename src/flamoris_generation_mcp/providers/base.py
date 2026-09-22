"""Small provider-neutral contracts used by the process-owned Hub authority."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

JobStatus = Literal[
    "submitting",
    "queued",
    "running",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
    "unknown",
]


class ProviderError(RuntimeError):
    """A bounded provider failure safe to surface as an MCP tool error."""


@dataclass(frozen=True)
class ProviderHealth:
    available: bool
    details: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"available": self.available, **self.details}


@dataclass(frozen=True)
class GenerationRequest:
    """Validated Hub request passed to the explicitly selected provider."""

    operation: str
    workflow_id: str
    payload: Any


@dataclass(frozen=True)
class ProviderJob:
    execution_id: str


@dataclass(frozen=True)
class ProviderOutput:
    output_id: str
    filename: str
    media_kind: str
    mime_type: str

    def as_dict(self) -> dict[str, str]:
        return {
            "output_id": self.output_id,
            "filename": self.filename,
            "media_kind": self.media_kind,
            "mime_type": self.mime_type,
        }


@dataclass(frozen=True)
class JobSnapshot:
    status: JobStatus
    error: Mapping[str, object] | None = None
    outputs: tuple[ProviderOutput, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "error": dict(self.error) if self.error is not None else None,
            "outputs": [output.as_dict() for output in self.outputs],
            **self.metadata,
        }


@runtime_checkable
class GenerationProvider(Protocol):
    provider_id: str

    async def health(self) -> ProviderHealth: ...

    async def submit(self, request: GenerationRequest, job_id: str) -> ProviderJob: ...

    async def inspect(self, execution_id: str) -> JobSnapshot: ...

    async def cancel(self, execution_id: str) -> JobSnapshot: ...

    async def materialize(self, execution_id: str, output_id: str) -> bytes: ...

    async def close(self) -> None: ...

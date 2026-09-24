"""ComfyUI implementation of the provider-neutral generation contract."""

from pathlib import Path

from ..comfyui import ComfyUIClient
from ..models import ModelCatalog
from ..workflows import ExternalRecipe, Recipe, WorkflowStore, build_prompt
from .base import (
    GenerationRequest,
    JobSnapshot,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
)

MEDIA_TYPES = {
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
}
STATUSES = {
    "queued",
    "running",
    "completed",
    "failed",
    "cancel_requested",
    "cancelled",
    "unknown",
}


class ComfyUIProvider:
    provider_id = "comfyui"

    def __init__(
        self, client: ComfyUIClient, catalog: ModelCatalog, workflows: WorkflowStore | None = None
    ):
        self.client = client
        self.catalog = catalog
        self.workflows = workflows
        self._outputs: dict[tuple[str, str], dict] = {}

    async def health(self) -> ProviderHealth:
        raw = await self.client.health()
        available = raw.get("available") is True
        return ProviderHealth(
            available=available,
            details={key: value for key, value in raw.items() if key != "available"},
        )

    async def submit(self, request: GenerationRequest, job_id: str) -> ProviderJob:
        if request.operation != "image.generate" or not isinstance(
            request.payload, (Recipe, ExternalRecipe)
        ):
            raise ProviderError("ComfyUI does not support the requested operation")
        if self.workflows is not None:
            prompt = self.workflows.prompt(request.payload, job_id)
        elif isinstance(request.payload, Recipe):
            prompt = build_prompt(request.payload, self.catalog)
            prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        else:
            raise ProviderError("Workflow definitions are not configured")
        execution_id = await self.client.submit(prompt, job_id)
        return ProviderJob(execution_id=execution_id)

    def _normalize(self, execution_id: str, raw: dict) -> JobSnapshot:
        status = raw.get("status")
        if status not in STATUSES:
            raise ProviderError("ComfyUI returned an invalid normalized execution state")

        outputs = []
        for index, item in enumerate(raw.get("outputs", [])):
            try:
                filename = item["filename"]
                suffix = Path(filename).suffix.lower()
                media_kind, mime_type = MEDIA_TYPES[suffix]
            except (KeyError, TypeError, ValueError):
                raise ProviderError("ComfyUI returned an unsupported output") from None
            output_id = f"{index:03d}"
            outputs.append(
                ProviderOutput(
                    output_id=output_id,
                    filename=filename,
                    media_kind=media_kind,
                    mime_type=mime_type,
                )
            )
            self._outputs[(execution_id, output_id)] = dict(item)

        error = raw.get("error")
        if error is not None and not isinstance(error, dict):
            raise ProviderError("ComfyUI returned an invalid normalized error")
        metadata = {key: raw[key] for key in ("cancel_supported", "reason") if key in raw}
        return JobSnapshot(
            status=status,
            error=error,
            outputs=tuple(outputs),
            metadata=metadata,
        )

    async def inspect(self, execution_id: str) -> JobSnapshot:
        return self._normalize(execution_id, await self.client.inspect(execution_id))

    async def cancel(self, execution_id: str) -> JobSnapshot:
        return self._normalize(execution_id, await self.client.cancel(execution_id))

    async def materialize(self, execution_id: str, output_id: str) -> bytes:
        try:
            output = self._outputs[(execution_id, output_id)]
        except KeyError:
            raise ProviderError(
                "Unknown ComfyUI output; inspect the job before retrieval"
            ) from None
        return await self.client.download(output)

    async def close(self) -> None:
        await self.client.close()

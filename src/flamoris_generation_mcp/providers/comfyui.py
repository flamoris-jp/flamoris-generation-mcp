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
from .comfyui_retention import ComfyUIRetention

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
        self,
        client: ComfyUIClient,
        catalog: ModelCatalog,
        workflows: WorkflowStore | None = None,
        managed_inputs=None,
    ):
        self.client = client
        self.catalog = catalog
        self.workflows = workflows
        self.managed_inputs = managed_inputs
        self._outputs: dict[tuple[str, str], dict] = {}
        self._output_nodes: dict[str, str] = {}

    def retention(self) -> ComfyUIRetention | None:
        root = self.client.settings.comfyui_output_root
        return ComfyUIRetention(root, self._outputs) if root is not None else None

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
        managed_inputs = {}
        if self.workflows is not None:
            bindings = self.workflows.managed_input_bindings(request.payload)
            if bindings:
                if self.managed_inputs is None:
                    raise ProviderError("Managed workflow inputs are not configured")
                refs = {}
                allowed = {}
                for name, spec in bindings.items():
                    try:
                        refs[name] = request.payload.parameters[name]
                    except (KeyError, TypeError):
                        raise ProviderError("Managed workflow input is missing") from None
                    allowed[name] = set(spec.media_types or ())
                provider_inputs = {}
                async with self.managed_inputs.stage(job_id, refs, allowed) as readers:
                    extensions = {
                        "image/png": ".png",
                        "image/jpeg": ".jpg",
                        "image/webp": ".webp",
                    }
                    for index, (name, reader) in enumerate(readers.items()):
                        mime_type = reader.metadata["mime_type"]
                        extension = extensions.get(mime_type)
                        if extension is None:
                            raise ProviderError("Unsupported ComfyUI managed input media type")
                        content = bytearray()
                        async for chunk in reader.chunks():
                            content.extend(chunk)
                        provider_inputs[name] = await self.client.upload_input(
                            bytes(content),
                            mime_type,
                            f"flamoris-{job_id}-{index:02d}{extension}",
                        )
                        managed_inputs[name] = {
                            key: reader.metadata[key]
                            for key in (
                                "input_id",
                                "source_asset_id",
                                "sha256",
                                "mime_type",
                                "size_bytes",
                            )
                        }
                    prompt = self.workflows.prompt(
                        request.payload, job_id, provider_inputs=provider_inputs
                    )
                    execution_id = await self.client.submit(prompt, job_id)
            else:
                prompt = self.workflows.prompt(request.payload, job_id)
                execution_id = await self.client.submit(prompt, job_id)
        elif isinstance(request.payload, Recipe):
            prompt = build_prompt(request.payload, self.catalog)
            prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
            execution_id = await self.client.submit(prompt, job_id)
        else:
            raise ProviderError("Workflow definitions are not configured")
        if isinstance(request.payload, ExternalRecipe):
            self._output_nodes[execution_id] = self.workflows.registry.get(
                request.payload.template, request.payload.definition_version
            ).output_node
        else:
            self._output_nodes[execution_id] = "7"
        return ProviderJob(execution_id=execution_id, managed_inputs=managed_inputs)

    def _normalize(self, execution_id: str, raw: dict) -> JobSnapshot:
        status = raw.get("status")
        if status not in STATUSES:
            raise ProviderError("ComfyUI returned an invalid normalized execution state")

        outputs = []
        declared_node = self._output_nodes.get(execution_id)
        if declared_node is None:
            raise ProviderError("Unknown ComfyUI execution; inspect only submitted jobs")
        selected = [item for item in raw.get("outputs", []) if item.get("node_id") == declared_node]
        if status == "completed" and not selected:
            raise ProviderError("ComfyUI returned no declared workflow output")
        for index, item in enumerate(selected):
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

    async def stream_output(self, execution_id: str, output_id: str):
        from contextlib import aclosing

        output = self._outputs.get((execution_id, output_id))
        if output is None:
            raise ProviderError("Unknown ComfyUI output; inspect before retrieval")
        async with aclosing(self.client.stream_output(output)) as stream:
            async for chunk in stream:
                yield chunk

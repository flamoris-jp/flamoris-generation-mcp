"""ComfyUI implementation of the provider-neutral generation contract."""

import asyncio
from contextlib import nullcontext
from pathlib import Path

from ..comfyui import ComfyUIClient
from ..image_decode import decode_image
from ..image_profile import image_topology
from ..models import ModelCatalog
from ..workflows import ExternalRecipe, Recipe, WorkflowStore, build_prompt
from .base import (
    GenerationRequest,
    JobSnapshot,
    OutputRole,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
    SubmissionRejected,
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
        self._output_contracts = {}

    def capture_output_contract(self, execution_id, definition):
        if hasattr(definition, "v3_identity"):
            dimensions = image_topology(definition)["dimensions"]
            self._output_contracts[execution_id] = (
                definition.output_port,
                definition.output_contract,
                {k: definition.graph[dimensions]["inputs"][k] for k in ("width", "height")},
            )

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
            raise SubmissionRejected("ComfyUI does not support the requested operation")
        definition = request.definition
        posted = False
        managed_inputs = {}
        try:
            if isinstance(request.payload, ExternalRecipe):
                if self.workflows is None:
                    raise ValueError("Workflow definitions are not configured")
                definition = definition or self.workflows.capture(request.payload)
            guard = nullcontext()
            if self.workflows is not None and self.workflows.readiness is not None:
                guard = self.workflows.readiness.execution_guard(
                    request.payload, definition, request.runtime_evidence
                )
            with guard:
                if definition is not None:
                    bindings = {
                        name: spec
                        for name, spec in definition.parameters.items()
                        if spec.type == "managed_input"
                    }
                else:
                    bindings = {}
                if bindings:
                    if self.managed_inputs is None:
                        raise ValueError("Managed workflow inputs are not configured")
                    refs = {name: request.payload.parameters[name] for name in bindings}
                    allowed = {name: set(spec.media_types) for name, spec in bindings.items()}
                    provider_inputs = {}
                    async with self.managed_inputs.stage(job_id, refs, allowed) as readers:
                        extensions = {
                            "image/png": ".png",
                            "image/jpeg": ".jpg",
                            "image/webp": ".webp",
                        }
                        for index, (name, reader) in enumerate(readers.items()):
                            mime_type = reader.metadata["mime_type"]
                            content = bytearray()
                            async for chunk in reader.chunks():
                                content.extend(chunk)
                            await asyncio.to_thread(decode_image, bytes(content), mime_type)
                            provider_inputs[name] = await self.client.upload_input(
                                bytes(content),
                                mime_type,
                                f"flamoris-{job_id}-{index:02d}{extensions[mime_type]}",
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
                            if reader.metadata.get("source_kind") == "upload":
                                managed_inputs[name]["source_kind"] = "upload"
                        self.workflows.capture(request.payload)
                        prompt = self.workflows.prompt(
                            request.payload, job_id, provider_inputs, definition
                        )
                        if self.workflows.readiness is not None:
                            self.workflows.readiness.before_post(
                                request.payload, definition, request.runtime_evidence
                            )
                        posted = True
                        execution_id = await self.client.submit(prompt, job_id)
                else:
                    if self.workflows is not None:
                        self.workflows.capture(request.payload)
                        prompt = self.workflows.prompt(
                            request.payload, job_id, definition=definition
                        )
                        if self.workflows.readiness is not None:
                            self.workflows.readiness.before_post(
                                request.payload, definition, request.runtime_evidence
                            )
                    else:
                        prompt = build_prompt(request.payload, self.catalog)
                        prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
                    posted = True
                    execution_id = await self.client.submit(prompt, job_id)
        except BaseException as exc:
            if not posted:
                detail = (
                    str(exc)[:300]
                    if isinstance(exc, ValueError)
                    else "Workflow rejected before generation submission"
                )
                raise SubmissionRejected(detail) from exc
            raise
        # The accepted execution always retains the graph's captured output identity.
        self._output_nodes[execution_id] = definition.output_node if definition is not None else "7"
        self.capture_output_contract(execution_id, definition)
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
        contract = self._output_contracts.get(execution_id)
        if (
            contract is not None
            and status == "completed"
            and (len(selected) != 1 or len(raw.get("outputs", [])) != 1)
        ):
            # Provider completion is observed, but cannot satisfy the declared result.
            return JobSnapshot(status="failed", error={"code": "output_contract"})
        if status == "completed" and not selected:
            raise ProviderError("ComfyUI returned no declared workflow output")
        if contract is not None and status != "completed":
            selected = []  # Partial provider outputs are never public v3 results.
        for index, item in enumerate(selected):
            try:
                filename = item["filename"]
                suffix = Path(filename).suffix.lower()
                media_kind, mime_type = MEDIA_TYPES[suffix]
            except (KeyError, TypeError, ValueError):
                if contract is not None:
                    return JobSnapshot(status="failed", error={"code": "output_contract"})
                raise ProviderError("ComfyUI returned an unsupported output") from None
            if contract is not None and mime_type not in contract[1].mime_types:
                return JobSnapshot(status="failed", error={"code": "output_contract"})
            output_id = f"{index:03d}"
            outputs.append(
                ProviderOutput(
                    output_id=output_id,
                    filename=filename,
                    media_kind=media_kind,
                    mime_type=mime_type,
                    role=OutputRole(contract[0], contract[1].role) if contract else None,
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

    @staticmethod
    def _owned_observation(raw):
        """Status only; old identities never restore output publication authority."""
        status = raw.get("status")
        supported = raw.get("cancel_supported")
        if status not in STATUSES or (supported is not None and type(supported) is not bool):
            raise ProviderError("ComfyUI returned an invalid owned execution observation")
        return JobSnapshot(
            status=status,
            metadata={"cancel_supported": supported} if supported is not None else {},
        )

    async def inspect_owned(self, execution_id: str) -> JobSnapshot:
        return self._owned_observation(await self.client.inspect(execution_id))

    async def cancel_owned(self, execution_id: str) -> JobSnapshot:
        return self._owned_observation(await self.client.cancel(execution_id))

    async def materialize(self, execution_id: str, output_id: str) -> bytes:
        try:
            output = self._outputs[(execution_id, output_id)]
        except KeyError:
            raise ProviderError(
                "Unknown ComfyUI output; inspect the job before retrieval"
            ) from None
        data = await self.client.download(output)
        contract = self._output_contracts.get(execution_id)
        if contract is not None and len(data) > contract[1].max_bytes:
            raise ProviderError("Image output exceeds declared byte limit")
        if contract is not None:
            await self._validate_image(data, output, contract)
        return data

    @staticmethod
    async def _validate_image(data, output, contract):
        mime_type = MEDIA_TYPES[Path(output["filename"]).suffix.lower()][1]
        dimensions = await asyncio.to_thread(decode_image, data, mime_type)
        if dimensions != contract[2]:
            raise ProviderError("Image output differs from declared dimensions")

    async def close(self) -> None:
        await self.client.close()

    async def stream_output(self, execution_id: str, output_id: str):
        from contextlib import aclosing

        output = self._outputs.get((execution_id, output_id))
        if output is None:
            raise ProviderError("Unknown ComfyUI output; inspect before retrieval")
        async with aclosing(self.client.stream_output(output)) as stream:
            size = 0
            contract = self._output_contracts.get(execution_id)
            data = bytearray() if contract is not None else None
            async for chunk in stream:
                size += len(chunk)
                if contract is not None and size > contract[1].max_bytes:
                    raise ProviderError("Image output exceeds declared byte limit")
                if data is not None:
                    data.extend(chunk)
                yield chunk
            if data is not None:
                # Transfer destinations commit only after the stream closes successfully.
                await self._validate_image(bytes(data), output, contract)

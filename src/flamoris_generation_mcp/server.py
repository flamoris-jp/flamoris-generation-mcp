"""Typed MCP tools served over stdio or HTTP, with one process-owned authority."""

import argparse
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from flamoris_generation_controller.config import Settings as ControllerSettings
from flamoris_generation_controller.contracts import API_PATH, CallerContext, ControllerError
from flamoris_generation_controller.http_api import HTTPAPI
from flamoris_generation_controller.jobs import GenerationBusyError
from flamoris_generation_controller.providers import ProviderError
from flamoris_generation_controller.runtime import GenerationController
from flamoris_generation_controller.transfers import CHUNK_BYTES
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, StrictInt
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import ModelKind, Settings
from .provenance import ProvenanceIngress, current_provenance


def create_server(
    settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> MCPServer:
    settings = settings or Settings.from_env()
    domain_settings = ControllerSettings.model_validate(
        {name: getattr(settings, name) for name in ControllerSettings.model_fields}
    )
    controller = GenerationController(domain_settings, transport=transport)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield None
        finally:
            await controller.close()

    server = MCPServer(
        "FLAMORIS Generation",
        version=__version__,
        lifespan=lifespan,
        middleware=[ProvenanceIngress(settings)],
    )
    server.controller = controller
    token = settings.controller_token.get_secret_value() if settings.controller_token else None
    api = HTTPAPI(controller, token)

    @server.custom_route(API_PATH + "/{operation}", methods=["POST"])
    async def internal(request: Request):
        return await api(request)

    @server.custom_route("/healthz", methods=["GET"])
    async def live(_request: Request) -> JSONResponse:
        return JSONResponse({"healthy": True})

    @server.tool(
        name="system.health",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def health() -> dict[str, Any]:
        """Check this process and provider connectivity/queue availability."""
        try:
            result = await controller.invoke(
                "system.health", {}, context=CallerContext.external(current_provenance())
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="capabilities.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def list_capabilities() -> dict[str, Any]:
        """List provider-independent operations and current availability."""
        try:
            result = await controller.invoke(
                "capabilities.list", {}, context=CallerContext.external(current_provenance())
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="capabilities.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def get_capability(capability_id: str) -> dict[str, Any]:
        """Inspect one capability ID independently from provider transport details."""
        try:
            result = await controller.invoke(
                "capabilities.get",
                {"capability_id": capability_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="models.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def list_models(kind: ModelKind | None = None) -> dict[str, Any]:
        """Scan installed model files, optionally filtered by kind."""
        try:
            result = await controller.invoke(
                "models.list", {"kind": kind}, context=CallerContext.external(current_provenance())
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="models.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def get_model(model_id: str) -> dict[str, Any]:
        """Inspect one kind:relative_filename ID from models.list."""
        try:
            result = await controller.invoke(
                "models.get",
                {"model_id": model_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="workflows.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def list_workflows() -> dict[str, Any]:
        """List known templates and workflow IDs (including saved recipes)."""
        try:
            result = await controller.invoke(
                "workflows.list", {}, context=CallerContext.external(current_provenance())
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="comfy.register",
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    async def register_comfy(name: str, graph: dict[str, Any]) -> dict[str, Any]:
        """Register an immutable bounded checkpoint ComfyWorkFlow API graph.

        Supports txt2img and init-image img2img core profiles. Use the documented
        $reference_image slot; supply its managed input ID to workflows.build.
        Registration is static validation, not proof of live GPU/model readiness.
        """
        try:
            return await controller.invoke("comfy.register", {"name": name, "graph": graph},
                                           context=CallerContext.external(current_provenance()))
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="comfy.get",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    async def get_comfy(definition_id: str) -> dict[str, Any]:
        """Get a registered ComfyWorkFlow descriptor and immutable content digest."""
        try:
            return await controller.invoke("comfy.get", {"definition_id": definition_id},
                                           context=CallerContext.external(current_provenance()))
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="workflows.build",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def build_workflow(
        template: str,
        parameters: dict[str, Any],
        definition_version: int | None = None,
        definition_digest: str | None = None,
        require_ready: bool = False,
    ) -> dict[str, Any]:
        """Build a known template with validated parameters; returns a workflow_id."""
        try:
            result = await controller.invoke(
                "workflows.build",
                {
                    "template": template,
                    "parameters": parameters,
                    "definition_version": definition_version,
                    "definition_digest": definition_digest,
                    "require_ready": require_ready,
                },
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="workflows.save",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def save_workflow(workflow_id: str) -> dict[str, Any]:
        """Persist the parameter recipe for reuse by workflow_id after restart."""
        try:
            result = await controller.invoke(
                "workflows.save",
                {"workflow_id": workflow_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="jobs.submit",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def submit_job(workflow_id: str) -> dict[str, Any]:
        """Submit one workflow when this process has no active generation."""
        try:
            result = await controller.invoke(
                "jobs.submit",
                {"workflow_id": workflow_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="jobs.status",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def job_status(job_id: str) -> dict[str, Any]:
        """Poll execution status for a job submitted by this process."""
        try:
            result = await controller.invoke(
                "jobs.status",
                {"job_id": job_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="jobs.result",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def job_result(job_id: str) -> dict[str, Any]:
        """Return reproducibility metadata; download completed outputs to configured storage."""
        try:
            result = await controller.invoke(
                "jobs.result",
                {"job_id": job_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="jobs.cancel",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel queued work; targeted running interruption requires configured support."""
        try:
            result = await controller.invoke(
                "jobs.cancel",
                {"job_id": job_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="assets.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def list_assets(job_id: str) -> dict[str, Any]:
        """List generated media assets belonging to one completed generation job."""
        try:
            result = await controller.invoke(
                "assets.list",
                {"job_id": job_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="assets.delete",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def delete_asset(asset_id: str) -> dict[str, Any]:
        """Delete one Hub-managed generated asset (not provider originals)."""
        try:
            result = await controller.invoke(
                "assets.delete",
                {"asset_id": asset_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="assets.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def get_asset(asset_id: str) -> Image:
        """Return one generated image asset as MCP-native binary media content."""
        try:
            result = await controller.invoke(
                "assets.get",
                {"asset_id": asset_id},
                context=CallerContext.external(current_provenance()),
            )
            return Image(data=result.data, format=result.format)
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="assets.prepare",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def prepare_asset(asset_id: str) -> dict[str, Any]:
        """Materialize one asset for bounded transfer; return immutable content digest."""
        try:
            result = await controller.invoke(
                "assets.prepare",
                {"asset_id": asset_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="assets.read",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def read_asset(
        asset_id: str, sha256: str, offset: int, length: int = CHUNK_BYTES
    ) -> dict[str, Any]:
        """Read at most 256 KiB from a prepared asset at a retryable byte offset."""
        try:
            result = await controller.invoke(
                "assets.read",
                {"asset_id": asset_id, "sha256": sha256, "offset": offset, "length": length},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="inputs.create",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def create_input(asset_id: str) -> dict[str, Any]:
        """Snapshot a generated image/audio asset as an immutable expiring input."""
        try:
            result = await controller.invoke(
                "inputs.create",
                {"asset_id": asset_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="inputs.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def get_input(input_id: str) -> dict[str, Any]:
        """Inspect managed input metadata; an ID is not a Studio ownership grant."""
        try:
            result = await controller.invoke(
                "inputs.get",
                {"input_id": input_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="inputs.delete",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def delete_input(input_id: str) -> dict[str, Any]:
        """Delete a managed snapshot unless a provider adapter is using it."""
        try:
            result = await controller.invoke(
                "inputs.delete",
                {"input_id": input_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        except (OSError, TimeoutError, ControllerError):
            raise ToolError("Generation operation unavailable") from None

    @server.tool(
        name="inputs.upload.begin",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def begin_input_upload(
        upload_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$", max_length=32)],
        mime_type: Literal["image/png", "image/jpeg", "image/webp"],
        size_bytes: Annotated[StrictInt, Field(ge=1, le=8 * 1024**2)],
        sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", max_length=64)],
    ) -> dict[str, Any]:
        """Reserve an 8 MiB image upload under a pre-recorded private UUID."""
        try:
            result = await controller.invoke(
                "inputs.upload.begin",
                {
                    "upload_id": upload_id,
                    "mime_type": mime_type,
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                },
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (ValueError, OSError, TimeoutError, ControllerError):
            raise ToolError("Image upload reservation unavailable or invalid") from None

    @server.tool(
        name="inputs.upload.write",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def write_input_upload(
        upload_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$", max_length=32)],
        offset: Annotated[StrictInt, Field(ge=0, lt=8 * 1024**2)],
        # The handler enforces this bound with a constant error: SDK validation
        # errors otherwise include a snippet of the private base64 argument.
        data_base64: Annotated[str, Field(json_schema_extra={"minLength": 1, "maxLength": 349528})],
        chunk_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$", max_length=64)],
    ) -> dict[str, Any]:
        """Write one ordered, digest-checked image chunk of at most 256 KiB."""
        try:
            result = await controller.invoke(
                "inputs.upload.write",
                {
                    "upload_id": upload_id,
                    "offset": offset,
                    "data_base64": data_base64,
                    "chunk_sha256": chunk_sha256,
                },
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (ValueError, OSError, TimeoutError, ControllerError):
            raise ToolError("Image upload chunk unavailable or invalid") from None

    @server.tool(
        name="inputs.upload.finish",
        annotations=ToolAnnotations(
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def finish_input_upload(
        upload_id: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$", max_length=32)],
    ) -> dict[str, Any]:
        """Decode and atomically publish one immutable expiring uploaded image."""
        try:
            result = await controller.invoke(
                "inputs.upload.finish",
                {"upload_id": upload_id},
                context=CallerContext.external(current_provenance()),
            )
            return result
        except (ValueError, OSError, TimeoutError, ControllerError):
            raise ToolError("Image upload publication unavailable or invalid") from None

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="FLAMORIS generation MCP server")
    parser.add_argument("--transport", dest="mcp_transport", choices=("stdio", "streamable-http"))
    parser.add_argument("--host", dest="http_host", help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port", dest="http_port", type=int, help="HTTP port (default: 8765)")
    parser.add_argument("--mcp-path", help="HTTP MCP path (default: /mcp)")
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env(**vars(args))
    except ValueError as exc:
        parser.error(str(exc))

    # Construct tools, stores and provider once, before selecting the transport.
    server = create_server(settings)
    if settings.mcp_transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host=settings.http_host,
            port=settings.http_port,
            streamable_http_path=settings.mcp_path,
        )


if __name__ == "__main__":
    main()

"""Typed MCP tools served over stdio or HTTP, with one process-owned authority."""

import argparse
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .capabilities import Capability, CapabilityRegistry
from .comfyui import ComfyUIClient
from .config import ModelKind, Settings
from .jobs import GenerationBusyError, JobStore
from .models import ModelCatalog
from .providers import ProviderError, ProviderRegistry
from .providers.comfyui import ComfyUIProvider
from .workflows import WorkflowStore


def create_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MCPServer:
    settings = settings or Settings.from_env()
    catalog = ModelCatalog(settings)
    workflows = WorkflowStore(catalog, settings.workflow_dir, settings.workflow_definition_dir)
    client = ComfyUIClient(settings, transport)
    providers = ProviderRegistry((ComfyUIProvider(client, catalog, workflows),))
    capabilities = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=(
                    "text-to-image", "text-to-image-lora",
                    *(workflows.registry.definitions if workflows.registry else ()),
                ),
            ),
        )
    )
    jobs = JobStore(workflows, providers, capabilities, settings.output_dir)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield None
        finally:
            await providers.close()

    server = MCPServer("FLAMORIS Generation", version=__version__, lifespan=lifespan)

    @server.custom_route("/healthz", methods=["GET"])
    async def live(_request: Request) -> JSONResponse:
        """Bounded HTTP liveness probe; provider reachability is reported by system.health."""
        return JSONResponse({"healthy": True})

    async def provider_availability() -> tuple[list[dict[str, object]], dict[str, bool]]:
        health = await providers.health()
        availability = {item["id"]: item.get("available") is True for item in health}
        return health, availability

    @server.tool(name="system.health")
    async def health() -> dict[str, Any]:
        """Check this process and provider connectivity/queue availability."""
        provider_health, _ = await provider_availability()
        return {
            "healthy": True,
            "version": __version__,
            **jobs.activity(),
            "providers": provider_health,
            "provider": "comfyui",
            "provider_health": {
                key: value for key, value in provider_health[0].items() if key != "id"
            },
        }

    @server.tool(name="capabilities.list")
    async def list_capabilities() -> dict[str, Any]:
        """List provider-independent operations and current availability."""
        _, availability = await provider_availability()
        return {"capabilities": capabilities.list(availability)}

    @server.tool(name="capabilities.get")
    async def get_capability(capability_id: str) -> dict[str, Any]:
        """Inspect one capability ID independently from provider transport details."""
        _, availability = await provider_availability()
        return capabilities.get(capability_id, availability)

    @server.tool(name="models.list")
    def list_models(kind: ModelKind | None = None) -> dict[str, Any]:
        """Scan installed model files, optionally filtered by kind."""
        return {"models": catalog.list(kind)}

    @server.tool(name="models.get")
    def get_model(model_id: str) -> dict[str, Any]:
        """Inspect one kind:relative_filename ID from models.list."""
        return catalog.get(model_id)

    @server.tool(name="workflows.list")
    def list_workflows() -> dict[str, Any]:
        """List known templates and workflow IDs (including saved recipes)."""
        return workflows.list()

    @server.tool(name="workflows.build")
    def build_workflow(template: str, parameters: dict[str, Any]) -> dict[str, Any]:
        """Build a known template with validated parameters; returns a workflow_id."""
        return workflows.build(template, parameters)

    @server.tool(name="workflows.save")
    def save_workflow(workflow_id: str) -> dict[str, Any]:
        """Persist the parameter recipe for reuse by workflow_id after restart."""
        return workflows.save(workflow_id)

    @server.tool(name="jobs.submit")
    async def submit_job(workflow_id: str) -> dict[str, Any]:
        """Submit one workflow when this process has no active generation."""
        try:
            return await jobs.submit(workflow_id)
        except GenerationBusyError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="jobs.status")
    async def job_status(job_id: str) -> dict[str, Any]:
        """Poll execution status for a job submitted by this process."""
        return await jobs.status(job_id)

    @server.tool(name="jobs.result")
    async def job_result(job_id: str) -> dict[str, Any]:
        """Return reproducibility metadata; download completed outputs to configured storage."""
        return await jobs.result(job_id)

    @server.tool(name="jobs.cancel")
    async def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel queued work; targeted running interruption requires configured support."""
        return await jobs.cancel(job_id)

    @server.tool(name="assets.list")
    async def list_assets(job_id: str) -> dict[str, Any]:
        """List generated media assets belonging to one completed generation job."""
        try:
            return await jobs.list_assets(job_id)
        except (ValueError, ProviderError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="assets.delete")
    async def delete_asset(asset_id: str) -> dict[str, Any]:
        """Delete one Hub-managed generated asset (not provider originals)."""
        try:
            return await jobs.delete_asset(asset_id)
        except (ValueError, ProviderError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(name="assets.get")
    async def get_asset(asset_id: str) -> Image:
        """Return one generated image asset as MCP-native binary media content."""
        try:
            _, data, media_format = await jobs.get_asset(asset_id)
            return Image(data=data, format=media_format)
        except (ValueError, ProviderError) as exc:
            raise ToolError(str(exc)) from exc

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

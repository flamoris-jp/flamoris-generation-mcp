"""Typed MCP tools served over stdio or HTTP, with one process-owned authority."""

import argparse
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server import MCPServer

from . import __version__
from .comfyui import ComfyUIClient
from .config import ModelKind, Settings
from .jobs import JobStore
from .models import ModelCatalog
from .workflows import Parameters, Template, WorkflowStore


def create_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MCPServer:
    settings = settings or Settings.from_env()
    catalog = ModelCatalog(settings)
    workflows = WorkflowStore(catalog, settings.workflow_dir)
    client = ComfyUIClient(settings, transport)
    jobs = JobStore(workflows, client, settings.output_dir)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield None
        finally:
            await client.close()

    server = MCPServer("FLAMORIS Generation", version=__version__, lifespan=lifespan)

    @server.tool(name="system.health")
    async def health() -> dict[str, Any]:
        """Check this process and provider connectivity/queue availability."""
        return {
            "healthy": True,
            "version": __version__,
            "provider": "comfyui",
            "provider_health": await client.health(),
        }

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
    def build_workflow(template: Template, parameters: Parameters) -> dict[str, Any]:
        """Build a known template with validated parameters; returns a workflow_id."""
        return workflows.build(template, parameters)

    @server.tool(name="workflows.save")
    def save_workflow(workflow_id: str) -> dict[str, Any]:
        """Persist the parameter recipe for reuse by workflow_id after restart."""
        return workflows.save(workflow_id)

    @server.tool(name="jobs.submit")
    async def submit_job(workflow_id: str) -> dict[str, Any]:
        """Submit a built/saved workflow once. Do not retry automatically after timeout."""
        return await jobs.submit(workflow_id)

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

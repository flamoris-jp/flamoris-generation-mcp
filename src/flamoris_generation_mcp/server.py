"""Typed MCP tools served over stdio or HTTP, with one process-owned authority."""

import argparse
from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import StrictBool, StrictInt
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .capabilities import Capability, CapabilityRegistry
from .comfyui import ComfyUIClient
from .config import ModelKind, Settings
from .inputs import ManagedInputs
from .jobs import GenerationBusyError, JobStore
from .models import ModelCatalog
from .provenance import ProvenanceIngress, current_provenance
from .providers import ProviderError, ProviderRegistry
from .providers.comfyui import ComfyUIProvider
from .retention import RetentionStore
from .runtime_evidence import RuntimeEvidence
from .transfers import CHUNK_BYTES, AssetTransfers
from .verification import WorkflowVerification
from .workflows import WorkflowStore


def create_server(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> MCPServer:
    settings = settings or Settings.from_env()
    catalog = ModelCatalog(settings)
    workflows = WorkflowStore(
        catalog,
        settings.workflow_dir,
        settings.workflow_definition_dir,
        v3_enabled=settings.workflow_v3_enabled,
    )
    client = ComfyUIClient(settings, transport)
    comfyui = ComfyUIProvider(client, catalog, workflows)
    providers = ProviderRegistry((comfyui,))
    capabilities = CapabilityRegistry(
        (
            Capability(
                capability_id="image.generate",
                provider_id="comfyui",
                runtime_id="janku",
                workflow_templates=(
                    "text-to-image",
                    "text-to-image-lora",
                    *(workflows.registry.definitions if workflows.registry else ()),
                ),
            ),
        )
    )
    retention = None
    maintenance = comfyui.retention()
    if maintenance is not None:
        retention = RetentionStore(
            settings.output_dir,
            {
                "comfyui": maintenance,
            },
        )
    jobs = JobStore(workflows, providers, capabilities, settings.output_dir, retention)

    transfers = AssetTransfers(
        jobs, max_bytes=settings.transfer_max_bytes, disk_bytes=settings.transfer_disk_bytes
    )

    inputs = ManagedInputs(transfers, settings.output_dir / "managed-inputs")
    # Provider construction precedes JobStore/ManagedInputs because the input lease
    # validates the shared Hub reservation. Wire the adapter only after both exist.
    comfyui.managed_inputs = inputs
    verifier = WorkflowVerification(
        workflows,
        jobs,
        RuntimeEvidence(settings.runtime_evidence_file, settings.comfyui_url),
        settings.managed_input_ready,
    )
    workflows.readiness = verifier
    jobs.verifier = verifier

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield None
        finally:
            await verifier.close()
            await jobs.close()
            await providers.close()

    server = MCPServer(
        "FLAMORIS Generation",
        version=__version__,
        lifespan=lifespan,
        middleware=[ProvenanceIngress(settings)],
    )

    @server.custom_route("/healthz", methods=["GET"])
    async def live(_request: Request) -> JSONResponse:
        """Bounded HTTP liveness probe; provider reachability is reported by system.health."""
        return JSONResponse({"healthy": True})

    async def provider_availability() -> tuple[list[dict[str, object]], dict[str, bool]]:
        health = await providers.health()
        availability = {item["id"]: item.get("available") is True for item in health}
        return health, availability

    @server.tool(
        name="system.health",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def health() -> dict[str, Any]:
        """Check this process and provider connectivity/queue availability."""
        provider_health, _ = await provider_availability()
        return {
            "healthy": True,
            "version": __version__,
            "deployment": {"reservation_scope": "process", "single_instance_required": True},
            **jobs.activity(),
            "providers": provider_health,
            "managed_input_support": {"ready": settings.managed_input_ready},
            "provider": "comfyui",
            "provider_health": {
                key: value for key, value in provider_health[0].items() if key != "id"
            },
        }

    @server.tool(
        name="capabilities.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def list_capabilities() -> dict[str, Any]:
        """List provider-independent operations and current availability."""
        _, availability = await provider_availability()
        return {"capabilities": capabilities.list(availability)}

    @server.tool(
        name="capabilities.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def get_capability(capability_id: str) -> dict[str, Any]:
        """Inspect one capability ID independently from provider transport details."""
        _, availability = await provider_availability()
        return capabilities.get(capability_id, availability)

    @server.tool(
        name="models.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    def list_models(kind: ModelKind | None = None) -> dict[str, Any]:
        """Scan installed model files, optionally filtered by kind."""
        return {"models": catalog.list(kind)}

    @server.tool(
        name="models.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    def get_model(model_id: str) -> dict[str, Any]:
        """Inspect one kind:relative_filename ID from models.list."""
        return catalog.get(model_id)

    @server.tool(
        name="workflows.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    def list_workflows() -> dict[str, Any]:
        """List known templates and workflow IDs (including saved recipes)."""
        return workflows.list()

    @server.tool(
        name="workflows.register",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    def register_workflow(definition: dict[str, Any]) -> dict[str, Any]:
        """Validate, persist, and activate one trusted workflow definition immediately."""
        # Reject routing conflicts before the definition is published to disk.
        # The registry still validates the entire definition before writing it.
        if isinstance(definition, dict):
            capabilities.validate_workflow(
                definition.get("capability_id"), definition.get("provider_id"), definition.get("id")
            )
        result = workflows.register_definition(definition)
        capabilities.assign_workflow(result["capability_id"], result["provider_id"], result["id"])
        return result

    @server.tool(
        name="workflows.build",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    def build_workflow(
        template: str,
        parameters: dict[str, Any],
        definition_version: int | None = None,
        definition_digest: str | None = None,
        require_ready: bool = False,
    ) -> dict[str, Any]:
        """Build a known template with validated parameters; returns a workflow_id."""
        return workflows.build(
            template, parameters, definition_version, definition_digest, require_ready
        )

    @server.tool(
        name="workflows.verify",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def verify_workflow(
        workflow_id: str,
        definition_version: int,
        definition_digest: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Admit a bounded verification through the normal JobStore; poll its job_id."""
        return await verifier.verify(
            workflow_id,
            definition_version,
            definition_digest,
            parameters,
            provenance=current_provenance(),
        )

    if settings.workflow_v3_enabled:
        # A separate opt-in catalog revision preserves every legacy tool/schema
        # and avoids ambiguous Image descriptor/recipe reinterpretation.
        from .workflow_v3 import canonical

        def v3_operation(call):
            try:
                return call()
            except (ValueError, TypeError, KeyError, OSError):
                raise ToolError(
                    "V3 workflow unavailable; check exact pins/profile/input contract"
                ) from None

        read_annotations = ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, openWorldHint=False
        )
        write_annotations = ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, openWorldHint=False
        )

        @server.tool(name="workflows.v3.register", annotations=write_annotations)
        def register_v3(definition: dict[str, Any]) -> dict[str, Any]:
            """Register a strict Image v3 provider or pinned pass-through composition; not ready."""
            return v3_operation(lambda: workflows.v3.versions.register(definition))

        @server.tool(name="workflows.v3.list", annotations=read_annotations)
        def list_v3() -> dict[str, Any]:
            """Return graph-free descriptor revision 3, independent of legacy Image discovery."""

            def project():
                descriptors = []
                with workflows.v3.versions.lock:
                    workflows.v3.versions._current()
                    for workflow_id, version in sorted(
                        workflows.v3.versions.state["active"].items()
                    ):
                        try:
                            root = workflows.v3.versions.get(workflow_id, version)
                            plan = workflows.v3.versions.compile(root.id, root.version, root.digest)
                            descriptor = verifier.v3_descriptor(root, plan)
                            descriptors.append(descriptor)
                        except (ValueError, TypeError, KeyError, OSError):
                            # Revoked/unsupported aliases remain visible, never executable.
                            raw = workflows.v3.versions.state["versions"][
                                workflows.v3.versions.key(workflow_id, version)
                            ]
                            from .workflow_v3 import Definition

                            descriptor = Definition.model_validate(raw).descriptor()
                            descriptor["readiness"].update(
                                state="validated", reason="verification_unavailable"
                            )
                            descriptors.append(descriptor)
                result = {"descriptor_revision": 3, "descriptors": descriptors}
                canonical(result, 256 * 1024)
                return result

            return v3_operation(project)

        @server.tool(
            name="workflows.v3.build",
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, openWorldHint=False
            ),
        )
        def build_v3(
            workflow_id: str,
            definition_version: StrictInt,
            definition_digest: str,
            parameters: dict[str, Any],
            require_ready: StrictBool = True,
        ) -> dict[str, Any]:
            """Build an exact Image v3 invocation; production requires parent attestation."""
            return v3_operation(
                lambda: workflows.build_v3(
                    workflow_id, definition_version, definition_digest, parameters, require_ready
                )
            )

        @server.tool(name="workflows.v3.verify", annotations=write_annotations)
        async def verify_v3(
            workflow_id: str,
            definition_version: StrictInt,
            definition_digest: str,
            parameters: dict[str, Any],
        ) -> dict[str, Any]:
            """Smoke the entire exact composed Image plan through the ordinary JobStore once."""
            try:
                return await verifier.verify(
                    workflow_id,
                    definition_version,
                    definition_digest,
                    parameters,
                    v3=True,
                    provenance=current_provenance(),
                )
            except (ValueError, TypeError, KeyError, OSError):
                raise ToolError("V3 verification unavailable; no automatic replay") from None

        @server.tool(name="workflows.v3.revoke", annotations=write_annotations)
        def revoke_v3(
            workflow_id: str, definition_version: StrictInt, definition_digest: str
        ) -> dict[str, Any]:
            """Administratively revoke an exact retained version and its dependent compositions."""

            def revoke():
                from .image_v3 import Identity

                Identity(id=workflow_id, version=definition_version, digest=definition_digest)
                workflows.v3.versions.revoke(workflow_id, definition_version, definition_digest)
                return {"revoked": True}

            return v3_operation(revoke)

    @server.tool(
        name="workflows.save",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    def save_workflow(workflow_id: str) -> dict[str, Any]:
        """Persist the parameter recipe for reuse by workflow_id after restart."""
        return workflows.save(workflow_id)

    @server.tool(
        name="jobs.submit",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def submit_job(workflow_id: str) -> dict[str, Any]:
        """Submit one workflow when this process has no active generation."""
        try:
            return await jobs.submit(workflow_id, provenance=current_provenance())
        except (GenerationBusyError, ProviderError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="jobs.status",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def job_status(job_id: str) -> dict[str, Any]:
        """Poll execution status for a job submitted by this process."""
        return await jobs.status(job_id)

    @server.tool(
        name="jobs.result",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def job_result(job_id: str) -> dict[str, Any]:
        """Return reproducibility metadata; download completed outputs to configured storage."""
        return await jobs.result(job_id)

    @server.tool(
        name="jobs.cancel",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel queued work; targeted running interruption requires configured support."""
        return await jobs.cancel(job_id)

    @server.tool(
        name="assets.list",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def list_assets(job_id: str) -> dict[str, Any]:
        """List generated media assets belonging to one completed generation job."""
        try:
            return await jobs.list_assets(job_id)
        except (ValueError, ProviderError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="assets.delete",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def delete_asset(asset_id: str) -> dict[str, Any]:
        """Delete one Hub-managed generated asset (not provider originals)."""
        try:
            return await jobs.delete_asset(asset_id)
        except (ValueError, ProviderError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="assets.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def get_asset(asset_id: str) -> Image:
        """Return one generated image asset as MCP-native binary media content."""
        try:
            _, data, media_format = await jobs.get_asset(asset_id)
            return Image(data=data, format=media_format)
        except (ValueError, ProviderError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="assets.prepare",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def prepare_asset(asset_id: str) -> dict[str, Any]:
        """Materialize one asset for bounded transfer; return immutable content digest."""
        try:
            return await transfers.prepare(asset_id)
        except (ValueError, ProviderError, OSError, TimeoutError) as exc:
            raise ToolError(
                str(exc)
                if isinstance(exc, (ValueError, ProviderError))
                else "Asset preparation failed; retry safely"
            ) from None

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
            return await transfers.read(asset_id, sha256, offset, length)
        except (ValueError, ProviderError, OSError, TimeoutError) as exc:
            raise ToolError(
                str(exc)
                if isinstance(exc, (ValueError, ProviderError))
                else "Asset read failed; prepare again"
            ) from None

    @server.tool(
        name="inputs.create",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def create_input(asset_id: str) -> dict[str, Any]:
        """Snapshot a generated image/audio asset as an immutable expiring input."""
        try:
            return await inputs.create(asset_id)
        except (ValueError, ProviderError, OSError, TimeoutError) as exc:
            raise ToolError(
                str(exc)
                if isinstance(exc, (ValueError, ProviderError))
                else "Input creation failed"
            ) from None

    @server.tool(
        name="inputs.get",
        annotations=ToolAnnotations(
            **{"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
        ),
    )
    async def get_input(input_id: str) -> dict[str, Any]:
        """Inspect managed input metadata; an ID is not a Studio ownership grant."""
        try:
            return inputs.get(input_id)
        except (ValueError, OSError) as exc:
            raise ToolError(
                str(exc) if isinstance(exc, ValueError) else "Input retrieval failed"
            ) from None

    @server.tool(
        name="inputs.delete",
        annotations=ToolAnnotations(
            **{"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False}
        ),
    )
    async def delete_input(input_id: str) -> dict[str, Any]:
        """Delete a managed snapshot unless a provider adapter is using it."""
        try:
            return inputs.delete(input_id)
        except (ValueError, OSError) as exc:
            raise ToolError(
                str(exc) if isinstance(exc, ValueError) else "Input deletion failed"
            ) from None

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

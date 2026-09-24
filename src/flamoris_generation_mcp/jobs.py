"""Process-owned Hub jobs, exclusivity and generated asset authority."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .asset_files import AssetFiles
from .capabilities import CapabilityRegistry
from .providers import GenerationRequest, JobSnapshot, ProviderRegistry
from .workflows import AnyRecipe, ExternalRecipe, WorkflowStore, checked_id

TERMINAL = {"completed", "failed", "cancelled"}


class GenerationBusyError(ValueError):
    """A second generation cannot start while this process owns active work."""


MAX_ASSET_BYTES = 64 * 1024 * 1024
MEDIA_TYPES = {
    ".png": ("image", "image/png", "png"),
    ".jpg": ("image", "image/jpeg", "jpeg"),
    ".jpeg": ("image", "image/jpeg", "jpeg"),
    ".webp": ("image", "image/webp", "webp"),
}


@dataclass
class Job:
    job_id: str
    workflow_id: str
    operation: str
    recipe: AnyRecipe
    provider_id: str
    provider_execution_id: str
    snapshot: JobSnapshot = field(default_factory=lambda: JobSnapshot(status="queued"))
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    deleted_outputs: set[int] = field(default_factory=set)


class JobStore:
    def __init__(
        self,
        workflows: WorkflowStore,
        providers: ProviderRegistry,
        capabilities: CapabilityRegistry,
        output_dir: Path,
    ):
        self.workflows = workflows
        self.providers = providers
        self.capabilities = capabilities
        self.output_dir = output_dir
        self._jobs: dict[str, Job] = {}
        self._archived_locks = tuple(asyncio.Lock() for _ in range(64))
        self._submit_lock = asyncio.Lock()
        self._active_job_id: str | None = None

    def _active_job(self) -> tuple[str, str] | None:
        if self._active_job_id is None:
            return None
        job = self._jobs.get(self._active_job_id)
        return self._active_job_id, job.snapshot.status if job else "submitting"

    async def _release_if_terminal(self, job_id: str, job: Job) -> None:
        if job.snapshot.status not in TERMINAL:
            return
        async with self._submit_lock:
            if self._active_job_id == job_id:
                self._active_job_id = None

    async def submit(self, workflow_id: str) -> dict:
        recipe = self.workflows.get(workflow_id)
        capability = self.capabilities.resolve_workflow(recipe.template)
        if isinstance(recipe, ExternalRecipe):
            routed_provider, routed_operation = self.workflows.routing(recipe)
            if (capability.provider_id, capability.capability_id) != (
                routed_provider,
                routed_operation,
            ):
                raise ValueError("Workflow provider/capability registration mismatch")
        operation = capability.capability_id
        provider_id = capability.provider_id
        provider = self.providers.get(provider_id)
        async with self._submit_lock:
            active = self._active_job()
            if active is not None:
                active_job_id, state = active
                if state in TERMINAL:
                    self._active_job_id = None
                else:
                    raise GenerationBusyError(
                        f"Generation is busy: active job {active_job_id} is {state}; "
                        "poll jobs.status/jobs.result or cancel it before submitting again"
                    )
            if len(self._jobs) >= 1024:
                raise ValueError("Job session is full; retrieve results before restarting")
            job_id = uuid4().hex
            self._active_job_id = job_id

        try:
            provider_job = await provider.submit(
                GenerationRequest(
                    operation=operation,
                    workflow_id=workflow_id,
                    payload=recipe,
                ),
                job_id,
            )
        except BaseException:
            async with self._submit_lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
            raise

        self._jobs[job_id] = Job(
            job_id=job_id,
            workflow_id=workflow_id,
            operation=operation,
            recipe=recipe,
            provider_id=provider_id,
            provider_execution_id=provider_job.execution_id,
        )
        return self._metadata(self._jobs[job_id])

    def _get(self, job_id: str) -> Job:
        checked_id(job_id)
        if job_id not in self._jobs:
            raise ValueError("Unknown job ID; jobs belong to this server process")
        return self._jobs[job_id]

    @staticmethod
    def _metadata(job: Job) -> dict:
        return {
            **job.snapshot.as_dict(),
            "job_id": job.job_id,
            "operation": job.operation,
            "provider": job.provider_id,
            "provider_id": job.provider_id,
            "provider_execution_id": job.provider_execution_id,
            "workflow_id": job.workflow_id,
            **job.recipe.model_dump(mode="json"),
        }

    async def _refresh(self, job_id: str, job: Job) -> None:
        if job.snapshot.status not in TERMINAL:
            provider = self.providers.get(job.provider_id)
            job.snapshot = await provider.inspect(job.provider_execution_id)
        await self._release_if_terminal(job_id, job)

    async def status(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            return self._metadata(job)

    def _local_output_path(self, job_id: str, index: int, suffix: str) -> Path:
        checked_id(job_id)
        if suffix not in MEDIA_TYPES:
            raise ValueError("Unsupported generated media output extension")
        return self.output_dir / job_id / f"{index:03d}{suffix}"

    async def result(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            result = self._metadata(job)
            result["files"] = []
            if job.snapshot.status == "completed":
                provider = self.providers.get(job.provider_id)
                for index, output in enumerate(job.snapshot.outputs):
                    suffix = Path(output.filename).suffix.lower()
                    if index in job.deleted_outputs:
                        continue
                    path = self._local_output_path(job_id, index, suffix)
                    with AssetFiles(self.output_dir, job_id) as files:
                        present = files.size(path.name) is not None
                    if not present:
                        data = await provider.materialize(
                            job.provider_execution_id, output.output_id
                        )
                        with AssetFiles(self.output_dir, job_id) as files:
                            files.write(path.name, data)
                    with AssetFiles(self.output_dir, job_id) as files:
                        result["files"].append(
                            {"file": str(path), "size_bytes": files.size(path.name)}
                        )
                with AssetFiles(self.output_dir, job_id) as files:
                    files.write("metadata.json", json.dumps(result, indent=2).encode())
            return result

    @staticmethod
    def _asset_id(job_id: str, index: int) -> str:
        return f"{job_id}:{index:03d}"

    @staticmethod
    def _parse_asset_id(asset_id: str) -> tuple[str, int]:
        match = re.fullmatch(r"([0-9a-f]{32}):([0-9]{3})", asset_id)
        if not match:
            raise ValueError("Asset ID must come from assets.list")
        return match.group(1), int(match.group(2))

    def _archived_lock(self, job_id: str) -> asyncio.Lock:
        # A fixed number of locks bounds memory without limiting stored job history.
        return self._archived_locks[int(job_id[:8], 16) % len(self._archived_locks)]

    def _archived_file(
        self, job_id: str, index: int, files: AssetFiles
    ) -> tuple[Path | None, bool]:
        """Resolve only materialized outputs recorded by this Hub, never metadata paths."""
        manifest = files.read("metadata.json", 1024 * 1024)
        tombstone = files.read(".deleted-assets.json", 4096)
        if manifest is None:
            raise ValueError("Unknown archived asset ID")
        try:
            record = json.loads(manifest)
            deleted = json.loads(tombstone) if tombstone is not None else []
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid archived asset record") from exc
        if (
            not isinstance(record, dict)
            or record.get("job_id") != job_id
            or record.get("status") != "completed"
            or not isinstance(record.get("files"), list)
            or len(record["files"]) > 64
            or not isinstance(deleted, list)
            or len(deleted) > 64
            or any(type(i) is not int or i < 0 or i > 999 for i in deleted)
        ):
            raise ValueError("Invalid archived asset record")
        prefix = f"{index:03d}"
        paths = []
        for item in record["files"]:
            if not isinstance(item, dict) or not isinstance(item.get("file"), str):
                raise ValueError("Invalid archived asset record")
            name = Path(item["file"]).name
            suffix = Path(name).suffix.lower()
            if name == prefix + suffix and suffix in MEDIA_TYPES:
                paths.append(self._local_output_path(job_id, index, suffix))
        if len(paths) > 1:
            raise ValueError("Ambiguous archived asset")
        if not paths and index not in deleted:
            raise ValueError("Unknown archived asset ID")
        return (paths[0] if paths else None), index in deleted

    async def _archived_get(self, job_id: str, index: int) -> tuple[dict, bytes, str]:
        async with self._archived_lock(job_id):
            with AssetFiles(self.output_dir, job_id, create=False) as files:
                path, deleted = self._archived_file(job_id, index, files)
                if deleted or path is None:
                    raise ValueError("Unknown archived asset ID")
                data = files.read(path.name, MAX_ASSET_BYTES)
                if data is None:
                    raise ValueError("Unknown archived asset ID")
            kind, mime, media_format = MEDIA_TYPES[path.suffix.lower()]
            size = len(data)
            asset = {
                "asset_id": self._asset_id(job_id, index),
                "job_id": job_id,
                "filename": path.name,
                "media_kind": kind,
                "mime_type": mime,
                "size_bytes": size,
                "materialized": True,
                "output_index": index,
            }
            return asset, data, media_format

    async def _archived_delete(self, job_id: str, index: int) -> dict:
        async with self._archived_lock(job_id):
            with AssetFiles(self.output_dir, job_id, create=False) as files:
                path, already_deleted = self._archived_file(job_id, index, files)
                if path is None and already_deleted:
                    # An old result call may have rewritten the manifest.
                    matches = [
                        self._local_output_path(job_id, index, suffix)
                        for suffix in MEDIA_TYPES
                        if files.size(f"{index:03d}{suffix}") is not None
                    ]
                    if len(matches) > 1:
                        raise ValueError("Ambiguous archived asset")
                    path = matches[0] if matches else None
                if not already_deleted:
                    prior_bytes = files.read(".deleted-assets.json", 4096)
                    prior = json.loads(prior_bytes) if prior_bytes is not None else []
                    files.write(
                        ".deleted-assets.json",
                        json.dumps(sorted(set(prior) | {index})).encode(),
                    )
                materialized_deleted = files.delete(path.name) if path is not None else False
            return {
                "asset_id": self._asset_id(job_id, index),
                "deleted": True,
                "already_deleted": already_deleted,
                "materialized_deleted": materialized_deleted,
            }

    def _asset_metadata(self, job_id: str, job: Job, index: int) -> tuple[dict, Path]:
        if index in job.deleted_outputs:
            raise ValueError("Unknown asset ID")
        if index >= len(job.snapshot.outputs):
            raise ValueError("Unknown asset ID")
        output = job.snapshot.outputs[index]
        suffix = Path(output.filename).suffix.lower()
        media_kind, mime_type, _ = MEDIA_TYPES.get(suffix, (None, None, None))
        if media_kind is None:
            raise ValueError("Unsupported generated media output extension")
        if (output.media_kind, output.mime_type) != (media_kind, mime_type):
            raise ValueError("Provider output metadata does not match its filename")
        path = self._local_output_path(job_id, index, suffix)
        with AssetFiles(self.output_dir, job_id) as files:
            size = files.size(path.name)
        materialized = size is not None
        asset = {
            "asset_id": self._asset_id(job_id, index),
            "job_id": job_id,
            "filename": path.name,
            "media_kind": media_kind,
            "mime_type": mime_type,
            "size_bytes": size,
            "materialized": materialized,
            "output_index": index,
        }
        return asset, path

    async def _materialize_asset(self, job_id: str, job: Job, index: int) -> tuple[dict, Path]:
        asset, path = self._asset_metadata(job_id, job, index)
        if not asset["materialized"]:
            output = job.snapshot.outputs[index]
            provider = self.providers.get(job.provider_id)
            data = await provider.materialize(job.provider_execution_id, output.output_id)
            with AssetFiles(self.output_dir, job_id) as files:
                files.write(path.name, data)
                asset["size_bytes"] = files.size(path.name)
            asset["materialized"] = True
        return asset, path

    async def list_assets(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot.status != "completed":
                raise ValueError("Assets are available only for completed jobs")
            assets = [
                self._asset_metadata(job_id, job, index)[0]
                for index in range(len(job.snapshot.outputs))
                if index not in job.deleted_outputs
            ]
            return {"job_id": job_id, "assets": assets}

    async def get_asset(self, asset_id: str) -> tuple[dict, bytes, str]:
        job_id, index = self._parse_asset_id(asset_id)
        job = self._jobs.get(job_id)
        if job is None:
            return await self._archived_get(job_id, index)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot.status != "completed":
                raise ValueError("Assets are available only for completed jobs")
            asset, path = await self._materialize_asset(job_id, job, index)
            suffix = path.suffix.lower()
            _, _, media_format = MEDIA_TYPES[suffix]
            with AssetFiles(self.output_dir, job_id) as files:
                data = files.read(path.name, MAX_ASSET_BYTES)
            if data is None:
                raise ValueError("Generated asset changed while being read")
            return asset, data, media_format

    async def delete_asset(self, asset_id: str) -> dict:
        """Remove one Hub-managed output; provider originals are unaffected."""
        job_id, index = self._parse_asset_id(asset_id)
        job = self._jobs.get(job_id)
        if job is None:
            return await self._archived_delete(job_id, index)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot.status != "completed":
                raise ValueError("Assets are available only for completed jobs")
            if index >= len(job.snapshot.outputs):
                raise ValueError("Unknown asset ID")
            # Check the destination even for retries; never unlink an attacker-supplied path.
            suffix = Path(job.snapshot.outputs[index].filename).suffix.lower()
            path = self._local_output_path(job_id, index, suffix)
            already_deleted = index in job.deleted_outputs
            with AssetFiles(self.output_dir, job_id) as files:
                if not already_deleted:
                    files.write(
                        ".deleted-assets.json",
                        json.dumps(sorted(job.deleted_outputs | {index})).encode(),
                    )
                    job.deleted_outputs.add(index)
                materialized_deleted = files.delete(path.name)
            return {
                "asset_id": asset_id,
                "deleted": True,
                "already_deleted": already_deleted,
                "materialized_deleted": materialized_deleted,
            }

    async def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            if job.snapshot.status not in TERMINAL:
                provider = self.providers.get(job.provider_id)
                job.snapshot = await provider.cancel(job.provider_execution_id)
            await self._release_if_terminal(job_id, job)
            return self._metadata(job)

    def activity(self) -> dict[str, object]:
        active = self._active_job()
        return {
            "busy": active is not None and active[1] not in TERMINAL,
            "active_job_id": active[0] if active is not None else None,
        }

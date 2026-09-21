"""Session-owned jobs and generated assets; ComfyUI remains the execution authority."""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .comfyui import ComfyUIClient
from .workflows import Recipe, WorkflowStore, atomic_write, build_prompt, checked_id

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
    workflow_id: str
    recipe: Recipe
    prompt_id: str
    snapshot: dict = field(
        default_factory=lambda: {"status": "queued", "error": None, "outputs": []}
    )
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class JobStore:
    def __init__(self, workflows: WorkflowStore, client: ComfyUIClient, output_dir: Path):
        self.workflows = workflows
        self.client = client
        self.output_dir = output_dir
        self._jobs: dict[str, Job] = {}
        self._submit_lock = asyncio.Lock()
        self._active_job_id: str | None = None

    def _active_job(self) -> tuple[str, str] | None:
        if self._active_job_id is None:
            return None
        job = self._jobs.get(self._active_job_id)
        return self._active_job_id, job.snapshot["status"] if job else "submitting"

    async def _release_if_terminal(self, job_id: str, job: Job) -> None:
        if job.snapshot["status"] not in TERMINAL:
            return
        async with self._submit_lock:
            if self._active_job_id == job_id:
                self._active_job_id = None

    async def submit(self, workflow_id: str) -> dict:
        recipe = self.workflows.get(workflow_id)
        prompt = build_prompt(recipe, self.workflows.catalog)
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

        prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
        try:
            prompt_id = await self.client.submit(prompt, job_id)
        except BaseException:
            async with self._submit_lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
            raise

        self._jobs[job_id] = Job(workflow_id, recipe, prompt_id)
        return self._metadata(job_id, self._jobs[job_id])

    def _get(self, job_id: str) -> Job:
        checked_id(job_id)
        if job_id not in self._jobs:
            raise ValueError("Unknown job ID; jobs belong to this server process")
        return self._jobs[job_id]

    @staticmethod
    def _metadata(job_id: str, job: Job) -> dict:
        return {
            "job_id": job_id,
            "provider": "comfyui",
            "workflow_id": job.workflow_id,
            **job.recipe.model_dump(mode="json"),
            **job.snapshot,
        }

    async def _refresh(self, job_id: str, job: Job) -> None:
        if job.snapshot["status"] not in TERMINAL:
            job.snapshot = await self.client.inspect(job.prompt_id)
        await self._release_if_terminal(job_id, job)

    async def status(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            return self._metadata(job_id, job)

    def _local_output_path(self, job_id: str, index: int, suffix: str) -> Path:
        checked_id(job_id)
        if suffix not in MEDIA_TYPES:
            raise ValueError("Unsupported generated media output extension")
        root = self.output_dir.resolve()
        directory = self.output_dir / job_id
        if directory.is_symlink():
            raise ValueError("Output job directory must not be a symlink")
        path = directory / f"{index:03d}{suffix}"
        if path.is_symlink():
            raise ValueError("Output file must not be a symlink")
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError("Output file escapes the configured output root")
        return path

    async def result(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            result = self._metadata(job_id, job)
            result["files"] = []
            if job.snapshot["status"] == "completed":
                for index, output in enumerate(job.snapshot["outputs"]):
                    suffix = Path(output["filename"]).suffix.lower()
                    path = self._local_output_path(job_id, index, suffix)
                    if not path.is_file():
                        atomic_write(path, await self.client.download(output))
                    result["files"].append({"file": str(path), "size_bytes": path.stat().st_size})
                directory = self.output_dir / job_id
                atomic_write(directory / "metadata.json", json.dumps(result, indent=2).encode())
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

    def _asset_metadata(self, job_id: str, job: Job, index: int) -> tuple[dict, Path]:
        if index >= len(job.snapshot["outputs"]):
            raise ValueError("Unknown asset ID")
        output = job.snapshot["outputs"][index]
        suffix = Path(output["filename"]).suffix.lower()
        media_kind, mime_type, _ = MEDIA_TYPES.get(suffix, (None, None, None))
        if media_kind is None:
            raise ValueError("Unsupported generated media output extension")
        path = self._local_output_path(job_id, index, suffix)
        materialized = path.is_file()
        asset = {
            "asset_id": self._asset_id(job_id, index),
            "job_id": job_id,
            "filename": path.name,
            "media_kind": media_kind,
            "mime_type": mime_type,
            "size_bytes": path.stat().st_size if materialized else None,
            "materialized": materialized,
            "output_index": index,
        }
        return asset, path

    async def _materialize_asset(self, job_id: str, job: Job, index: int) -> tuple[dict, Path]:
        asset, path = self._asset_metadata(job_id, job, index)
        if not asset["materialized"]:
            output = job.snapshot["outputs"][index]
            atomic_write(path, await self.client.download(output))
            asset["size_bytes"] = path.stat().st_size
            asset["materialized"] = True
        return asset, path

    async def list_assets(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot["status"] != "completed":
                raise ValueError("Assets are available only for completed jobs")
            assets = [
                self._asset_metadata(job_id, job, index)[0]
                for index in range(len(job.snapshot["outputs"]))
            ]
            return {"job_id": job_id, "assets": assets}

    async def get_asset(self, asset_id: str) -> tuple[dict, bytes, str]:
        job_id, index = self._parse_asset_id(asset_id)
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job_id, job)
            if job.snapshot["status"] != "completed":
                raise ValueError("Assets are available only for completed jobs")
            asset, path = await self._materialize_asset(job_id, job, index)
            suffix = path.suffix.lower()
            _, _, media_format = MEDIA_TYPES[suffix]
            size = path.stat().st_size
            if size > MAX_ASSET_BYTES:
                raise ValueError(f"Asset exceeds the {MAX_ASSET_BYTES} byte retrieval limit")
            data = path.read_bytes()
            if len(data) > MAX_ASSET_BYTES or len(data) != size:
                raise ValueError("Generated asset changed while being read")
            return asset, data, media_format

    async def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            if job.snapshot["status"] not in TERMINAL:
                job.snapshot = await self.client.cancel(job.prompt_id)
            await self._release_if_terminal(job_id, job)
            return self._metadata(job_id, job)

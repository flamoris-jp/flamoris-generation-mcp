"""Session-owned jobs; ComfyUI remains the execution authority."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from .comfyui import ComfyUIClient
from .workflows import Recipe, WorkflowStore, atomic_write, build_prompt, checked_id

TERMINAL = {"completed", "failed", "cancelled"}


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

    async def submit(self, workflow_id: str) -> dict:
        recipe = self.workflows.get(workflow_id)
        prompt = build_prompt(recipe, self.workflows.catalog)
        async with self._submit_lock:
            if len(self._jobs) >= 1024:
                raise ValueError("Job session is full; retrieve results before restarting")
            job_id = uuid4().hex
            prompt["7"]["inputs"]["filename_prefix"] = f"flamoris/{job_id}"
            prompt_id = await self.client.submit(prompt, job_id)
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

    async def _refresh(self, job: Job) -> None:
        if job.snapshot["status"] not in TERMINAL:
            job.snapshot = await self.client.inspect(job.prompt_id)

    async def status(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job)
            return self._metadata(job_id, job)

    async def result(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            await self._refresh(job)
            result = self._metadata(job_id, job)
            result["files"] = []
            if job.snapshot["status"] == "completed":
                # Local destinations are generated, never taken from provider paths.
                directory = self.output_dir / job_id
                if directory.is_symlink():
                    raise ValueError("Output job directory must not be a symlink")
                for index, output in enumerate(job.snapshot["outputs"]):
                    suffix = Path(output["filename"]).suffix.lower()
                    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                        raise ValueError("Unsupported image output extension")
                    path = directory / f"{index:03d}{suffix}"
                    if path.is_symlink():
                        raise ValueError("Output file must not be a symlink")
                    if not path.is_file():
                        atomic_write(path, await self.client.download(output))
                    result["files"].append({"file": str(path), "size_bytes": path.stat().st_size})
                atomic_write(directory / "metadata.json", json.dumps(result, indent=2).encode())
            return result

    async def cancel(self, job_id: str) -> dict:
        job = self._get(job_id)
        async with job.lock:
            if job.snapshot["status"] not in TERMINAL:
                job.snapshot = await self.client.cancel(job.prompt_id)
            return self._metadata(job_id, job)

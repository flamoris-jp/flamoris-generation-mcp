import asyncio

import pytest

from flamoris_generation_mcp.jobs import GenerationBusyError, JobStore
from flamoris_generation_mcp.models import ModelCatalog
from flamoris_generation_mcp.providers import (
    GenerationRequest,
    JobSnapshot,
    ProviderError,
    ProviderHealth,
    ProviderJob,
    ProviderOutput,
    ProviderRegistry,
)
from flamoris_generation_mcp.workflows import Parameters, WorkflowStore


class FakeProvider:
    provider_id = "fake"

    def __init__(self):
        self.requests: list[tuple[GenerationRequest, str]] = []
        self.snapshots: dict[str, JobSnapshot] = {}
        self.submit_error: ProviderError | None = None
        self.submit_entered: asyncio.Event | None = None
        self.submit_release: asyncio.Event | None = None
        self.cancel_snapshot: JobSnapshot | None = None
        self.closed = False

    async def health(self):
        return ProviderHealth(available=True, details={"queue_depth": 0})

    async def submit(self, request, job_id):
        if self.submit_entered is not None:
            self.submit_entered.set()
        if self.submit_release is not None:
            await self.submit_release.wait()
        if self.submit_error is not None:
            raise self.submit_error
        self.requests.append((request, job_id))
        execution_id = f"execution-{len(self.requests)}"
        self.snapshots[execution_id] = JobSnapshot(status="queued")
        return ProviderJob(execution_id=execution_id)

    async def inspect(self, execution_id):
        return self.snapshots[execution_id]

    async def cancel(self, execution_id):
        if self.cancel_snapshot is not None:
            self.snapshots[execution_id] = self.cancel_snapshot
        return self.snapshots[execution_id]

    async def materialize(self, execution_id, output_id):
        assert execution_id in self.snapshots
        assert output_id == "image-0"
        return b"provider-neutral image"

    async def close(self):
        self.closed = True


def make_store(settings):
    workflows = WorkflowStore(ModelCatalog(settings), settings.workflow_dir)
    provider = FakeProvider()
    providers = ProviderRegistry((provider,))
    jobs = JobStore(workflows, providers, settings.output_dir, provider_id="fake")
    workflow = workflows.build(
        "text-to-image",
        Parameters(checkpoint="base.safetensors", positive_prompt="flowers"),
    )
    return workflows, provider, providers, jobs, workflow["workflow_id"]


async def test_job_store_tracks_provider_neutral_identity_and_outputs(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)

    submitted = await jobs.submit(workflow_id)

    assert submitted["operation"] == "image.generate"
    assert submitted["provider"] == submitted["provider_id"] == "fake"
    assert submitted["provider_execution_id"] == "execution-1"
    request, hub_job_id = provider.requests[0]
    assert hub_job_id == submitted["job_id"]
    assert request.operation == "image.generate"
    assert request.workflow_id == workflow_id

    provider.snapshots["execution-1"] = JobSnapshot(
        status="completed",
        outputs=(
            ProviderOutput(
                output_id="image-0",
                filename="provider-name.png",
                media_kind="image",
                mime_type="image/png",
            ),
        ),
    )
    result = await jobs.result(submitted["job_id"])

    assert result["outputs"] == [
        {
            "output_id": "image-0",
            "filename": "provider-name.png",
            "media_kind": "image",
            "mime_type": "image/png",
        }
    ]
    assert result["files"][0]["size_bytes"] == len(b"provider-neutral image")


async def test_provider_submit_failure_releases_hub_reservation(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)
    provider.submit_error = ProviderError("test provider rejected request")

    with pytest.raises(ProviderError, match="rejected"):
        await jobs.submit(workflow_id)

    provider.submit_error = None
    assert (await jobs.submit(workflow_id))["status"] == "queued"


async def test_provider_neutral_submit_reservation_is_race_safe(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)
    provider.submit_entered = asyncio.Event()
    provider.submit_release = asyncio.Event()

    first = asyncio.create_task(jobs.submit(workflow_id))
    await provider.submit_entered.wait()

    with pytest.raises(GenerationBusyError, match="is submitting"):
        await jobs.submit(workflow_id)

    provider.submit_release.set()
    assert (await first)["provider_execution_id"] == "execution-1"
    assert len(provider.requests) == 1


async def test_cancel_requested_keeps_capacity_until_terminal_confirmation(settings):
    _, provider, _, jobs, workflow_id = make_store(settings)
    first = await jobs.submit(workflow_id)
    provider.cancel_snapshot = JobSnapshot(
        status="cancel_requested",
        metadata={"cancel_supported": True},
    )

    cancelled = await jobs.cancel(first["job_id"])
    assert cancelled["status"] == "cancel_requested"
    with pytest.raises(GenerationBusyError, match="is cancel_requested"):
        await jobs.submit(workflow_id)

    provider.snapshots["execution-1"] = JobSnapshot(status="cancelled")
    assert (await jobs.status(first["job_id"]))["status"] == "cancelled"
    assert (await jobs.submit(workflow_id))["provider_execution_id"] == "execution-2"


async def test_registry_health_and_lifecycle_are_process_owned():
    provider = FakeProvider()
    registry = ProviderRegistry((provider,))

    assert await registry.health() == [{"id": "fake", "available": True, "queue_depth": 0}]
    await registry.close()
    assert provider.closed is True

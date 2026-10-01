"""Automatic attestation through the ordinary JobStore and provider authority."""

import asyncio
import hashlib
import time
from contextlib import contextmanager
from uuid import uuid4

from .durable import CommitUnknown, Records
from .image_decode import decode_image
from .image_profile import image_topology, smoke_budget
from .workflows import ExternalRecipe

PROFILE_REVISION = 1
DEADLINE = 300


class WorkflowVerification:
    def __init__(self, workflows, jobs, evidence, infrastructure_ready=False):
        self.workflows = workflows
        self.jobs = jobs
        self.evidence = evidence
        self.infrastructure_ready = infrastructure_ready
        self.records = Records(jobs.output_dir, "workflow-attestations")
        self.instance = uuid4().hex
        self.tasks = set()
        self.uncertain = set()

    @staticmethod
    def identity(definition):
        return {
            "workflow_id": definition.id,
            "definition_version": definition.version,
            "definition_digest": definition.digest,
            "profile_revision": PROFILE_REVISION,
        }

    @staticmethod
    def compatible(definition, parameters, runtime):
        if definition.schema_version != 2:
            raise ValueError("Image metadata upgrade required")
        manifest = runtime["manifest"]
        topology = image_topology(definition)
        if any(
            definition.graph[key]["class_type"] not in manifest["nodes"]
            for key in topology["active"]
        ):
            raise ValueError("runtime_evidence_unavailable")
        checkpoint = next(
            name for name, p in definition.parameters.items() if p.role == "checkpoint"
        )
        model = "checkpoint:" + parameters[checkpoint]
        if model not in manifest["models"]:
            raise ValueError("runtime_evidence_unavailable")
        return {"model": model, "content": manifest["models"][model]}

    def descriptor(self, definition):
        item = definition.metadata()
        try:
            with self.evidence.guard() as runtime:
                self._require_record(definition, runtime)
            item["readiness"].update(state="ready", reason=None)
        except (ValueError, OSError, TypeError, KeyError):
            item["readiness"].update(state="validated", reason="verification_unavailable")
        return item

    def _require_record(self, definition, runtime, parameters=None):
        if definition.id in self.uncertain:
            raise ValueError("Workflow production readiness persistence uncertain")
        record = self.records.read(definition.id + ".json")
        if (
            not record
            or record.get("identity") != self.identity(definition)
            or record.get("state") != "ready"
            or record.get("runtime") != runtime
            or record.get("evidence_revision") != 1
            or not record.get("attempt_id")
            or not record.get("output")
        ):
            raise ValueError("Workflow production readiness is unavailable")
        if parameters is not None:
            if self.compatible(definition, parameters, runtime) != record.get("model"):
                raise ValueError("Model outside verified profile domain")
        return record

    def require(self, definition, parameters):
        with self.evidence.guard() as runtime:
            self._require_record(definition, runtime, parameters)
        if definition.image.mode == "img2img" and not self.infrastructure_ready:
            raise ValueError("Managed input infrastructure readiness is unavailable")

    @contextmanager
    def execution_guard(self, recipe, definition, expected):
        if not isinstance(recipe, ExternalRecipe) or not (recipe.require_ready or expected):
            yield
            return
        with self.evidence.guard() as runtime:
            if expected is not None and expected != runtime:
                raise ValueError("runtime_evidence_unavailable")
            self.compatible(definition, recipe.parameters, runtime)
            if recipe.require_ready:
                self.require(definition, recipe.parameters)
            yield

    def before_post(self, recipe, definition, expected):
        if isinstance(recipe, ExternalRecipe) and (recipe.require_ready or expected):
            with self.evidence.guard() as runtime:
                if expected is not None and runtime != expected:
                    raise ValueError("runtime_evidence_unavailable")
                if recipe.require_ready:
                    self.require(definition, recipe.parameters)

    async def verify(self, workflow_id, version, digest, parameters):
        if type(version) is not int or not isinstance(digest, str):
            raise ValueError("Verification requires exact Definition identity")
        built = self.workflows.build(workflow_id, parameters, version, digest)
        recipe = self.workflows.get(built["workflow_id"])
        definition = self.workflows.capture(recipe)
        if definition is None:
            raise ValueError("Verification requires a registered Image definition")
        budget = smoke_budget(definition, built["prompt"])
        with self.evidence.guard() as runtime:
            model = self.compatible(definition, recipe.parameters, runtime)
        attempt = {
            "identity": self.identity(definition),
            "runtime": runtime,
            "model": model,
            "budget": budget,
            "instance": self.instance,
            "attempt_id": uuid4().hex,
            "deadline": time.time() + DEADLINE,
            "evidence_revision": 1,
            "state": "pending",
        }
        result = await self.jobs.submit(built["workflow_id"], verification=attempt)
        task = asyncio.create_task(self._watch(result["job_id"]))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return result

    def admit(self, job, attempt):
        with self.workflows.registry._register_lock, self.evidence.guard() as runtime:
            definition = self.workflows.capture(job.recipe)
            if self.identity(definition) != attempt["identity"] or runtime != attempt["runtime"]:
                raise ValueError("Verification identity changed before admission")
            try:
                self.records.write(definition.id + ".json", attempt)
            except CommitUnknown:
                self.uncertain.add(definition.id)
                raise
            job.verification = dict(attempt)

    def fail(self, job, reason):
        job.verification.update(state="failed", reason=reason)
        try:
            record = self.records.read(job.definition.id + ".json")
            if record and record.get("attempt_id") == job.verification["attempt_id"]:
                self.records.write(job.definition.id + ".json", job.verification)
        except (OSError, ValueError, TypeError, KeyError):
            self.uncertain.add(job.definition.id)

    async def refresh(self, job):
        if job.verification.get("state") != "pending":
            return
        if job.verification.get("instance") != self.instance:
            self.fail(job, "interrupted_verification")
            return
        if time.time() > job.verification["deadline"]:
            self.fail(job, "verification_timeout")
            return
        if job.snapshot.status in {"failed", "cancelled", "unknown"}:
            self.fail(job, "verification_failed")
            return
        if job.snapshot.status != "completed":
            return
        try:
            async with asyncio.timeout(max(0.01, job.verification["deadline"] - time.time())):
                if len(job.snapshot.outputs) != 1:
                    raise ValueError("Unexpected verification output count")
                output = job.snapshot.outputs[0]
                provider = self.jobs.providers.get(job.provider_id)
                data = await provider.materialize(job.provider_execution_id, output.output_id)
                dimensions = await asyncio.to_thread(
                    decode_image, data, output.mime_type, max_dimension=512
                )
                budget = job.verification["budget"]
                if dimensions != {k: budget[k] for k in ("width", "height")}:
                    raise ValueError("Verification output dimensions differ")
                with self.workflows.registry._register_lock, self.evidence.guard() as runtime:
                    current = self.workflows.capture(job.recipe)
                    record = self.records.read(current.id + ".json")
                    if (
                        self.identity(current) != job.verification["identity"]
                        or runtime != job.verification["runtime"]
                        or not record
                        or record.get("attempt_id") != job.verification["attempt_id"]
                        or record.get("state") != "pending"
                    ):
                        raise ValueError("Verification superseded")
                    ready = {
                        **job.verification,
                        "state": "ready",
                        "completed_at": time.time(),
                        "output": {
                            **dimensions,
                            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                        },
                    }
                    self.records.write(current.id + ".json", ready)
                    job.verification = ready
        except (ValueError, OSError, TypeError, KeyError, TimeoutError):
            self.fail(job, "verification_evidence_failed")
        except Exception:
            self.fail(job, "verification_output_unavailable")

    async def _watch(self, job_id):
        job = self.jobs._get(job_id)
        while job.verification.get("state") == "pending":
            try:
                await self.jobs.status(job_id)
            except Exception:
                self.fail(job, "verification_observation_failed")
            if job.verification.get("state") == "pending":
                await asyncio.sleep(1)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

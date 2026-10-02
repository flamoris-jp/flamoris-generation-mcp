"""Process-owned immutable v3 history. Active aliases never change exact include pins.

All references are retained in this first delivery: there is deliberately no GC.
Publication refuses full storage rather than evicting a pinned child or saved plan.
"""

import copy
import threading
from collections.abc import Callable

from .composition import Compiler
from .durable import CommitUnknown, Records
from .workflow_v3 import Definition, canonical, decode_definition

MAX_RECORDS = 256
MAX_ALIASES = 126
STORE_BYTES = 8 * 1024 * 1024


class DefinitionVersions:
    def __init__(self, root, validate: Callable[[Definition], None]):
        self.records = Records(root, "definition-versions-v3", limit=STORE_BYTES)
        self.validate = validate
        self.lock = threading.RLock()
        self.uncertain = False
        self.state = self.records.read("index.json") or {
            "revision": 1,
            "versions": {},
            "active": {},
            "revoked": [],
        }
        self._check_state(self.state)

    @staticmethod
    def key(workflow_id, version):
        return f"{workflow_id}:{version}"

    def _check_state(self, state):
        if set(state) != {"revision", "versions", "active", "revoked"} or state["revision"] != 1:
            raise ValueError("Invalid version-store schema")
        if (
            type(state["versions"]) is not dict
            or type(state["active"]) is not dict
            or type(state["revoked"]) is not list
        ):
            raise ValueError("Invalid version-store index")
        if len(state["versions"]) > MAX_RECORDS or len(state["active"]) > MAX_ALIASES:
            raise ValueError("Version-store index exceeds limits")
        for key, raw in state["versions"].items():
            definition = Definition.model_validate(raw)
            if key != self.key(definition.id, definition.version):
                raise ValueError("Version-store identity mismatch")
        if len(set(state["revoked"])) != len(state["revoked"]) or set(state["revoked"]) - set(
            state["versions"]
        ):
            raise ValueError("Invalid revoked version")
        for workflow_id, version in state["active"].items():
            if type(version) is not int or self.key(workflow_id, version) not in state["versions"]:
                raise ValueError("Invalid active alias")
        canonical(state, STORE_BYTES)
        # Restart validates all usable closures again with current adapter/profile policy.
        for key in state["versions"]:
            if key not in state["revoked"]:
                definition = Definition.model_validate(state["versions"][key])
                try:
                    self._compiler(state).compile(definition)
                except ValueError:
                    # A revoked child invalidates its parents, but must not prevent startup.
                    if not self._has_revoked_dependency(definition, state, set()):
                        raise

    def _has_revoked_dependency(self, definition, state, visited):
        key = self.key(definition.id, definition.version)
        if key in state["revoked"]:
            return True
        if key in visited:
            return False
        visited.add(key)
        for include in definition.includes:
            raw = state["versions"].get(self.key(include.workflow_id, include.version))
            if raw and self._has_revoked_dependency(Definition.model_validate(raw), state, visited):
                return True
        return False

    def _compiler(self, state):
        return Compiler(lambda i, v, d: self._lookup(state, i, v, d), self.validate)

    def _lookup(self, state, workflow_id, version, expected_digest=None):
        key = self.key(workflow_id, version)
        if key in state["revoked"]:
            raise ValueError("Workflow version is revoked")
        raw = state["versions"].get(key)
        if raw is None:
            raise ValueError("Unknown exact workflow version")
        definition = Definition.model_validate(copy.deepcopy(raw))
        if expected_digest is not None and definition.digest != expected_digest:
            raise ValueError("Workflow version digest mismatch")
        return definition

    def _current(self):
        if self.uncertain:
            raise ValueError("Version-store durability uncertain; restart/reconcile required")

    def get(self, workflow_id, version=None, expected_digest=None):
        with self.lock:
            self._current()
            if version is None:
                version = self.state["active"].get(workflow_id)
            return self._lookup(self.state, workflow_id, version, expected_digest)

    def compile(self, workflow_id, version, expected_digest):
        with self.lock:
            self._current()
            root = self._lookup(self.state, workflow_id, version, expected_digest)
            return self._compiler(self.state).compile(root)

    def register(self, raw):
        if isinstance(raw, bytes):
            raw = decode_definition(raw)
        canonical(raw, 256 * 1024)
        definition = Definition.model_validate(raw)
        key = self.key(definition.id, definition.version)
        with self.lock:
            self._current()
            existing = self.state["versions"].get(key)
            if existing is not None:
                current = self._lookup(self.state, definition.id, definition.version)
                if current.digest != definition.digest:
                    raise ValueError("Immutable version cannot be replaced")
                self._compiler(self.state).compile(current)
                return {"registered": True, "unchanged": True, **current.descriptor()}
            if len(self.state["versions"]) >= MAX_RECORDS:
                raise ValueError("Version store is full; pinned history cannot be evicted")
            active = self.state["active"].get(definition.id)
            if active is not None and definition.version <= active:
                raise ValueError("New active version must increase")
            if active is None and len(self.state["active"]) >= MAX_ALIASES:
                raise ValueError("Active alias limit exceeded")
            candidate = copy.deepcopy(self.state)
            candidate["versions"][key] = definition.model_dump(mode="json", by_alias=True)
            candidate["active"][definition.id] = definition.version
            self._compiler(candidate).compile(definition)
            self._publish(candidate)
            return {"registered": True, "unchanged": False, **definition.descriptor()}

    def _publish(self, candidate):
        canonical(candidate, STORE_BYTES)
        try:
            self.records.write("index.json", candidate)
        except CommitUnknown:
            self.uncertain = True
            raise
        self.state = candidate

    def revoke(self, workflow_id, version, expected_digest):
        with self.lock:
            self._current()
            self._lookup(self.state, workflow_id, version, expected_digest)
            candidate = copy.deepcopy(self.state)
            candidate["revoked"].append(self.key(workflow_id, version))
            self._publish(candidate)

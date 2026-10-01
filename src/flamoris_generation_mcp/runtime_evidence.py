"""Read measured revisions published by a trusted runtime mutation authority.

The authority must hold the same exclusive lock across runtime mutations, advance
a never-reused provider epoch, and publish a measured manifest before releasing it.
An absent authority is unavailable, not evidence from names/stat/health.
"""

import fcntl
import hashlib
import json
import os
import re
import stat
import time
from contextlib import contextmanager


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class RuntimeEvidence:
    def __init__(self, path, provider_url):
        self.path = path
        self.provider_url = str(provider_url).rstrip("/")

    @contextmanager
    def guard(self):
        if self.path is None:
            raise ValueError("runtime_evidence_unavailable")
        fd = None
        try:
            fd = os.open(str(self.path) + ".lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("runtime_evidence_unavailable")
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            runtime = self.read()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if fd is not None:
                os.close(fd)
            raise ValueError("runtime_evidence_unavailable") from exc
        try:
            yield runtime
        finally:
            os.close(fd)

    def read(self):
        if self.path is None:
            raise ValueError("runtime_evidence_unavailable")
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("runtime_evidence_unavailable")
            raw = os.read(fd, 1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("runtime_evidence_unavailable")
        finally:
            os.close(fd)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("runtime_evidence_unavailable")
        manifest = data["manifest"]
        if (
            type(data.get("schema_version")) is not int
            or data.get("schema_version") != 1
            or data.get("continuity") != "exclusive-mutation-lock-v1"
            or data.get("provider_url") != self.provider_url
            or not isinstance(data.get("provider_epoch"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", data["provider_epoch"])
            or type(data.get("expires_at")) not in (int, float)
            or not time.time() < data["expires_at"] <= time.time() + 300
            or not isinstance(manifest, dict)
            or not isinstance(manifest.get("nodes"), dict)
            or not isinstance(manifest.get("models"), dict)
            or not all(
                manifest.get(k) for k in ("core", "dependencies", "config", "nodes", "models")
            )
        ):
            raise ValueError("runtime_evidence_unavailable")
        for key in ("core", "dependencies", "config"):
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest[key]):
                raise ValueError("runtime_evidence_unavailable")
        for node in manifest["nodes"].values():
            if not isinstance(node, dict):
                raise ValueError("runtime_evidence_unavailable")
            if not all(
                re.fullmatch(r"sha256:[0-9a-f]{64}", node.get(k, ""))
                for k in ("interface", "implementation")
            ):
                raise ValueError("runtime_evidence_unavailable")
        for digest in manifest["models"].values():
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("runtime_evidence_unavailable")
        return {
            "provider_epoch": data["provider_epoch"],
            "fingerprint": fingerprint(manifest),
            "evidence_revision": 1,
            "manifest": manifest,
        }

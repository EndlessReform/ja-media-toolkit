"""Deterministic identities shared by product compilers."""

from __future__ import annotations

import hashlib
import json


def fingerprint(*parts: object) -> str:
    """Hash JSON-normalized product inputs with stable ordering."""

    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    """Derive a compact stable identifier from immutable identifier inputs."""

    return prefix + "-" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:32]

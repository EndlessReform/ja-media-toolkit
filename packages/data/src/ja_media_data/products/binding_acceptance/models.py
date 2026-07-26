"""Immutable automatic binding-admission records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class AcceptedBinding:
    """One proposal admitted by the current automatic acceptance policy."""

    acceptance_id: str
    proposal_id: str
    namespace: str
    series_id: str
    episode: str
    audio_capture_id: str
    acceptance_method: str
    policy_version: str
    input_fingerprint: str

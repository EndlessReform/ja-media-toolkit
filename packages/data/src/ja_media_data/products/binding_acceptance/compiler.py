"""Deterministic compiler for automatic binding admission."""

from __future__ import annotations

from dataclasses import dataclass
import duckdb

from ja_media_data.products.binding_acceptance.models import AcceptedBinding
from ja_media_data.products.identities import fingerprint, stable_id


ACCEPTANCE_POLICY_VERSION = "accept-resolver-proposals-v1"


@dataclass(frozen=True)
class CompiledAcceptances:
    """A complete accepted-binding product prepared for one atomic commit."""

    rows: tuple[AcceptedBinding, ...]
    fingerprint: str


def compile_product(connection: duckdb.DuckDBPyConnection) -> CompiledAcceptances:
    """Admit every resolver proposal under the intentionally permissive v1 policy."""

    source = connection.execute(
        """SELECT proposal_id, namespace, series_id, episode, audio_capture_id,
                  input_data_version, recipe_version
           FROM episode_binding_proposals
           ORDER BY namespace, series_id, episode, audio_capture_id, proposal_id"""
    ).fetchall()
    rows = tuple(
        AcceptedBinding(
            stable_id("acceptance", row[0], ACCEPTANCE_POLICY_VERSION),
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            "automatic",
            ACCEPTANCE_POLICY_VERSION,
            fingerprint(*row),
        )
        for row in source
    )
    return CompiledAcceptances(rows, fingerprint(ACCEPTANCE_POLICY_VERSION, source))

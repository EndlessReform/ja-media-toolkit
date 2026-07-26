"""Execute the staged Gate 1 method matrix and score every output uniformly."""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from time import perf_counter

from ja_media_core.subsync import subtitle_anchor_fit_score, subtitle_goodness_of_fit

from subtitle_alignment.matrix_methods import MethodSpec, invoke, method_specs
from subtitle_alignment.matrix_sample import MatrixPair, select_pairs, stage_pair
from subtitle_alignment.matrix_store import write_matrix_results
from subtitle_alignment.matrix_transform import TransformFacts, infer_transform
from subtitle_alignment.track_io import read_subtitle_cues


MATRIX_VERSION = "matrix-v3"


@dataclass(frozen=True)
class PairTask:
    root: str
    pair: MatrixPair
    methods: tuple[MethodSpec, ...]
    executables: dict[str, str]


def run_method_matrix(
    dataset: Path,
    identity_result: Path,
    *,
    output_root: Path,
    sample_size: int,
    workers: int,
) -> Path:
    """Run the fixed method matrix on a deterministic identity-score stratum."""

    manifest = json.loads((dataset / "manifest.json").read_text())
    result_name = f"gate1-{MATRIX_VERSION}-{manifest['dataset_id']}-n{sample_size}"
    output_root.mkdir(parents=True, exist_ok=True)
    target = output_root / result_name
    if (target / "summary.json").is_file():
        return target
    if target.exists():
        raise FileExistsError(f"incomplete result directory exists: {target}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{result_name}-", dir=output_root))
    started = perf_counter()
    try:
        pairs = select_pairs(dataset, identity_result, sample_size=sample_size)
        if len(pairs) != sample_size:
            raise RuntimeError(f"selected {len(pairs)} pairs, expected {sample_size}")
        for pair in pairs:
            stage_pair(dataset, temporary, pair)
        specs = method_specs()
        executables = _executables()
        tasks = [
            PairTask(str(temporary), pair, specs, executables) for pair in pairs
        ]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            batches = pool.map(_run_pair, tasks, chunksize=1)
            runs = [row for batch in batches for row in batch]
        elapsed_s = perf_counter() - started
        write_matrix_results(
            temporary,
            manifest=manifest,
            pairs=pairs,
            specs=specs,
            runs=runs,
            versions=executable_versions(executables),
            workers=workers,
            elapsed_s=elapsed_s,
            matrix_version=MATRIX_VERSION,
        )
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def _run_pair(task: PairTask) -> list[dict[str, object]]:
    root = Path(task.root)
    pair_root = root / "artifacts" / task.pair.pair_id
    anchor = next(pair_root.glob("anchor.*"))
    candidate = next(pair_root.glob("candidate.*"))
    anchor_cues = read_subtitle_cues(anchor, task.pair.anchor_format)
    candidate_cues = read_subtitle_cues(candidate, task.pair.candidate_format)
    cue_path = pair_root / "cue-transforms.jsonl"
    cue_lines: list[str] = []
    rows = []
    for spec in task.methods:
        if spec.key == "identity":
            facts, cue_rows = infer_transform(candidate_cues, candidate_cues)
            row = _scored_row(task.pair, spec, facts, 0.0, candidate)
            row.update(
                goodness_of_fit=task.pair.identity_goodness,
                anchor_fit_score=task.pair.identity_score,
                gain_over_identity=0.0,
            )
        else:
            output = pair_root / f"{spec.key}.{task.pair.candidate_format}"
            invocation = invoke(
                spec,
                executable=task.executables[spec.tool],
                anchor=anchor,
                candidate=candidate,
                output=output,
            )
            row, cue_rows = _consume_output(
                task.pair, spec, invocation, output, anchor_cues, candidate_cues
            )
            _write_log(pair_root, spec.key, "stdout", invocation.stdout)
            _write_log(pair_root, spec.key, "stderr", invocation.stderr)
        rows.append(row)
        for cue in cue_rows:
            cue.update(pair_id=task.pair.pair_id, method=spec.key)
            cue_lines.append(json.dumps(cue) + "\n")
    cue_path.write_text("".join(cue_lines))
    return rows


def _consume_output(pair, spec, invocation, output, anchor_cues, candidate_cues):
    base = _base_row(pair, spec, invocation.runtime_ms, output)
    if invocation.error or invocation.returncode != 0 or not output.is_file():
        base.update(
            status="tool_error",
            error=invocation.error or f"exit code {invocation.returncode}",
        )
        return base, []
    try:
        aligned = read_subtitle_cues(output, pair.candidate_format)
        goodness = subtitle_goodness_of_fit(anchor_cues, aligned)
        score = subtitle_anchor_fit_score(anchor_cues, aligned)
        try:
            facts, cue_rows = infer_transform(candidate_cues, aligned)
        except ValueError as error:
            row = _base_row(pair, spec, invocation.runtime_ms, output)
            row.update(
                status="scored_transform_error",
                error=f"ValueError: {error}",
                goodness_of_fit=goodness,
                anchor_fit_score=score,
                gain_over_identity=score - pair.identity_score,
            )
            return row, []
        row = _scored_row(pair, spec, facts, invocation.runtime_ms, output)
        row.update(
            goodness_of_fit=goodness,
            anchor_fit_score=score,
            gain_over_identity=score - pair.identity_score,
        )
        if spec.tool == "alass":
            row["offset_bound_exceeded"] = facts.max_abs_offset_s > 30.0
        return row, cue_rows
    except (OSError, UnicodeError, ValueError) as error:
        base.update(status="parse_error", error=f"{type(error).__name__}: {error}")
        return base, []


def _scored_row(pair, spec, facts, runtime_ms, output):
    row = _base_row(pair, spec, runtime_ms, output)
    row.update(status="scored", error=None, **asdict(facts))
    return row


def _base_row(pair, spec, runtime_ms, output):
    result_root = Path(output).parents[2]
    pair_root = Path("artifacts") / pair.pair_id
    return {
        "pair_id": pair.pair_id,
        "method": spec.key,
        "tool": spec.tool,
        "transform_class": spec.transform_class,
        "runtime_ms": runtime_ms,
        "output_path": str(output.relative_to(result_root)),
        "output_sha256": _hash(output) if output.is_file() else None,
        "stdout_path": (
            str(pair_root / f"{spec.key}.stdout.log")
            if spec.key != "identity" else None
        ),
        "stderr_path": (
            str(pair_root / f"{spec.key}.stderr.log")
            if spec.key != "identity" else None
        ),
        "status": None,
        "error": None,
        "goodness_of_fit": None,
        "anchor_fit_score": None,
        "gain_over_identity": None,
        "scale": None,
        "median_offset_s": None,
        "min_offset_s": None,
        "max_offset_s": None,
        "max_abs_offset_s": None,
        "offset_blocks": None,
        "reconstruction_rmse_ms": None,
        # ALASS has no search-bound flag equivalent to ffsubsync's. This is a
        # post-hoc diagnostic, not a rejection and not accumulated clock drift.
        "offset_bound_exceeded": None,
    }


def _executables() -> dict[str, str]:
    found = {name: shutil.which(binary) for name, binary in {
        "alass": "alass-cli", "ffsubsync": "ffsubsync"
    }.items()}
    missing = [name for name, path in found.items() if path is None]
    if missing:
        raise RuntimeError(f"missing executable(s): {', '.join(missing)}")
    return {name: str(path) for name, path in found.items()}


def executable_versions(executables: dict[str, str]) -> dict[str, str]:
    return {
        name: subprocess.run(
            [path, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
        for name, path in executables.items()
    }


def _write_log(root: Path, method: str, stream: str, content: str) -> None:
    (root / f"{method}.{stream}.log").write_text(content)


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

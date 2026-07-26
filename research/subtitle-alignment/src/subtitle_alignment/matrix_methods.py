"""Pinned subprocess-only method definitions for the Gate 1 matrix."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import subprocess
from time import perf_counter


@dataclass(frozen=True)
class MethodSpec:
    """One deliberately restricted aligner arm."""

    key: str
    tool: str
    transform_class: str
    scale_enabled: bool
    piecewise_enabled: bool
    split_penalty: int | None
    args: tuple[str, ...]


@dataclass(frozen=True)
class Invocation:
    """Captured executable result without importing either aligner."""

    returncode: int
    runtime_ms: float
    stdout: str
    stderr: str
    error: str | None = None


def method_specs() -> tuple[MethodSpec, ...]:
    """Return the staged comparison matrix in stable display order."""

    methods = [
        MethodSpec("identity", "custom", "identity", False, False, None, ()),
        MethodSpec(
            "alass-global", "alass", "global", False, False, None,
            ("--no-split", "--disable-fps-guessing"),
        ),
        MethodSpec(
            "ffsubsync-global", "ffsubsync", "global", False, False, None,
            ("--no-fix-framerate", "--skip-infer-framerate-ratio",
             "--max-offset-seconds", "30"),
        ),
        MethodSpec(
            "alass-clock-global", "alass", "clock+global", True, False, None,
            ("--no-split",),
        ),
        MethodSpec(
            "ffsubsync-clock-global", "ffsubsync", "clock+global", True,
            False, None,
            ("--skip-infer-framerate-ratio", "--max-offset-seconds", "30"),
        ),
    ]
    for penalty in (5, 10, 20):
        methods.extend(
            [
                MethodSpec(
                    f"alass-piecewise-p{penalty}", "alass", "piecewise",
                    False, True, penalty,
                    ("--disable-fps-guessing", "--split-penalty", str(penalty)),
                ),
                MethodSpec(
                    f"ffsubsync-piecewise-p{penalty}", "ffsubsync",
                    "piecewise", False, True, penalty,
                    ("--no-fix-framerate", "--skip-infer-framerate-ratio",
                     "--split-penalty", str(penalty),
                     "--max-offset-seconds", "30"),
                ),
            ]
        )
    return tuple(methods)


def definition_rows(specs: tuple[MethodSpec, ...]) -> list[dict[str, object]]:
    """Serialize method definitions for DuckDB, reports, and later review."""

    rows = []
    for order, spec in enumerate(specs):
        row = asdict(spec)
        row["method_order"] = order
        row["args"] = " ".join(spec.args)
        rows.append(row)
    return rows


def invoke(
    spec: MethodSpec,
    *,
    executable: str,
    anchor: Path,
    candidate: Path,
    output: Path,
    timeout_s: float = 120,
) -> Invocation:
    """Run one arms-length CLI invocation with no shell or in-process import."""

    if spec.tool == "alass":
        command = [executable, str(anchor), str(candidate), str(output), *spec.args]
    elif spec.tool == "ffsubsync":
        command = [
            executable, str(anchor), "-i", str(candidate), "-o", str(output),
            *spec.args,
        ]
    else:
        raise ValueError(f"cannot invoke non-executable method: {spec.key}")
    started = perf_counter()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        return Invocation(
            completed.returncode,
            (perf_counter() - started) * 1000,
            completed.stdout,
            completed.stderr,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return Invocation(
            -1, (perf_counter() - started) * 1000, "", "",
            f"{type(error).__name__}: {str(error)[:400]}",
        )

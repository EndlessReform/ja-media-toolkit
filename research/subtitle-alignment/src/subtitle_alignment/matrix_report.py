"""Compile the generated Gate 1 tables into the Typst research paper."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess


def write_matrix_paper(result: Path, *, pdf_root: Path) -> Path:
    """Copy the reference Typst source and compile it against generated tables."""

    source = Path(__file__).with_name("gate1_matrix.typ")
    paper = result / "gate1-matrix-paper.typ"
    shutil.copyfile(source, paper)
    pdf_root.mkdir(parents=True, exist_ok=True)
    dataset_suffix = result.name.removeprefix("gate1-")
    pdf = pdf_root / f"gate1-{dataset_suffix}.pdf"
    subprocess.run(
        [
            "typst", "compile", "--root", str(result),
            paper.name, str(pdf),
        ],
        cwd=result,
        check=True,
    )
    return pdf

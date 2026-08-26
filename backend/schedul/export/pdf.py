"""PDF export via headless LibreOffice.

SPEC.md section 13 warns that PDF is the hard part of a web version, not the
easy part: Excel gives pagination, repeating headers and A4 fitting for free and
HTML renderers do not.

We sidestep it rather than solve it. Nothing renders HTML to PDF. The real
``.xlsx`` is generated -- with the print titles, ``fitToWidth``, A4 landscape and
page setup the vendored sheet code already gets right -- and LibreOffice converts
it, honouring all of them. The A4 fitting stays Excel's, exactly as it is today.

The exported workbook is ordinary Excel, so "Microsoft Print to PDF" from the
desktop remains available and produces the same thing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

__all__ = ["PdfError", "soffice_path", "available", "to_pdf", "merge_pdfs"]

log = logging.getLogger(__name__)

#: Conversion is a subprocess against a full office suite; a slow first run
#: builds the user profile.
DEFAULT_TIMEOUT = 180


class PdfError(RuntimeError):
    """PDF conversion failed."""


def soffice_path() -> str | None:
    """Locate LibreOffice, honouring ``SCHEDUL_SOFFICE``."""
    configured = os.environ.get("SCHEDUL_SOFFICE")
    if configured:
        return configured if Path(configured).exists() else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    # The usual Windows install locations, since the target is a Windows shop.
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def available() -> bool:
    """Whether PDF export can run here.

    Checked so the UI can offer Excel export alone rather than failing at the
    click, on a machine with no LibreOffice installed.
    """
    return soffice_path() is not None


def to_pdf(
    xlsx_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> Path:
    """Convert one workbook to PDF and return the result's path."""
    source = Path(xlsx_path).resolve()
    if not source.exists():
        raise PdfError(f"nothing to convert at {source}")

    binary = soffice_path()
    if binary is None:
        raise PdfError(
            "LibreOffice was not found, so PDF export is unavailable. Install it, "
            "or set SCHEDUL_SOFFICE to its path. The .xlsx export is unaffected "
            "and prints to PDF from Excel."
        )

    target_dir = Path(out_dir).resolve() if out_dir else source.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    # A private profile per conversion: concurrent runs sharing one profile
    # collide, and the second silently produces nothing.
    with tempfile.TemporaryDirectory(prefix="schedul-lo-") as profile:
        command = [
            binary,
            "--headless",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            "pdf:calc_pdf_Export",
            "--outdir",
            str(target_dir),
            str(source),
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise PdfError(f"PDF conversion timed out after {timeout}s") from exc

    produced = target_dir / (source.stem + ".pdf")
    if not produced.exists():
        raise PdfError(
            "LibreOffice produced no PDF.\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return produced


def merge_pdfs(parts: Sequence[str | Path], out_path: str | Path) -> Path:
    """Concatenate PDFs, for issuing a whole building as one document.

    Uses pypdf when it is installed and otherwise reports that plainly, rather
    than producing a corrupt file by concatenating bytes.
    """
    try:
        from pypdf import PdfWriter
    except ImportError as exc:
        raise PdfError(
            "merging PDFs needs pypdf (pip install pypdf); the individual PDFs "
            "were still produced"
        ) from exc

    writer = PdfWriter()
    for part in parts:
        writer.append(str(part))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as fh:
        writer.write(fh)
    return out

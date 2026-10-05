"""De-identified table of the series in a DICOM session folder.

Reads only whitelisted, non-identifying header fields (series number,
description, protocol, image type, timing) and counts files per series.
Patient name/ID/birth date, dates, times and institution are never read into
the output, so the table is safe to paste into an issue or chat.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, asdict
from pathlib import Path

import pydicom
from pydicom.errors import InvalidDicomError

SAFE_TAGS = ["SeriesNumber", "SeriesDescription", "ProtocolName", "ImageType",
             "RepetitionTime", "EchoTime", "SeriesInstanceUID"]


@dataclass
class SeriesRow:
    series: int
    description: str
    protocol: str
    image_type: str
    n_files: int
    tr_ms: str
    te_ms: str


def _fmt_num(v) -> str:
    if v in (None, ""):
        return ""
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v)


def scan_session(folder: Path) -> list[SeriesRow]:
    """One row per (SeriesNumber, SeriesInstanceUID), sorted by series number."""
    if not folder.is_dir():
        raise FileNotFoundError(f"DICOM session folder not found: {folder}")
    series: dict[tuple[int, str], dict] = {}
    for f in sorted(p for p in folder.rglob("*") if p.is_file()):
        try:
            ds = pydicom.dcmread(f, stop_before_pixels=True, specific_tags=SAFE_TAGS)
        except (InvalidDicomError, OSError):
            continue  # not a DICOM (e.g. a stray text file)
        if "SeriesNumber" not in ds:
            continue
        key = (int(ds.SeriesNumber), str(ds.get("SeriesInstanceUID", "")))
        entry = series.get(key)
        if entry is None:
            it = ds.get("ImageType", "")
            it = "\\".join(it) if isinstance(it, (list, tuple, pydicom.multival.MultiValue)) else str(it)
            series[key] = entry = {
                "series": key[0],
                "description": str(ds.get("SeriesDescription", "")),
                "protocol": str(ds.get("ProtocolName", "")),
                "image_type": it,
                "n_files": 0,
                "tr_ms": _fmt_num(ds.get("RepetitionTime")),
                "te_ms": _fmt_num(ds.get("EchoTime")),
            }
        entry["n_files"] += 1
    return [SeriesRow(**v) for _, v in sorted(series.items())]


def to_tsv(rows: list[SeriesRow]) -> str:
    buf = io.StringIO()
    fields = list(SeriesRow.__dataclass_fields__)
    w = csv.DictWriter(buf, fieldnames=fields, delimiter="\t", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(asdict(r))
    return buf.getvalue()

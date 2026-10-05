"""Participants/sessions table: study ID <-> BIDS label <-> DICOM session folder."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

COLUMNS = ["study_id", "bids_label", "session_folder", "protocol", "group", "notes"]
PROTOCOLS = {"pilot", "v1"}
_ALNUM = re.compile(r"^[A-Za-z0-9]+$")


class SessionError(ValueError):
    pass


@dataclass(frozen=True)
class Session:
    study_id: str
    label: str            # BIDS label without "sub-"
    session_folder: str
    protocol: str
    group: str = ""
    notes: str = ""

    @property
    def sub(self) -> str:
        return f"sub-{self.label}"


def derive_label(study_id: str, label_regex: str, label_format: str) -> str:
    """ACHI_2026_001 -> ACHI001 (per config); result must be alphanumeric."""
    m = re.match(label_regex, study_id)
    if not m:
        raise SessionError(
            f"study ID {study_id!r} does not match study.label_regex {label_regex!r}; "
            "set bids_label explicitly in the sessions table")
    label = label_format.format(**m.groupdict())
    if not _ALNUM.match(label):
        raise SessionError(f"derived label {label!r} is not alphanumeric")
    return label


def load_sessions(path: Path, label_regex: str, label_format: str) -> list[Session]:
    if not path.is_file():
        raise SessionError(
            f"sessions table not found: {path}\n"
            "Copy config/sessions.example.tsv to config/sessions.tsv and fill it in "
            "(it is gitignored because folder names contain scan dates).")
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if not rows:
        raise SessionError(f"{path} has no rows")
    absent = [c for c in ("study_id", "session_folder", "protocol") if c not in rows[0]]
    if absent:
        raise SessionError(f"{path} is missing columns: {absent}")

    out, seen_ids, seen_labels = [], set(), set()
    for i, r in enumerate(rows, start=2):  # line 1 is the header
        r = {k: (v or "").strip() for k, v in r.items() if k}
        sid = r["study_id"]
        label = r.get("bids_label") or derive_label(sid, label_regex, label_format)
        label = label.removeprefix("sub-")
        if not _ALNUM.match(label):
            raise SessionError(f"{path}:{i}: BIDS label {label!r} must be alphanumeric "
                               "(BIDS forbids _ and - in labels)")
        if r["protocol"] not in PROTOCOLS:
            raise SessionError(f"{path}:{i}: protocol must be one of {sorted(PROTOCOLS)}, "
                               f"got {r['protocol']!r}")
        if not r["session_folder"]:
            raise SessionError(f"{path}:{i}: session_folder is empty")
        if sid in seen_ids or label in seen_labels:
            raise SessionError(f"{path}:{i}: duplicate study_id or bids_label ({sid}/{label})")
        seen_ids.add(sid)
        seen_labels.add(label)
        out.append(Session(sid, label, r["session_folder"], r["protocol"],
                           r.get("group", ""), r.get("notes", "")))
    return out


def find_session(sessions: list[Session], key: str) -> Session:
    """Look up by BIDS label (with or without sub-) or by study ID."""
    key = key.removeprefix("sub-")
    for s in sessions:
        if key in (s.label, s.study_id):
            return s
    known = ", ".join(s.label for s in sessions)
    raise SessionError(f"subject {key!r} not in sessions table (known: {known})")

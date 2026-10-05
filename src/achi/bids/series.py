"""Expected-series table, dcm2bids_helper inspection, and the series check.

The check compares what the scanner produced (dcm2bids_helper sidecars + NIfTI
headers) with config/expected_series/<protocol>.tsv and returns a readable diff.
Any mismatch is an error: a repeated or skipped scan shifts the series numbers,
and converting anyway would silently put the wrong scan under the wrong name.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

import nibabel as nib

ROLES = {"anat_T1w", "fmap_PA", "fmap_AP", "sbref", "bold", "ignore"}


@dataclass(frozen=True)
class Expected:
    series: int
    role: str
    task: str = ""
    run: int | None = None
    description: str = ".*"      # regex, full match
    n_vols: int | None = None
    pe_dir: str = ""

    @property
    def label(self) -> str:
        if self.role in ("bold", "sbref"):
            return f"{self.role} task-{self.task} run-{self.run}"
        if self.role == "fmap_AP":
            return f"fmap dir-AP (= SBRef of task-{self.task} run-{self.run})"
        return self.role


@dataclass(frozen=True)
class Observed:
    series: int
    description: str
    n_vols: int
    pe_dir: str = ""
    sidecar: str = ""


def load_expected(path: Path, tasks: dict[str, int]) -> list[Expected]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    rows = list(csv.DictReader(lines, delimiter="\t"))
    out, seen = [], set()
    for r in rows:
        r = {k: (v or "").strip() for k, v in r.items()}
        e = Expected(series=int(r["series"]), role=r["role"], task=r.get("task", ""),
                     run=int(r["run"]) if r.get("run") else None,
                     description=r.get("description") or ".*",
                     n_vols=int(r["n_vols"]) if r.get("n_vols") else None,
                     pe_dir=r.get("pe_dir", ""))
        if e.role not in ROLES:
            raise ValueError(f"{path}: series {e.series}: unknown role {e.role!r}")
        if e.series in seen:
            raise ValueError(f"{path}: series {e.series} listed twice")
        if e.role == "bold" and tasks.get(e.task) != e.n_vols:
            raise ValueError(f"{path}: series {e.series} expects {e.n_vols} volumes for "
                             f"{e.task}, config acquisition.tasks says {tasks.get(e.task)}")
        re.compile(e.description)
        seen.add(e.series)
        out.append(e)
    return out


def read_helper_dir(helper_dir: Path) -> list[Observed]:
    """Parse dcm2bids_helper output: one JSON sidecar (+ NIfTI) per converted series."""
    out = []
    for js in sorted(helper_dir.rglob("*.json")):
        meta = json.loads(js.read_text())
        if "SeriesNumber" not in meta:
            continue
        nii = js.with_suffix(".nii.gz")
        if not nii.exists():
            nii = js.with_suffix(".nii")
        n_vols = 0
        if nii.exists():
            shape = nib.load(nii).shape
            n_vols = shape[3] if len(shape) > 3 else 1
        out.append(Observed(int(meta["SeriesNumber"]), str(meta.get("SeriesDescription", "")),
                            n_vols, str(meta.get("PhaseEncodingDirection", "")), js.name))
    return sorted(out, key=lambda o: (o.series, o.sidecar))


@dataclass
class CheckResult:
    errors: list[str]
    warnings: list[str]
    table: str          # human-readable expected-vs-found table

    @property
    def ok(self) -> bool:
        return not self.errors


def check_series(expected: list[Expected], observed: list[Observed],
                 task_series_regex: str) -> CheckResult:
    errors, warnings, lines = [], [], []
    by_num: dict[int, list[Observed]] = {}
    for o in observed:
        by_num.setdefault(o.series, []).append(o)

    def fmt(o: Observed | None) -> str:
        if o is None:
            return "-- missing --"
        pe = f", PE {o.pe_dir}" if o.pe_dir else ""
        return f"{o.description!r} ({o.n_vols} vol{pe})"

    for e in expected:
        found = by_num.get(e.series, [])
        if e.role == "ignore":
            continue
        status = "ok"
        if not found:
            status = "MISSING"
            errors.append(f"series {e.series} ({e.label}) is missing")
        elif len(found) > 1:
            status = "DUPLICATE"
            errors.append(f"series {e.series} ({e.label}) appears {len(found)} times")
        else:
            o = found[0]
            problems = []
            if not re.fullmatch(e.description, o.description):
                problems.append(f"description {o.description!r} does not match {e.description!r}")
            if e.n_vols is not None and o.n_vols != e.n_vols:
                problems.append(f"{o.n_vols} volumes, expected {e.n_vols}")
            if e.pe_dir and o.pe_dir and o.pe_dir != e.pe_dir:
                problems.append(f"phase encoding {o.pe_dir}, expected {e.pe_dir}")
            if e.pe_dir and not o.pe_dir:
                warnings.append(f"series {e.series}: no PhaseEncodingDirection in sidecar")
            if problems:
                status = "MISMATCH"
                errors.append(f"series {e.series} ({e.label}): " + "; ".join(problems))
        lines.append((str(e.series), e.label, status, fmt(found[0] if found else None)))

    listed = {e.series for e in expected}
    task_re = re.compile(task_series_regex)
    for num, obs in sorted(by_num.items()):
        if num in listed:
            continue
        for o in obs:
            if task_re.search(o.description):
                errors.append(f"series {num} {o.description!r} is a task scan that is not in the "
                              "expected table: a scan was probably repeated or added")
                lines.append((str(num), "(not expected)", "UNEXPECTED", fmt(o)))
            else:
                lines.append((str(num), "(not in table: ignored)", "-", fmt(o)))
    for e in expected:
        if e.role == "ignore" and e.series in by_num:
            for o in by_num[e.series]:
                if task_re.search(o.description):
                    errors.append(f"series {e.series} should be an excluded series but is the "
                                  f"task scan {o.description!r}: the numbering has shifted "
                                  "(repeated or added scan?)")
                    lines.append((str(e.series), "ignore", "UNEXPECTED", fmt(o)))
                else:
                    lines.append((str(e.series), "ignore", "-", fmt(o)))
    lines.sort(key=lambda r: int(r[0]))

    w = [max(len(r[i]) for r in lines + [("series", "expected", "status", "found")]) for i in range(3)]
    head = f"{'series':>{w[0]}}  {'expected':<{w[1]}}  {'status':<{w[2]}}  found"
    body = [f"{a:>{w[0]}}  {b:<{w[1]}}  {c:<{w[2]}}  {d}" for a, b, c, d in lines]
    return CheckResult(errors, warnings, "\n".join([head, "-" * len(head)] + body))

"""Post-conversion checks on one subject's BIDS folder.

These are the problems that have actually happened (or would silently break
fMRIPrep): wrong file counts, missing field maps, IntendedFor with backslashes or
pointing at files that do not exist (fMRIPrep then skips distortion correction
without an error), missing TaskName, and SliceTiming values >= TR.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import nibabel as nib

from .series import Expected

_ENT = re.compile(r"_task-(?P<task>[A-Za-z0-9]+)_run-(?P<run>\d+)_")


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _sidecar(nii: Path) -> dict | None:
    js = Path(str(nii).removesuffix(".gz").removesuffix(".nii") + ".json")
    if not js.is_file():
        return None
    return json.loads(js.read_text())


def _resolve_intendedfor(entry: str, bids_root: Path, sub_dir: Path) -> Path:
    if entry.startswith("bids::"):
        return bids_root / entry.removeprefix("bids::")
    return sub_dir / entry


def validate_subject(bids_root: Path, sub: str, expected: list[Expected], tr: float,
                     n_slices: int | None = None, pe_bold: str = "j-") -> Validation:
    v = Validation()
    sub_dir = bids_root / sub
    if not (bids_root / "dataset_description.json").is_file():
        v.errors.append("dataset_description.json missing at the BIDS root")
    if not sub_dir.is_dir():
        v.errors.append(f"{sub_dir} does not exist")
        return v

    exp_bold = {(e.task, e.run): e for e in expected if e.role == "bold"}
    exp_sbref = {(e.task, e.run) for e in expected if e.role == "sbref"}

    # ---- anat -------------------------------------------------------------
    t1 = sorted((sub_dir / "anat").glob(f"{sub}*_T1w.nii*"))
    if len(t1) != 1:
        v.errors.append(f"expected 1 T1w image, found {len(t1)}")

    # ---- func -------------------------------------------------------------
    func = sub_dir / "func"
    bolds = sorted(func.glob(f"{sub}_*_bold.nii*"))
    sbrefs = sorted(func.glob(f"{sub}_*_sbref.nii*"))
    v.summary.append(f"{len(bolds)} bold (expected {len(exp_bold)}), "
                     f"{len(sbrefs)} sbref (expected {len(exp_sbref)})")
    if len(bolds) != len(exp_bold):
        v.errors.append(f"expected {len(exp_bold)} BOLD runs, found {len(bolds)}")
    if len(sbrefs) != len(exp_sbref):
        v.errors.append(f"expected {len(exp_sbref)} SBRef images, found {len(sbrefs)}")

    found_bold = set()
    for nii in bolds:
        m = _ENT.search(nii.name)
        if not m:
            v.errors.append(f"{nii.name}: cannot parse task/run entities")
            continue
        key = (m["task"], int(m["run"]))
        found_bold.add(key)
        name = nii.name
        meta = _sidecar(nii)
        if meta is None:
            v.errors.append(f"{name}: JSON sidecar missing")
            continue
        if meta.get("TaskName") != m["task"]:
            v.errors.append(f"{name}: TaskName is {meta.get('TaskName')!r}, expected {m['task']!r}")
        rt = meta.get("RepetitionTime")
        if rt is None or abs(float(rt) - tr) > 1e-3:
            v.errors.append(f"{name}: RepetitionTime {rt}, expected {tr}")
        st = meta.get("SliceTiming")
        if not st:
            v.errors.append(f"{name}: SliceTiming missing")
        else:
            bad = [t for t in st if not 0 <= float(t) < tr]
            if bad:
                v.errors.append(f"{name}: {len(bad)} SliceTiming values outside [0, TR={tr}), "
                                f"e.g. {bad[:3]} (are they in ms?)")
            if n_slices and len(st) != n_slices:
                v.errors.append(f"{name}: {len(st)} SliceTiming entries, expected {n_slices}")
        pe = meta.get("PhaseEncodingDirection")
        if pe != pe_bold:
            v.errors.append(f"{name}: PhaseEncodingDirection {pe!r}, expected {pe_bold!r}")
        if "TotalReadoutTime" not in meta and "EffectiveEchoSpacing" not in meta:
            v.errors.append(f"{name}: no TotalReadoutTime/EffectiveEchoSpacing (needed for SDC)")
        e = exp_bold.get(key)
        if e is None:
            v.errors.append(f"{name}: unexpected task/run")
        elif e.n_vols is not None:
            shape = nib.load(nii).shape
            nv = shape[3] if len(shape) > 3 else 1
            if nv != e.n_vols:
                v.errors.append(f"{name}: {nv} volumes, expected {e.n_vols}")
    for key in sorted(set(exp_bold) - found_bold):
        v.errors.append(f"missing BOLD: task-{key[0]} run-{key[1]}")

    found_sbref = set()
    for nii in sbrefs:
        m = _ENT.search(nii.name)
        if m:
            found_sbref.add((m["task"], int(m["run"])))
    for key in sorted(exp_sbref - found_sbref):
        v.errors.append(f"missing SBRef: task-{key[0]} run-{key[1]}")
    for key in sorted(found_sbref - exp_sbref):
        v.errors.append(f"unexpected SBRef: task-{key[0]} run-{key[1]} "
                        "(quest run-1's SBRef should be the AP field map)")

    # ---- fmap -------------------------------------------------------------
    fmap = sub_dir / "fmap"
    for d, pe_expected in (("AP", "j-"), ("PA", "j")):
        epis = sorted(fmap.glob(f"{sub}_*dir-{d}_epi.nii*"))
        if len(epis) != 1:
            v.errors.append(f"expected exactly one dir-{d} epi field map, found {len(epis)}")
            continue
        meta = _sidecar(epis[0])
        name = epis[0].name
        if meta is None:
            v.errors.append(f"{name}: JSON sidecar missing")
            continue
        if meta.get("PhaseEncodingDirection") != pe_expected:
            v.errors.append(f"{name}: PhaseEncodingDirection {meta.get('PhaseEncodingDirection')!r}, "
                            f"expected {pe_expected!r}")
        if "TotalReadoutTime" not in meta:
            v.errors.append(f"{name}: TotalReadoutTime missing (needed for PEPOLAR SDC)")
        intended = meta.get("IntendedFor")
        if not intended:
            v.errors.append(f"{name}: IntendedFor missing: fMRIPrep would skip distortion correction")
            continue
        if isinstance(intended, str):
            intended = [intended]
        targets: set[Path] = set()
        for entry in intended:
            if "\\" in entry:
                v.errors.append(f"{name}: IntendedFor entry uses backslashes: {entry!r}")
                continue
            target = _resolve_intendedfor(entry, bids_root, sub_dir)
            if not target.is_file():
                v.errors.append(f"{name}: IntendedFor points to a missing file: {entry!r}")
            else:
                targets.add(target.resolve())
        not_covered = [b.name for b in bolds if b.resolve() not in targets]
        if not_covered:
            v.errors.append(f"{name}: IntendedFor does not list {len(not_covered)} BOLD run(s), "
                            f"e.g. {not_covered[:2]}")
    return v

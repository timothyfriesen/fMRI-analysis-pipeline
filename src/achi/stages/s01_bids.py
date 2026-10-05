"""Stage 01: DICOM -> BIDS.

1. Inspect the session with dcm2bids_helper and check every series against
   config/expected_series/<protocol>.tsv. Any repeated, skipped or renamed scan
   stops the stage with a readable diff (nothing is converted).
2. Convert with dcm2bids, using a config generated from the same table.
3. Validate the result (file counts, field maps, IntendedFor, TaskName,
   SliceTiming < TR, volume counts, phase-encoding directions).

Re-converting a subject that already exists needs --force; the old folder is
moved to <work>/bids_replaced/ (not deleted), and stage 02 will notice the changed
inputs and clear fMRIPrep's work directory.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
from pathlib import Path

from ..bids.dcm2bids_config import build_config
from ..bids.series import check_series, load_expected, read_helper_dir
from ..bids.validate import validate_subject
from ..config import REPO_ROOT
from ..context import RunContext, StageError, fingerprint

DATASET_DESCRIPTION = {
    "Name": "ACHI: affective conditioned hallucinations task fMRI",
    "BIDSVersion": "1.9.0",
    "DatasetType": "raw",
}
BIDSIGNORE = "tmp_dcm2bids/\n"


def _expected(ctx: RunContext):
    table = ctx.cfg["bids"]["expected_series"].get(ctx.session.protocol)
    if not table:
        raise StageError(f"no expected-series table for protocol {ctx.session.protocol!r} "
                         "(config bids.expected_series)")
    path = Path(table)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path, load_expected(path, ctx.cfg["acquisition"]["tasks"])


def _dicom_files(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*") if p.is_file()]


def _inspect(ctx: RunContext, dicom_dir: Path, helper_root: Path):
    ctx.logger.info("inspecting %s with dcm2bids_helper ...", dicom_dir)
    ctx.run_cmd(["dcm2bids_helper", "-d", dicom_dir, "-o", helper_root, "--force"],
                always=True)
    observed = read_helper_dir(helper_root / "tmp_dcm2bids" / "helper")
    if not observed:
        raise StageError(f"dcm2bids_helper produced no sidecars from {dicom_dir}")
    return observed


def _validate(ctx: RunContext, expected) -> None:
    acq = ctx.cfg["acquisition"]
    v = validate_subject(ctx.paths["bids"], ctx.session.sub, expected, float(acq["tr"]),
                         acq.get("n_slices"), acq.get("pe_dir_bold", "j-"))
    for line in v.summary:
        ctx.logger.info("BIDS check: %s", line)
    for w in v.warnings:
        ctx.logger.warning("BIDS check: %s", w)
    if not v.ok:
        raise StageError("BIDS validation failed:\n  - " + "\n  - ".join(v.errors))
    ctx.logger.info("BIDS check passed for %s", ctx.session.sub)


def run(ctx: RunContext) -> None:
    sess = ctx.session
    dicom_dir = ctx.paths["dicom_root"] / sess.session_folder
    if not dicom_dir.is_dir():
        raise StageError(f"DICOM folder not found: {dicom_dir}")
    table_path, expected = _expected(ctx)
    bids_root = ctx.paths["bids"]
    sub_dir = bids_root / sess.sub

    files = _dicom_files(dicom_dir)
    ctx.logger.info("%d files in %s; expected-series table: %s (%s)", len(files), dicom_dir,
                    table_path.name, sess.protocol)
    fp = fingerprint(files, {"table": table_path.read_text(),
                             "tasks": ctx.cfg["acquisition"]["tasks"],
                             "bids": ctx.cfg["bids"], "label": sess.label})

    if sub_dir.exists() and ctx.is_up_to_date(fp):
        ctx.logger.info("%s already converted from these DICOMs; re-checking only "
                        "(use --force to reconvert)", sess.sub)
        _validate(ctx, expected)
        return
    if sub_dir.exists() and not ctx.force:
        raise StageError(f"{sub_dir} already exists but was not produced from the current "
                         "DICOMs/settings by this pipeline. Rerun with --force to replace it "
                         "(the old folder is kept under work/bids_replaced/).")

    # ---- 1. inspect + series check ---------------------------------------
    if ctx.dry_run:
        with tempfile.TemporaryDirectory(prefix="achi-helper-") as tmp:
            observed = _inspect(ctx, dicom_dir, Path(tmp))
    else:
        helper_root = ctx.paths["work"] / "dcm2bids_helper" / sess.sub
        if helper_root.exists():
            shutil.rmtree(helper_root)
        observed = _inspect(ctx, dicom_dir, helper_root)
    res = check_series(expected, observed, ctx.cfg["bids"]["task_series_regex"])
    ctx.logger.info("series check for %s (%s):\n%s", sess.sub, sess.study_id, res.table)
    for w in res.warnings:
        ctx.logger.warning(w)
    if not res.ok:
        raise StageError(
            "the session does not match the expected series table, so nothing was "
            "converted:\n  - " + "\n  - ".join(res.errors) +
            f"\nCompare the table above with {table_path}. If a scan was repeated or "
            "skipped, decide which series to use before converting this session.")
    ctx.logger.info("series check passed: %d expected series found", len(
        [e for e in expected if e.role != "ignore"]))

    # ---- 2. convert ------------------------------------------------------
    config = build_config(expected, ctx.cfg["bids"].get("intendedfor_style", "relative"))
    config_path = ctx.paths["work"] / "dcm2bids_config" / f"{sess.sub}_dcm2bids_config.json"
    ctx.write_text(config_path, json.dumps(config, indent=2))
    ctx.mkdir(bids_root)
    if not (bids_root / "dataset_description.json").exists():
        ctx.write_text(bids_root / "dataset_description.json",
                       json.dumps(DATASET_DESCRIPTION, indent=2))
    if not (bids_root / ".bidsignore").exists():
        ctx.write_text(bids_root / ".bidsignore", BIDSIGNORE)

    if sub_dir.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = ctx.paths["work"] / "bids_replaced" / f"{sess.sub}_{stamp}"
        if ctx.dry_run:
            ctx.logger.info("[dry-run] would move %s to %s", sub_dir, backup)
        else:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(sub_dir), str(backup))
            ctx.logger.warning("moved previous %s to %s", sub_dir, backup)

    ctx.run_cmd(["dcm2bids", "-d", dicom_dir, "-p", sess.label, "-c", config_path,
                 "-o", bids_root, "--clobber", "--force_dcm2bids"])
    if ctx.dry_run:
        ctx.logger.info("[dry-run] series check passed; conversion and validation not run")
        return
    tmp_sub = bids_root / "tmp_dcm2bids" / sess.sub
    if tmp_sub.exists():
        shutil.rmtree(tmp_sub)   # leftovers = excluded series (localizers etc.)

    # ---- 3. validate -----------------------------------------------------
    _validate(ctx, expected)
    ctx.write_stamp(fp, {"dicom_dir": str(dicom_dir), "n_dicom_files": len(files),
                         "expected_table": str(table_path)})

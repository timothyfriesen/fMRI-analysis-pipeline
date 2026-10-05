"""Stage 01: expected-series check, dcm2bids config, BIDS validation, end to end."""
import json
import os
import sys
import textwrap
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from achi import cli
from achi.bids import dcm2bids_config as d2b
from achi.bids.series import Observed, check_series, load_expected
from achi.bids.validate import validate_subject
from achi.config import load_config
from synth import TASK_ORDER, v1_layout, write_session

REPO = Path(__file__).resolve().parents[1]
CFG = load_config(REPO / "config" / "pipeline.yaml")
TASKS = CFG["acquisition"]["tasks"]
TASK_RE = CFG["bids"]["task_series_regex"]
V1 = load_expected(REPO / "config" / "expected_series" / "v1.tsv", TASKS)
PILOT = load_expected(REPO / "config" / "expected_series" / "pilot.tsv", TASKS)
SMALL = {"quest": 4, "traintest1": 3, "traintest2": 3, "traintest3": 3, "mist1": 2, "mist2": 2}


def observed_from(layout, pe=True):
    out = []
    for series, desc, n, _ in layout:
        pe_dir = ("j" if series == 3 else "j-") if pe and series >= 3 else ""
        out.append(Observed(series, desc, n, pe_dir))
    return out


def good_layout(**kw):
    rows = v1_layout(**kw)
    # series 3 is the PA field map; give it a realistic name
    return [(3, "fMRI_fmap_PA", 1, r[3]) if r[0] == 3 else r for r in rows]


# ---- expected tables ------------------------------------------------------------
@pytest.mark.parametrize("table", [V1, PILOT])
def test_expected_table_shape(table):
    roles = [e.role for e in table]
    assert roles.count("bold") == 12 and roles.count("sbref") == 11
    assert roles.count("fmap_AP") == roles.count("fmap_PA") == roles.count("anat_T1w") == 1
    ap = next(e for e in table if e.role == "fmap_AP")
    assert (ap.series, ap.task, ap.run) == (5, "quest", 1)
    bolds = [(e.series, e.task, e.run) for e in table if e.role == "bold"]
    order = [(t, r) for r in (1, 2) for t in TASK_ORDER]
    assert [(t, r) for _, t, r in bolds] == order
    assert [s for s, _, _ in bolds] == list(range(6, 29, 2))
    assert {e.series for e in table if e.role == "ignore"} == {4, 29, 30}


def test_table_disagreeing_with_config_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="config acquisition.tasks"):
        load_expected(REPO / "config" / "expected_series" / "v1.tsv", {**TASKS, "quest": 180})


# ---- series check ---------------------------------------------------------------
def test_correct_session_passes():
    res = check_series(V1, observed_from(good_layout()), TASK_RE)
    assert res.ok, res.errors
    assert "fMRI_Task_quest_run-1" in res.table


def test_pilot_naming_needs_pilot_table():
    pilot = observed_from(good_layout(prefix="rfMRI", sbref_suffix="_SBRef"))
    assert check_series(PILOT, pilot, TASK_RE).ok
    res = check_series(V1, pilot, TASK_RE)
    assert not res.ok and any("does not match" in e for e in res.errors)


def test_repeated_scan_is_caught():
    # traintest1 run-1 (series 7/8) repeated: everything after shifts by 2
    rows = good_layout()
    shifted = []
    for s, d, n, it in rows:
        if s in (29, 30):
            continue
        shifted.append((s + 2 if s >= 9 else s, d, n, it))
        if s in (7, 8):
            shifted.append((s + 2, d, n, it))
    res = check_series(V1, observed_from(shifted), TASK_RE)
    assert not res.ok
    assert "UNEXPECTED" in res.table
    assert any("series 30 should be an excluded series" in e for e in res.errors)
    assert any("series 9" in e and "does not match" in e for e in res.errors)


def test_skipped_scan_is_caught():
    rows = [r for r in good_layout() if r[0] not in (27, 28)]   # traintest3 run-2 never ran
    res = check_series(V1, observed_from(rows), TASK_RE)
    assert not res.ok
    assert any("series 28" in e and "missing" in e for e in res.errors)


def test_aborted_run_is_caught():
    rows = [(s, d, 90 if s == 6 else n, it) for s, d, n, it in good_layout()]
    res = check_series(V1, observed_from(rows), TASK_RE)
    assert any("series 6" in e and "90 volumes, expected 182" in e for e in res.errors)


def test_wrong_phase_encoding_is_caught():
    obs = [Observed(o.series, o.description, o.n_vols, "j-" if o.series == 3 else o.pe_dir)
           for o in observed_from(good_layout())]
    res = check_series(V1, obs, TASK_RE)
    assert any("series 3" in e and "phase encoding" in e for e in res.errors)


# ---- dcm2bids config ------------------------------------------------------------
def test_dcm2bids_config():
    cfg = d2b.build_config(V1)
    all_ = cfg["descriptions"]
    # only BOLD carries ids (see module docstring: otherwise IntendedFor can come out empty)
    assert all(("id" in d) == (d["suffix"] == "bold") for d in all_)
    descs = {d.get("id") or f'{d["suffix"]}_{d.get("custom_entities", "")}': d for d in all_}
    assert len([d for d in all_ if d["suffix"] == "bold"]) == 12
    sbrefs = [d for d in all_ if d["suffix"] == "sbref"]
    assert len(sbrefs) == 11
    assert "task-quest_run-1" not in [d["custom_entities"] for d in sbrefs]
    assert descs["epi_dir-AP"]["criteria"] == {"SeriesNumber": 5,
                                               "SeriesDescription": "fMRI_Task_quest_run-1"}
    for d in ("AP", "PA"):
        fm = descs[f"epi_dir-{d}"]
        assert fm["sidecar_changes"]["IntendedFor"] == [x["id"] for x in all_ if x["suffix"] == "bold"]
    b = descs["bold_traintest2_run2"]
    assert b["custom_entities"] == "task-traintest2_run-2"
    assert b["sidecar_changes"]["TaskName"] == "traintest2"
    assert b["criteria"]["SeriesNumber"] == 26
    assert cfg["bids_uri"] == "relative"


# ---- BIDS validator on hand-built trees ---------------------------------------------
def _nii(path, n_vols):
    path.parent.mkdir(parents=True, exist_ok=True)
    shape = (2, 2, 2, n_vols) if n_vols > 1 else (2, 2, 2)
    nib.save(nib.Nifti1Image(np.zeros(shape, dtype=np.int16), np.eye(4)), path)


def make_bids(root, sub="sub-ACHI001", style="relative", tweak=None):
    (root / "dataset_description.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "dataset_description.json").write_text("{}")
    sd = root / sub
    _nii(sd / "anat" / f"{sub}_T1w.nii.gz", 1)
    bold_rel = []
    for e in V1:
        if e.role not in ("bold", "sbref"):
            continue
        name = f"{sub}_task-{e.task}_run-{e.run}_{e.role}"
        _nii(sd / "func" / f"{name}.nii.gz", e.n_vols if e.role == "bold" else 1)
        meta = {"TaskName": e.task, "RepetitionTime": 1.5, "PhaseEncodingDirection": "j-",
                "TotalReadoutTime": 0.05,
                "SliceTiming": list(np.linspace(0, 1.4, 72).round(4))}
        (sd / "func" / f"{name}.json").write_text(json.dumps(meta))
        if e.role == "bold":
            bold_rel.append(f"func/{name}.nii.gz")
    for d, pe in (("AP", "j-"), ("PA", "j")):
        _nii(sd / "fmap" / f"{sub}_dir-{d}_epi.nii.gz", 1)
        intended = bold_rel if style == "relative" else [f"bids::{sub}/{p}" for p in bold_rel]
        (sd / "fmap" / f"{sub}_dir-{d}_epi.json").write_text(json.dumps(
            {"PhaseEncodingDirection": pe, "TotalReadoutTime": 0.05, "IntendedFor": intended}))
    if tweak:
        tweak(sd)
    return root


def _edit(path, **changes):
    meta = json.loads(path.read_text())
    meta.update(changes)
    path.write_text(json.dumps(meta))


def _val(root):
    return validate_subject(root, "sub-ACHI001", V1, 1.5, 72)


@pytest.mark.parametrize("style", ["relative", "URI"])
def test_valid_tree_passes(tmp_path, style):
    v = _val(make_bids(tmp_path, style=style))
    assert v.ok, v.errors


@pytest.mark.parametrize("tweak,needle", [
    (lambda sd: _edit(sd / "fmap" / "sub-ACHI001_dir-AP_epi.json",
                      IntendedFor=["func\\sub-ACHI001_task-quest_run-1_bold.nii.gz"]), "backslashes"),
    (lambda sd: _edit(sd / "fmap" / "sub-ACHI001_dir-PA_epi.json",
                      IntendedFor=["func/sub-ACHI001_task-quest_run-9_bold.nii.gz"]), "missing file"),
    (lambda sd: _edit(sd / "fmap" / "sub-ACHI001_dir-PA_epi.json", IntendedFor=[]), "IntendedFor missing"),
    (lambda sd: _edit(sd / "func" / "sub-ACHI001_task-mist1_run-2_bold.json",
                      SliceTiming=[0, 500, 1000]), "SliceTiming values outside"),
    (lambda sd: _edit(sd / "func" / "sub-ACHI001_task-mist1_run-2_bold.json", TaskName=None), "TaskName"),
    (lambda sd: _edit(sd / "fmap" / "sub-ACHI001_dir-PA_epi.json", PhaseEncodingDirection="j-"),
     "PhaseEncodingDirection"),
    (lambda sd: _nii(sd / "func" / "sub-ACHI001_task-quest_run-1_sbref.nii.gz", 1), "unexpected SBRef"),
    (lambda sd: _nii(sd / "fmap" / "sub-ACHI001_acq-x_dir-AP_epi.nii.gz", 1), "exactly one dir-AP"),
    (lambda sd: (sd / "func" / "sub-ACHI001_task-quest_run-2_bold.nii.gz").unlink(), "missing BOLD"),
    (lambda sd: _nii(sd / "func" / "sub-ACHI001_task-quest_run-2_bold.nii.gz", 100), "100 volumes"),
])
def test_validator_catches(tmp_path, tweak, needle):
    v = _val(make_bids(tmp_path, tweak=tweak))
    assert not v.ok
    assert any(needle in e for e in v.errors), v.errors


# ---- end to end: synthetic DICOMs -> dcm2bids -> validation ------------------------
@pytest.fixture
def e2e(tmp_path, monkeypatch):
    """Small synthetic session + config whose tasks have few volumes."""
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    root = tmp_path / "project"
    table = tmp_path / "v1_small.tsv"
    text = (REPO / "config" / "expected_series" / "v1.tsv").read_text()
    lines = []
    for ln in text.splitlines():
        cols = ln.split("\t")
        if len(cols) == 7 and cols[1] == "bold":
            cols[5] = str(SMALL[cols[2]])
        lines.append("\t".join(cols))
    table.write_text("\n".join(lines) + "\n")
    sessions = tmp_path / "sessions.tsv"
    sessions.write_text("study_id\tbids_label\tsession_folder\tprotocol\n"
                        "ACHI_2026_001\t\tSESSION_001\tv1\n")
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text((REPO / "config" / "pipeline.yaml").read_text())
    tasks = "\n".join(f"    {k}: {v}" for k, v in SMALL.items())
    (tmp_path / "pipeline.local.yaml").write_text(textwrap.dedent(f"""
        paths:
          dicom_root: {root}/dicom
          project_root: {root}
          sessions_table: {sessions}
        bids:
          expected_series:
            v1: {table}
        acquisition:
          n_slices: 2
          tasks:
        """) + tasks + "\n")

    # Real Siemens sidecars carry these from the CSA header; the synthetic DICOMs
    # cannot, so add them through dcm2bids sidecar_changes for the test only.
    real_build = d2b.build_config

    def build_with_siemens_fields(expected, style="relative"):
        cfg_ = real_build(expected, style)
        for d in cfg_["descriptions"]:
            sc = d.setdefault("sidecar_changes", {})
            if d["datatype"] in ("func", "fmap"):
                pe = "j" if d.get("custom_entities") == "dir-PA" else "j-"
                sc.update(PhaseEncodingDirection=pe, TotalReadoutTime=0.05)
            if d["suffix"] == "bold":
                sc["SliceTiming"] = [0.0, 0.75]
        return cfg_
    monkeypatch.setattr("achi.stages.s01_bids.build_config", build_with_siemens_fields)

    def make(layout):
        return write_session(root / "dicom" / "SESSION_001", layout)
    return {"root": root, "cfg": cfg, "make": make}


def _run(e2e, *extra):
    return cli.main(["--config", str(e2e["cfg"]), "run", "01", "--sub", "ACHI001", *extra])


def test_e2e_convert_validate_and_idempotent(e2e, caplog):
    e2e["make"](good_layout(nvols=SMALL))
    bids = e2e["root"] / "bids"

    assert _run(e2e, "--dry-run") == 0               # series check runs, nothing written
    assert not bids.exists()

    assert _run(e2e) == 0
    sub = bids / "sub-ACHI001"
    assert len(list((sub / "func").glob("*_bold.nii.gz"))) == 12
    assert len(list((sub / "func").glob("*_sbref.nii.gz"))) == 11
    assert not (sub / "func" / "sub-ACHI001_task-quest_run-1_sbref.nii.gz").exists()
    ap = json.loads((sub / "fmap" / "sub-ACHI001_dir-AP_epi.json").read_text())
    assert all("/" in p and "\\" not in p for p in ap["IntendedFor"])
    assert nib.load(sub / "func" / "sub-ACHI001_task-quest_run-2_bold.nii.gz").shape[3] == 4
    bold_meta = json.loads((sub / "func" / "sub-ACHI001_task-mist2_run-1_bold.json").read_text())
    assert bold_meta["TaskName"] == "mist2"
    assert "PatientName" not in bold_meta and "PatientBirthDate" not in bold_meta
    assert not (bids / "tmp_dcm2bids" / "sub-ACHI001").exists()
    stamp = e2e["root"] / "derivatives" / "achi" / "sub-ACHI001" / "stamps" / "stage-01.json"
    assert stamp.is_file()
    logs = list((e2e["root"] / "logs" / "sub-ACHI001").glob("*stage-01*.log"))
    assert logs and "series check passed" in logs[0].read_text()

    mtime = (sub / "anat" / "sub-ACHI001_T1w.nii.gz").stat().st_mtime_ns
    assert _run(e2e) == 0                            # up to date: re-check only
    assert (sub / "anat" / "sub-ACHI001_T1w.nii.gz").stat().st_mtime_ns == mtime

    assert _run(e2e, "--force") == 0                 # reconvert, old copy kept
    assert len(list((e2e["root"] / "work" / "bids_replaced").iterdir())) == 1


def test_e2e_repeated_scan_stops_before_conversion(e2e, capsys):
    rows = good_layout(nvols=SMALL)
    rows.append((31, "fMRI_Task_mist1_run-2", SMALL["mist1"], rows[-1][3]))  # extra repeat at the end
    e2e["make"](rows)
    assert _run(e2e) == 1
    assert not (e2e["root"] / "bids" / "sub-ACHI001").exists()
    err = capsys.readouterr().err
    assert "series 31" in err and "repeated or added" in err


def test_e2e_existing_subject_needs_force(e2e):
    e2e["make"](good_layout(nvols=SMALL))
    (e2e["root"] / "bids" / "sub-ACHI001").mkdir(parents=True)
    assert _run(e2e) == 1


def test_cli_prints_dcm2bids_config(capsys):
    assert cli.main(["dcm2bids-config", "pilot"]) == 0
    cfg = json.loads(capsys.readouterr().out)
    assert any(d["criteria"]["SeriesDescription"] == "rfMRI_Task_quest_run-1_SBRef"
               for d in cfg["descriptions"])

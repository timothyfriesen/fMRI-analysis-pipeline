"""Stage 0: config, sessions table, CLI, stamps, de-identified series table."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from achi import cli
from achi.config import ConfigError, load_config
from achi.context import RunContext, StageError, fingerprint
from achi.dicom_table import scan_session, to_tsv
from achi.sessions import SessionError, derive_label, find_session, load_sessions
from synth import v1_layout, write_series, write_session

REPO = Path(__file__).resolve().parents[1]


# ---- config ---------------------------------------------------------------
def test_repo_config_loads_and_resolves_paths():
    cfg = load_config(REPO / "config" / "pipeline.yaml")
    assert str(cfg.paths["bids"]) == "/data/benlab/datalake/afch/achi/fmriprep/bids"
    assert str(cfg.paths["fmriprep_out"]).endswith("fmriprep/derivatives/fmriprep")
    assert cfg.paths["sessions_table"] == REPO / "config" / "sessions.tsv"
    assert cfg["software"]["fmriprep_version"] == "25.2.6"
    assert sum(1 for _ in cfg["acquisition"]["tasks"]) == 6


def test_local_override_is_merged(project):
    cfg = load_config(project["config"])
    assert cfg.paths["bids"] == project["root"] / "bids"
    assert cfg["compute"]["n_threads"] == 8  # untouched values survive the merge


def test_bad_compute_settings_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    text = (REPO / "config" / "pipeline.yaml").read_text().replace("omp_threads: 4", "omp_threads: 16")
    p.write_text(text)
    with pytest.raises(ConfigError, match="omp_threads"):
        load_config(p)


def test_missing_section_reported(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("study: {name: x}\n")
    with pytest.raises(ConfigError, match="missing required settings"):
        load_config(p)


# ---- sessions -------------------------------------------------------------
@pytest.mark.parametrize("sid,label", [("ACHI_2026_001", "ACHI001"), ("ACHI_2027_123", "ACHI123")])
def test_label_rule(sid, label):
    cfg = load_config(REPO / "config" / "pipeline.yaml")
    assert derive_label(sid, cfg["study"]["label_regex"], cfg["study"]["label_format"]) == label


def test_sessions_table(project):
    cfg = load_config(project["config"])
    ss = load_sessions(cfg.paths["sessions_table"], cfg["study"]["label_regex"], cfg["study"]["label_format"])
    assert [s.sub for s in ss] == ["sub-01", "sub-ACHI001"]
    assert find_session(ss, "sub-ACHI001").study_id == "ACHI_2026_001"
    assert find_session(ss, "ACHI_2026_001").label == "ACHI001"
    with pytest.raises(SessionError, match="not in sessions table"):
        find_session(ss, "ACHI999")


def test_non_alphanumeric_label_rejected(tmp_path):
    p = tmp_path / "s.tsv"
    p.write_text("study_id\tbids_label\tsession_folder\tprotocol\nX\tACHI_001\tf\tv1\n")
    with pytest.raises(SessionError, match="alphanumeric"):
        load_sessions(p, r"^x$", "{x}")


def test_duplicate_label_rejected(tmp_path):
    p = tmp_path / "s.tsv"
    p.write_text("study_id\tbids_label\tsession_folder\tprotocol\nA\tX1\tf\tv1\nB\tX1\tg\tv1\n")
    with pytest.raises(SessionError, match="duplicate"):
        load_sessions(p, r"^x$", "{x}")


# ---- context / stamps -------------------------------------------------------
def _ctx(project, **kw):
    cfg = load_config(project["config"])
    ss = load_sessions(cfg.paths["sessions_table"], cfg["study"]["label_regex"], cfg["study"]["label_format"])
    return RunContext(cfg, ss[1], "01", **kw)


def test_stamp_roundtrip_and_force(project, tmp_path):
    f = tmp_path / "input.txt"
    f.write_text("a")
    fp = fingerprint([f], {"x": 1})
    ctx = _ctx(project)
    assert not ctx.is_up_to_date(fp)
    ctx.write_stamp(fp)
    assert ctx.is_up_to_date(fp)
    assert not ctx.is_up_to_date(fingerprint([f], {"x": 2}))      # settings changed
    assert not _ctx(project, force=True).is_up_to_date(fp)          # --force
    assert json.loads(ctx.stamp_file.read_text())["subject"] == "sub-ACHI001"


def test_dry_run_changes_nothing(project):
    ctx = _ctx(project, dry_run=True)
    target = project["root"] / "x" / "y.txt"
    ctx.write_text(target, "hello")
    ctx.mkdir(project["root"] / "newdir")
    ctx.write_stamp("abc")
    assert ctx.run_cmd(["false"]) is None
    assert not target.exists() and not (project["root"] / "newdir").exists()
    assert not ctx.stamp_file.exists()


def test_failed_command_raises(project):
    with pytest.raises(StageError, match="exit 1"):
        _ctx(project).run_cmd(["false"])


def test_host_guard(project):
    ctx = _ctx(project)
    ctx.cfg["compute"]["allowed_hosts"] = ["some-other-host"]
    with pytest.raises(StageError, match="allowed_hosts"):
        ctx.check_host()


# ---- de-identified series table ----------------------------------------------
def test_series_table_counts_and_hides_phi(tmp_path):
    sess = write_session(tmp_path / "sess", v1_layout(scale_vols=50))
    (tmp_path / "sess" / "README.txt").write_text("not a dicom")
    rows = scan_session(sess)
    assert [r.series for r in rows] == list(range(1, 31))
    by = {r.series: r for r in rows}
    assert by[5].description == by[6].description == "fMRI_Task_quest_run-1"
    assert (by[5].n_files, by[6].n_files) == (2, 2 * (182 // 50))  # 2 slices per volume
    assert by[6].tr_ms == "1500" and by[6].te_ms == "37"
    tsv = to_tsv(rows)
    for secret in ("SECRET", "19990101", "20260527"):
        assert secret not in tsv


def test_series_table_splits_repeated_series_number(tmp_path):
    # Same series number, different SeriesInstanceUID (e.g. a re-exported series)
    write_series(tmp_path / "s", 6, "fMRI_Task_quest_run-1", 2)
    write_series(tmp_path / "s" / "again", 6, "fMRI_Task_quest_run-1", 3)
    assert sorted(r.n_files for r in scan_session(tmp_path / "s")) == [4, 6]


# ---- CLI ------------------------------------------------------------------------
def test_cli_stages_and_unimplemented(project, capsys):
    assert cli.main(["stages"]) == 0
    assert "fMRIPrep" in capsys.readouterr().out
    rc = cli.main(["--config", str(project["config"]), "run", "08", "--sub", "ACHI001", "--dry-run"])
    assert rc == 2


def test_cli_unknown_subject_and_stage(project, capsys):
    assert cli.main(["--config", str(project["config"]), "run", "01", "--sub", "NOPE"]) == 1
    assert "not in sessions table" in capsys.readouterr().err
    assert cli.main(["--config", str(project["config"]), "run", "42", "--sub", "ACHI001"]) == 1


def test_run_stage_sh_help():
    env = {**os.environ, "ACHI_PYTHON": sys.executable}
    out = subprocess.run(["bash", str(REPO / "bin" / "run_stage.sh"), "--help"],
                         capture_output=True, text=True, check=True, env=env).stdout
    assert "--detach" in out and "01" in out

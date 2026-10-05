import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def project(tmp_path):
    """A throwaway project tree + config pointing into it."""
    root = tmp_path / "project"
    (root / "dicom").mkdir(parents=True)
    sessions = tmp_path / "sessions.tsv"
    sessions.write_text(
        "study_id\tbids_label\tsession_folder\tprotocol\tgroup\tnotes\n"
        "PILOT\t01\t20250101_x_PILOT\tpilot\t\t\n"
        "ACHI_2026_001\t\t20260527_x_ACHI_2026_001\tv1\tHC\t\n")
    base = (REPO / "config" / "pipeline.yaml").read_text()
    cfg = tmp_path / "pipeline.yaml"
    cfg.write_text(base)
    (tmp_path / "pipeline.local.yaml").write_text(textwrap.dedent(f"""
        paths:
          dicom_root: {root}/dicom
          project_root: {root}
          sessions_table: {sessions}
        """))
    return {"root": root, "config": cfg, "sessions": sessions}

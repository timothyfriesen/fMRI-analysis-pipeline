"""Stage registry. Each stage module exposes `run(ctx: RunContext) -> None`."""
from __future__ import annotations

import importlib

STAGES: dict[str, tuple[str, str]] = {
    # id: (module, description)
    "01": ("s01_bids", "DICOM -> BIDS (series check, dcm2bids, BIDS validation)"),
    "02": ("s02_fmriprep", "fMRIPrep 25.2.6 in Apptainer/Singularity + SDC check"),
    "03": ("s03_qc", "QC table: FD, spikes, SDC, non-steady-state, flags"),
    "04": ("s04_spmprep", "Unzip MNI BOLD, smooth, confound .mat files"),
    "05": ("s05_events", "Task logs -> events.tsv + SPM onsets (HGF pmod hook)"),
    "06": ("s06_glm", "First-level SPM GLM batches + contrasts"),
    "07": ("s07_roi", "ROI extraction -> tidy CSV"),
    "08": ("s08_figures", "Figures: motion QC, maps, ROI plots"),
}


def normalize(stage: str) -> str:
    s = stage.strip().zfill(2)
    if s not in STAGES:
        raise KeyError(f"unknown stage {stage!r}; choose from {', '.join(STAGES)}")
    return s


def get_runner(stage: str):
    module, _ = STAGES[normalize(stage)]
    try:
        mod = importlib.import_module(f"{__name__}.{module}")
    except ModuleNotFoundError as exc:
        if exc.name == f"{__name__}.{module}":
            return None  # not implemented yet
        raise
    return mod.run

"""Synthetic test data. No real participant data is ever used in tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

# (series, description, n_files, image_type) for a real-protocol ACHI session.
TASK_ORDER = ["quest", "traintest1", "mist1", "mist2", "traintest2", "traintest3"]
NVOLS = {"quest": 182, "traintest1": 150, "traintest2": 150, "traintest3": 150,
         "mist1": 86, "mist2": 86}


def v1_layout(scale_vols: int = 1) -> list[tuple[int, str, int, str]]:
    """Series layout of a real (v1) session. Divide volume counts by scale_vols
    to keep tests fast."""
    rows = [(1, "localizer", 3, "ORIGINAL\\PRIMARY\\M\\ND"),
            (2, "anat_T1w_mprage", 4, "ORIGINAL\\PRIMARY\\M\\ND\\NORM"),
            (3, "fMRI_fmap_PA", 1, "ORIGINAL\\PRIMARY\\M\\ND\\MOSAIC"),
            (4, "anat_T1w_mprage_extra", 2, "DERIVED\\PRIMARY\\M\\ND")]
    series = 5
    for run in (1, 2):
        for task in TASK_ORDER:
            desc = f"fMRI_Task_{task}_run-{run}"
            rows.append((series, desc, 1, "ORIGINAL\\PRIMARY\\M\\ND\\MOSAIC"))       # SBRef
            rows.append((series + 1, desc, max(1, NVOLS[task] // scale_vols),
                         "ORIGINAL\\PRIMARY\\M\\ND\\MOSAIC"))                          # BOLD
            series += 2
    rows += [(29, "PhysioLog", 1, "ORIGINAL\\PRIMARY\\RAWDATA\\PHYSIO"),
             (30, "fMRI_extra", 1, "DERIVED\\PRIMARY\\M\\ND")]
    return rows


def write_series(folder: Path, series: int, description: str, n_files: int,
                 image_type: str = "ORIGINAL\\PRIMARY\\M\\ND", tr_ms: float = 1500.0,
                 te_ms: float = 37.0) -> None:
    """Write n_files tiny DICOMs for one series, with fake PHI to prove it is not leaked."""
    sdir = folder / f"{series:04d}_{description}"
    sdir.mkdir(parents=True, exist_ok=True)
    uid = generate_uid()
    for i in range(n_files):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = MRImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = FileDataset(str(sdir / f"{i:05d}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.is_little_endian, ds.is_implicit_VR = True, False
        ds.SOPClassUID = MRImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.PatientName = "SECRET^PATIENT"
        ds.PatientID = "SECRET-ID-123"
        ds.PatientBirthDate = "19990101"
        ds.InstitutionName = "SECRET HOSPITAL"
        ds.StudyDate = ds.SeriesDate = "20260527"
        ds.Modality = "MR"
        ds.SeriesNumber = series
        ds.SeriesDescription = description
        ds.ProtocolName = description
        ds.SeriesInstanceUID = uid
        ds.ImageType = image_type.split("\\")
        ds.RepetitionTime = tr_ms
        ds.EchoTime = te_ms
        ds.InstanceNumber = i + 1
        ds.Rows = ds.Columns = 4
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
        ds.PixelData = np.zeros((4, 4), dtype=np.uint16).tobytes()
        ds.save_as(sdir / f"{i:05d}.dcm", write_like_original=False)


def write_session(folder: Path, layout: list[tuple[int, str, int, str]]) -> Path:
    for series, desc, n, it in layout:
        write_series(folder, series, desc, n, it)
    return folder

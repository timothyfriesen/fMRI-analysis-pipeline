"""Synthetic test data. No real participant data is ever used in tests."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, MRImageStorage, generate_uid

# (series, description, n_vols, image_type) for a real-protocol ACHI session.
TASK_ORDER = ["quest", "traintest1", "mist1", "mist2", "traintest2", "traintest3"]
NVOLS = {"quest": 182, "traintest1": 150, "traintest2": 150, "traintest3": 150,
         "mist1": 86, "mist2": 86}


def v1_layout(scale_vols: int = 1, nvols: dict | None = None,
              prefix: str = "fMRI", sbref_suffix: str = "") -> list[tuple[int, str, int, str]]:
    """Series layout of a real (v1) session; pilot naming via prefix/sbref_suffix.
    Volume counts come from `nvols` (default NVOLS) divided by scale_vols."""
    nvols = nvols or NVOLS
    rows = [(1, "localizer", 1, "ORIGINAL\\PRIMARY\\M\\ND"),
            (2, "anat_T1w_mprage", 1, "ORIGINAL\\PRIMARY\\M\\ND\\NORM"),
            (3, "fMRI_fmap_PA", 1, "ORIGINAL\\PRIMARY\\M\\ND\\MOSAIC"),
            (4, "anat_T1w_mprage_extra", 1, "DERIVED\\PRIMARY\\M\\ND")]
    series = 5
    for run in (1, 2):
        for task in TASK_ORDER:
            desc = f"{prefix}_Task_{task}_run-{run}"
            rows.append((series, desc + sbref_suffix, 1, "ORIGINAL\\PRIMARY\\M\\ND\\MOSAIC"))  # SBRef
            rows.append((series + 1, desc, max(1, nvols[task] // scale_vols),
                         "ORIGINAL\\PRIMARY\\M\\ND\\MOSAIC"))                          # BOLD
            series += 2
    rows += [(29, "PhysioLog", 1, "ORIGINAL\\PRIMARY\\RAWDATA\\PHYSIO"),
             (30, "fMRI_extra", 1, "DERIVED\\PRIMARY\\M\\ND")]
    return rows


def write_series(folder: Path, series: int, description: str, n_vols: int,
                 image_type: str = "ORIGINAL\\PRIMARY\\M\\ND", tr_ms: float = 1500.0,
                 te_ms: float = 37.0, n_slices: int = 2) -> None:
    """Write one series as 2-D slice DICOMs (n_vols x n_slices files) that dcm2niix
    can stack into a 3-D/4-D NIfTI. Includes fake PHI to prove it never leaks."""
    sdir = folder / f"{series:04d}_{description}"
    sdir.mkdir(parents=True, exist_ok=True)
    uid, study_uid, frame_uid = generate_uid(), "1.2.3.4.5.6", "1.2.3.4.5.7"
    for v in range(n_vols):
        for z in range(n_slices):
            i = v * n_slices + z
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
            ds.StudyDate = ds.SeriesDate = ds.AcquisitionDate = "20260527"
            ds.StudyTime = "100000"
            ds.SeriesTime = f"10{series:02d}00"
            ds.AcquisitionTime = f"10{series:02d}{v % 60:02d}.{z:03d}"
            ds.Modality = "MR"
            ds.Manufacturer = "Generic"
            ds.StudyInstanceUID = study_uid
            ds.FrameOfReferenceUID = frame_uid
            ds.SeriesNumber = series
            ds.SeriesDescription = description
            ds.ProtocolName = description
            ds.SeriesInstanceUID = uid
            ds.ImageType = image_type.split("\\")
            ds.RepetitionTime = tr_ms
            ds.EchoTime = te_ms
            ds.AcquisitionNumber = v + 1
            ds.TemporalPositionIdentifier = v + 1
            ds.InstanceNumber = i + 1
            ds.ImagePositionPatient = [0.0, 0.0, float(2 * z)]
            ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
            ds.PixelSpacing = [2.0, 2.0]
            ds.SliceThickness = 2.0
            ds.SliceLocation = float(2 * z)
            ds.Rows = ds.Columns = 4
            ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
            ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 0
            ds.PixelData = (np.arange(16, dtype=np.uint16) + i).tobytes()
            ds.save_as(sdir / f"{i:05d}.dcm", write_like_original=False)


def write_session(folder: Path, layout: list[tuple[int, str, int, str]]) -> Path:
    for series, desc, n, it in layout:
        write_series(folder, series, desc, n, it, n_slices=3 if series == 2 else 2)
    return folder

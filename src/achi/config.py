"""Load and validate config/pipeline.yaml (+ optional pipeline.local.yaml)."""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "pipeline.yaml"

REQUIRED = {
    "study": ["name", "label_regex", "label_format"],
    "paths": ["dicom_root", "project_root", "bids", "derivatives", "work",
              "containers", "templateflow", "fs_license", "logs", "sessions_table"],
    "compute": ["n_threads", "omp_threads", "mem_gb"],
    "software": ["fmriprep_version"],
    "acquisition": ["tr", "tasks", "runs_per_task"],
}


class ConfigError(ValueError):
    pass


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _resolve_paths(paths: dict, repo_root: Path) -> dict[str, Path]:
    """Expand {other_key} references and make relative paths repo-relative."""
    raw = {k: str(v) for k, v in paths.items()}
    for _ in range(len(raw) + 1):
        changed = False
        for k, v in raw.items():
            refs = re.findall(r"\{(\w+)\}", v)
            for ref in refs:
                if ref not in raw:
                    raise ConfigError(f"paths.{k} references unknown key {{{ref}}}")
                if "{" not in raw[ref]:
                    v = v.replace("{" + ref + "}", raw[ref])
                    changed = True
            raw[k] = v
        if not changed:
            break
    unresolved = {k: v for k, v in raw.items() if "{" in v}
    if unresolved:
        raise ConfigError(f"circular path references: {unresolved}")
    out = {}
    for k, v in raw.items():
        p = Path(os.path.expandvars(os.path.expanduser(v)))
        out[k] = p if p.is_absolute() else (repo_root / p)
    return out


class Config(dict):
    """Plain dict of the YAML plus `.paths` (resolved Path objects)."""

    paths: dict[str, Path]
    source: list[Path]


def load_config(path: str | os.PathLike | None = None,
                repo_root: Path = REPO_ROOT) -> Config:
    path = Path(path or os.environ.get("ACHI_CONFIG") or DEFAULT_CONFIG)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    sources = [path]
    local = path.with_name(path.stem + ".local" + path.suffix)
    if local.is_file():
        data = _deep_merge(data, yaml.safe_load(local.read_text()) or {})
        sources.append(local)

    missing = [f"{sec}.{key}" for sec, keys in REQUIRED.items()
               for key in keys if key not in (data.get(sec) or {})]
    if missing:
        raise ConfigError(f"{path}: missing required settings: {', '.join(missing)}")

    for key in ("n_threads", "omp_threads", "mem_gb"):
        val = data["compute"][key]
        if not isinstance(val, int) or val < 1:
            raise ConfigError(f"compute.{key} must be a positive integer, got {val!r}")
    if data["compute"]["omp_threads"] > data["compute"]["n_threads"]:
        raise ConfigError("compute.omp_threads cannot exceed compute.n_threads")

    cfg = Config(data)
    cfg.paths = _resolve_paths(data["paths"], repo_root)
    cfg.source = sources
    return cfg

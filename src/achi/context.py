"""Shared machinery for every stage: logging, dry-run, command execution, stamps.

Idempotency: a stage that finishes writes a stamp file
    <pipeline_out>/<sub>/stamps/stage-NN.json
holding a fingerprint of its inputs and settings. On rerun the stage is skipped
when the fingerprint is unchanged, unless --force is given.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import shlex
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .config import Config
from .sessions import Session


class StageError(RuntimeError):
    """A stage found a problem it must not continue past. Message is user-facing."""


def setup_logger(name: str, log_file: Path | None, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


def fingerprint(files: Iterable[Path] = (), settings: object = None) -> str:
    """Hash of file paths + sizes + mtimes (cheap) and a JSON-able settings object."""
    h = hashlib.sha256()
    for f in sorted(Path(p) for p in files):
        st = f.stat()
        h.update(f"{f}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    h.update(json.dumps(settings, sort_keys=True, default=str).encode())
    return h.hexdigest()


@dataclass
class RunContext:
    cfg: Config
    session: Session
    stage: str
    dry_run: bool = False
    force: bool = False
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("achi"))

    # ---- paths -----------------------------------------------------------
    @property
    def paths(self) -> dict[str, Path]:
        return self.cfg.paths

    @property
    def stamp_file(self) -> Path:
        return self.paths["pipeline_out"] / self.session.sub / "stamps" / f"stage-{self.stage}.json"

    # ---- host guard ------------------------------------------------------
    def check_host(self) -> None:
        allowed = self.cfg["compute"].get("allowed_hosts") or []
        host = socket.gethostname().split(".")[0]
        if allowed and host not in allowed:
            raise StageError(
                f"this host ({host}) is not in compute.allowed_hosts {allowed}. "
                "Run on an allowed workstation or edit config/pipeline.local.yaml.")

    # ---- stamps ----------------------------------------------------------
    def is_up_to_date(self, fp: str) -> bool:
        if self.force or not self.stamp_file.is_file():
            return False
        try:
            return json.loads(self.stamp_file.read_text()).get("fingerprint") == fp
        except (OSError, json.JSONDecodeError):
            return False

    def write_stamp(self, fp: str, extra: dict | None = None) -> None:
        if self.dry_run:
            self.logger.info("[dry-run] would write stamp %s", self.stamp_file)
            return
        self.stamp_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {"stage": self.stage, "subject": self.session.sub,
                   "fingerprint": fp, "host": socket.gethostname(),
                   "finished": dt.datetime.now().isoformat(timespec="seconds")}
        payload.update(extra or {})
        self.stamp_file.write_text(json.dumps(payload, indent=2))

    # ---- side effects that respect --dry-run -----------------------------
    def run_cmd(self, cmd: Sequence[str], **kwargs) -> subprocess.CompletedProcess | None:
        """Run an external command, streaming its output into the log."""
        line = shlex.join(str(c) for c in cmd)
        if self.dry_run:
            self.logger.info("[dry-run] would run: %s", line)
            return None
        self.logger.info("running: %s", line)
        proc = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, **kwargs)
        assert proc.stdout is not None
        for out_line in proc.stdout:
            self.logger.debug("  | %s", out_line.rstrip())
        rc = proc.wait()
        if rc != 0:
            raise StageError(f"command failed (exit {rc}): {line}\nsee the log file for its output")
        return subprocess.CompletedProcess(cmd, rc)

    def mkdir(self, path: Path) -> None:
        if self.dry_run:
            if not path.exists():
                self.logger.info("[dry-run] would create %s", path)
            return
        path.mkdir(parents=True, exist_ok=True)

    def write_text(self, path: Path, text: str) -> None:
        if self.dry_run:
            self.logger.info("[dry-run] would write %s (%d bytes)", path, len(text))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

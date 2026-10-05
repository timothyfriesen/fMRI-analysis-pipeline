"""Command-line entry point.

    achi run 01 --sub ACHI001 [--dry-run] [--force] [-v]
    achi stages
    achi check-config
    achi series-table /path/to/dicom/session   (de-identified series list)
    achi dcm2bids-config v1                   (config generated from the expected table)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .context import RunContext, StageError, setup_logger
from .sessions import SessionError, find_session, load_sessions
from .stages import STAGES, get_runner, normalize


def _cmd_stages(_args) -> int:
    for sid, (_, desc) in STAGES.items():
        status = "ready" if get_runner(sid) else "not implemented yet"
        print(f"{sid}  {desc}  [{status}]")
    return 0


def _cmd_check_config(args) -> int:
    cfg = load_config(args.config)
    print("config files:", ", ".join(str(p) for p in cfg.source))
    width = max(len(k) for k in cfg.paths)
    for key, path in cfg.paths.items():
        mark = "ok" if path.exists() else "MISSING"
        print(f"  {key:<{width}}  {path}  [{mark}]")
    c = cfg["compute"]
    print(f"compute: {c['n_threads']} threads, {c['omp_threads']} per process, "
          f"{c['mem_gb']} GB, allowed hosts: {c.get('allowed_hosts') or 'any'}")
    try:
        sessions = load_sessions(cfg.paths["sessions_table"], cfg["study"]["label_regex"],
                                 cfg["study"]["label_format"])
        print(f"sessions: {len(sessions)} -> " + ", ".join(s.sub for s in sessions))
    except SessionError as exc:
        print(f"sessions: {exc}")
    return 0


def _cmd_series_table(args) -> int:
    from .dicom_table import scan_session, to_tsv
    folder = Path(args.folder)
    if not folder.is_absolute() and not folder.exists():
        folder = load_config(args.config).paths["dicom_root"] / folder
    sys.stdout.write(to_tsv(scan_session(folder)))
    return 0


def _cmd_dcm2bids_config(args) -> int:
    import json

    from .bids.dcm2bids_config import build_config
    from .bids.series import load_expected
    from .config import REPO_ROOT
    cfg = load_config(args.config)
    table = Path(cfg["bids"]["expected_series"][args.protocol])
    table = table if table.is_absolute() else REPO_ROOT / table
    expected = load_expected(table, cfg["acquisition"]["tasks"])
    print(json.dumps(build_config(expected, cfg["bids"].get("intendedfor_style", "relative")),
                     indent=2))
    return 0


def _cmd_run(args) -> int:
    cfg = load_config(args.config)
    stage = normalize(args.stage)
    sessions = load_sessions(cfg.paths["sessions_table"], cfg["study"]["label_regex"],
                             cfg["study"]["label_format"])
    session = find_session(sessions, args.sub)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = None if args.dry_run else (
        cfg.paths["logs"] / session.sub / f"{session.sub}_stage-{stage}_{stamp}.log")
    logger = setup_logger(f"achi.{stage}.{session.label}", log_file, args.verbose)
    ctx = RunContext(cfg, session, stage, dry_run=args.dry_run, force=args.force, logger=logger)

    runner = get_runner(stage)
    if runner is None:
        logger.error("stage %s (%s) is not implemented yet", stage, STAGES[stage][1])
        return 2
    logger.info("stage %s for %s (%s)%s", stage, session.sub, session.study_id,
                " [DRY RUN]" if args.dry_run else "")
    if log_file:
        logger.info("log file: %s", log_file)
    try:
        ctx.check_host()
        runner(ctx)
    except StageError as exc:
        logger.error("STOPPED: %s", exc)
        return 1
    logger.info("stage %s finished for %s", stage, session.sub)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="achi", description="ACHI fMRI pipeline")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", help="config YAML (default: config/pipeline.yaml or $ACHI_CONFIG)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run one stage for one subject")
    r.add_argument("stage", help="stage number, e.g. 01")
    r.add_argument("--sub", required=True, help="BIDS label or study ID, e.g. ACHI001")
    r.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    r.add_argument("--force", action="store_true", help="rerun even if up to date")
    r.add_argument("-v", "--verbose", action="store_true", help="show command output on screen")
    r.set_defaults(func=_cmd_run)

    sub.add_parser("stages", help="list stages").set_defaults(func=_cmd_stages)
    sub.add_parser("check-config", help="show resolved paths and sessions").set_defaults(
        func=_cmd_check_config)
    t = sub.add_parser("series-table", help="de-identified list of series in a DICOM folder")
    t.add_argument("folder", help="session folder (absolute, or relative to paths.dicom_root)")
    t.set_defaults(func=_cmd_series_table)
    c = sub.add_parser("dcm2bids-config", help="print the dcm2bids config generated for a protocol")
    c.add_argument("protocol", nargs="?", default="v1", help="v1 or pilot")
    c.set_defaults(func=_cmd_dcm2bids_config)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, SessionError, KeyError, FileNotFoundError) as exc:
        msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        print(f"error: {msg}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

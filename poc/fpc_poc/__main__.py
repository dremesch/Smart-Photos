"""POC command line.

  python -m fpc_poc run    --event "Japan 2024" D:\\OneDrive\\Pictures\\Japan2024 [--auto-approve 20] [--out D:\\Prints]
  python -m fpc_poc review --event "Japan 2024"            # PySide6 review UI
  python -m fpc_poc crops  --event "Japan 2024"
  python -m fpc_poc export --event "Japan 2024" --out D:\\Prints
  python -m fpc_poc status --event "Japan 2024"
  python -m fpc_poc integrity --event "Japan 2024"
  python -m fpc_poc summary --event "Japan 2024"          # aggregate-only JSON for agents

Output is aggregate-only (issue #13, D6): counts and timings, never paths, file names,
capture times, hashes or exception messages. An uncaught exception prints only its class
and an error code; a message-free traceback goes to <data>/logs/crash.log.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections import Counter
from pathlib import Path

from . import db, guard, pipeline, quiet
from .config import Paths, default_data_dir

EXIT_UNCAUGHT = 3
_data_dir: Path | None = None  # set once arguments are parsed, for the crash log


def _p(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fpc_poc")
    ap.add_argument("--data", type=Path, default=default_data_dir(), help="app data dir (not in OneDrive)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, **kw):
        s = sub.add_parser(name, **kw)
        s.add_argument("--event", required=True)
        s.add_argument("--workers", type=int, default=None)
        return s

    r = add("run", help="scan + ingest + analyze + group/rank (+ optional crops/export)")
    r.add_argument("roots", nargs="+", type=Path)
    r.add_argument("--target", type=int)
    r.add_argument("--auto-approve", type=int, default=0, help="DEMO: approve top-N recommendations")
    r.add_argument("--out", type=Path, help="also export to this folder")
    r.add_argument("--allow-onedrive", action="store_true")
    add("rank", help="re-run grouping/ranking (respects decisions)").add_argument("--target", type=int)
    add("crops")
    e = add("export")
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--allow-onedrive", action="store_true")
    add("status")
    add("integrity")
    add("review")
    add("summary", help="aggregate-only JSON: counts and timings, for agents")
    a = ap.parse_args(argv)

    global _data_dir
    _data_dir = a.data
    guard.block()  # nothing in this app talks to the network
    paths = Paths(a.data)
    con = db.connect(paths.db)

    if a.cmd == "run":
        _p({"scan": pipeline.scan(con, a.event, a.roots)})
        _p({"ingest": pipeline.ingest(con, paths, a.event, a.workers)})
        _p({"features": pipeline.analyze(con, paths, a.event, a.workers)})
        _p({"rank": pipeline.group_and_rank(con, a.event, a.target)})
        if a.auto_approve:
            recs = con.execute(
                "SELECT s.photo_id FROM scores s JOIN photos p ON p.id=s.photo_id WHERE p.event=? "
                "AND s.recommended=1 ORDER BY s.rec_order LIMIT ?", (a.event, a.auto_approve)).fetchall()
            state = db.review_state(con, a.event)
            for row in recs:
                if row[0] not in state:
                    db.decide(con, row[0], "approve", source="poc_auto")
        _p({"crops": pipeline.propose_crops(con, a.event)})
        if a.out:
            _p({"export": pipeline.export(con, a.event, a.out, a.workers, a.allow_onedrive)})
        _p({"integrity": pipeline.integrity(con, a.event, paths.root / "reports")})
    elif a.cmd == "rank":
        _p(pipeline.group_and_rank(con, a.event, a.target))
    elif a.cmd == "crops":
        _p(pipeline.propose_crops(con, a.event))
    elif a.cmd == "export":
        _p(pipeline.export(con, a.event, a.out, a.workers, a.allow_onedrive))
    elif a.cmd == "integrity":
        res = pipeline.integrity(con, a.event, paths.root / "reports")
        _p(res)
        return 1 if res["changed"] else 0
    elif a.cmd == "summary":
        _p(pipeline.summary(con, a.event))
    elif a.cmd == "status":
        st = Counter(r[0] for r in con.execute("SELECT status FROM photos WHERE event=?", (a.event,)))
        dec = Counter(db.review_state(con, a.event).values())
        risk = Counter(r[0] for r in con.execute(
            "SELECT c.risk FROM crops c JOIN photos p ON p.id=c.photo_id WHERE p.event=?", (a.event,)))
        _p({"photos": st, "decisions": dec, "crops": risk})
    elif a.cmd == "review":
        from .ui import run_ui

        return run_ui(con, paths, a.event)
    return 0


def _crash_log(exc: BaseException, code: str) -> None:
    """Class, code and frames (package-relative file, line, function). Never the message,
    never an absolute path: third-party messages (Pillow, pillow-heif, OSError) carry paths."""
    if _data_dir is None:
        return
    lines = [f"class={type(exc).__name__} code={code}"]
    for fr in traceback.extract_tb(exc.__traceback__):
        parts = Path(fr.filename).parts
        name = "/".join(parts[parts.index("fpc_poc"):]) if "fpc_poc" in parts else Path(fr.filename).name
        lines.append(f"  {name}:{fr.lineno} in {fr.name}")
    try:
        logs = _data_dir / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "crash.log", "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def cli(argv: list[str] | None = None) -> int:
    """Entry point with the top-level handler: class and code only, on stderr."""
    quiet.install_main()  # library warnings print category and code, never a module path
    try:
        return main(argv)
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - last line of defence against leaking messages
        code = "E_INTERRUPTED" if isinstance(e, KeyboardInterrupt) else "E_UNCAUGHT"
        _crash_log(e, code)
        print(f"fpc_poc: error class={type(e).__name__} code={code}", file=sys.stderr)
        return EXIT_UNCAUGHT


if __name__ == "__main__":
    sys.exit(cli())

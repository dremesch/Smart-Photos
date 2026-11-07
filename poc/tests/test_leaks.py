"""Leak and canary tests (issue #13, A-11; D6, D7, S2-D11).

A synthetic event is generated with a canary token planted in a folder name, file
names, EXIF, MakerNote, XMP and the ICC profile description. The whole POC pipeline
then runs as a real subprocess, the way an agent on the VM runs it. Everything an
agent can see (stdout, stderr, DB error fields, the logs folder and crash dumps) and
everything handed to the print lab (the whole export folder) is scanned.

Failure messages show hit counts only (``leakscan.report``), never the hit itself, so
agents see pass/fail counts and nothing else (S2-D11).
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC))

from fpc_poc import leakscan  # noqa: E402

TOOLS = POC / "tools"
TOKEN = "CANARY_7Q3"
EVENT = "Test"
TIMEOUT = 600


class Proc:
    """A finished command. Its repr shows the exit code only, so a failed assertion can
    never echo captured output into the test report an agent reads."""

    def __init__(self, cp: subprocess.CompletedProcess):
        self.rc, self.stdout, self.stderr = cp.returncode, cp.stdout, cp.stderr

    def __repr__(self) -> str:
        return f"Proc(rc={self.rc})"


def _run(data: Path, *args: str, cwd: Path) -> Proc:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(POC) + os.pathsep + env.get("PYTHONPATH", "")
    env["FPC_POC_CRASH_MARKER"] = "CRASHME"
    env.pop("PYTHONFAULTHANDLER", None)
    return Proc(subprocess.run([sys.executable, "-m", "fpc_poc", "--data", str(data), *args],
                               cwd=cwd, env=env, capture_output=True, text=True, timeout=TIMEOUT,
                               check=False))


@pytest.fixture(scope="module")
def canary_run(tmp_path_factory):
    pytest.importorskip("skimage")
    sys.path.insert(0, str(TOOLS))
    import make_sample_event

    root = tmp_path_factory.mktemp("canary")
    event_dir, data, out = root / "event", root / "data", root / "prints"
    with contextlib.redirect_stdout(io.StringIO()):  # the generator prints its output path
        make_sample_event.main(event_dir, canary=TOKEN)
    procs = {}
    procs["run"] = _run(data, "run", "--event", EVENT, str(event_dir), "--target", "1000",
                        "--auto-approve", "1000", "--out", str(out), "--workers", "2", cwd=root)
    # An interrupted export leaves a .tmp behind; the next export must clean it up.
    stale = out / EVENT / "print_ready" / "TEST_9999.tmp"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"partial write from an interrupted export")
    procs["export"] = _run(data, "export", "--event", EVENT, "--out", str(out), "--workers", "2",
                           cwd=root)
    for cmd in ("status", "integrity", "summary", "rank", "crops"):
        procs[cmd] = _run(data, cmd, "--event", EVENT, cwd=root)
    return {"root": root, "event_dir": event_dir, "data": data, "out": out, "procs": procs,
            "stale": stale}


def _literals(run) -> list[str]:
    """Absolute roots of this run, in both slash styles, counted as path hits."""
    lits = {str(run["root"]), run["root"].as_posix(), str(Path.home())}
    return sorted(lits)


def _assert_clean(results: dict) -> None:
    total = sum(leakscan.hits(c) for c in results.values())
    assert total == 0, "leak scan failed (counts only): " + leakscan.report(results)


# ---------------------------------------------------------------- the canaries are real


def test_canaries_planted_in_every_carrier(canary_run):
    from PIL import Image, ImageCms

    day = canary_run["event_dir"] / f"Day4_{TOKEN}"
    assert day.is_dir()  # folder name
    files = sorted(day.iterdir())
    assert files and all(TOKEN in f.name for f in files)  # file names
    jpg = next(f for f in files if f.name.endswith("4001.JPG"))
    with Image.open(jpg) as im:  # synthetic fixture only
        exif = im.getexif()
        maker = exif.get_ifd(0x8769).get(0x927C)
        xmp = im.info.get("xmp")
        icc = im.info.get("icc_profile")
    assert TOKEN in exif[271] and TOKEN in exif[270]  # EXIF Make, ImageDescription
    assert TOKEN.encode() in bytes(maker)  # MakerNote
    assert TOKEN.encode() in bytes(xmp)  # XMP
    desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(io.BytesIO(icc)))
    assert TOKEN in desc  # ICC description


def test_canary_sources_flow_through_the_whole_pipeline(canary_run):
    for name, p in canary_run["procs"].items():
        rc = p.rc
        assert rc == 0, name
    con = sqlite3.connect(canary_run["data"] / "poc.db")
    like = f"%{TOKEN}%"
    ok = con.execute("SELECT COUNT(*) FROM photos WHERE path LIKE ? AND status='ok'", (like,)).fetchone()[0]
    err = con.execute("SELECT COUNT(*) FROM photos WHERE path LIKE ? AND status='import_error'",
                      (like,)).fetchone()[0]
    exported = con.execute(
        "SELECT COUNT(*) FROM exports e JOIN photos p ON p.id=e.photo_id WHERE p.path LIKE ? "
        "AND e.status IN ('done','skipped_existing')", (like,)).fetchone()[0]
    assert ok >= 4 and err >= 1  # 3 JPEG + 1 HEIC decoded, the corrupt one failed
    assert exported >= 1  # canary metadata really reached the export stage


# ---------------------------------------------------------------- scans


def test_no_leak_in_command_output(canary_run):
    lits = _literals(canary_run)
    results = {}
    for name, p in canary_run["procs"].items():
        results[f"{name}.stdout"] = leakscan.scan_text(p.stdout, [TOKEN], lits)
        results[f"{name}.stderr"] = leakscan.scan_text(p.stderr, [TOKEN], lits)
    _assert_clean(results)


def test_no_leak_in_db_error_fields(canary_run):
    con = sqlite3.connect(canary_run["data"] / "poc.db")
    errors = [r[0] for r in con.execute("SELECT error FROM photos WHERE error IS NOT NULL")]
    assert len(errors) >= 3  # crasher, corrupt, canary corrupt
    results = {"photos.error": leakscan.scan_text("\n".join(errors), [TOKEN], _literals(canary_run))}
    _assert_clean(results)


def test_no_leak_in_export_folder(canary_run):
    out = canary_run["out"]
    c = leakscan.scan_tree(out, [TOKEN], _literals(canary_run))
    assert c["files"] >= 4  # images + selection.csv + crop_decisions.json + export_summary.json
    _assert_clean({"export": c})


def test_no_leak_in_logs_and_crash_dumps(canary_run):
    data = canary_run["data"]
    results = {"logs": leakscan.scan_tree(data / "logs", [TOKEN], _literals(canary_run))}
    for i, p in enumerate(sorted(data.rglob("*"))):
        if p.suffix.lower() in (".log", ".dmp", ".mdmp", ".txt") and "logs" not in p.parts:
            results[f"dump{i}"] = leakscan.scan_file(p, [TOKEN], _literals(canary_run))
    _assert_clean(results)


# ---------------------------------------------------------------- export folder contents (D7)


def test_export_file_names_are_opaque(canary_run):
    base = canary_run["out"] / EVENT
    names = sorted(p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file())
    images = [n for n in names if n.startswith("print_ready/")]
    assert images
    assert all(re.fullmatch(r"print_ready/[A-Z0-9]{1,8}_\d{4}\.jpg", n) for n in images)
    assert set(names) - set(images) == {"manifests/selection.csv", "manifests/crop_decisions.json",
                                        "reports/export_summary.json"}
    stems = {p.stem.lower() for p in canary_run["event_dir"].rglob("*") if p.is_file()}
    parts = {s for stem in stems for s in re.split(r"[^a-z0-9]+", stem) if len(s) >= 3}
    words = {s for s in parts if not s.isdigit()} | {TOKEN.lower(), "canary", "7q3"}
    numbers = {s for s in parts if s.isdigit()}
    for n in images:
        prefix, seq = n.split("/")[-1].rsplit(".", 1)[0].lower().rsplit("_", 1)
        assert prefix == "test"  # from the user's event label, not from any source
        assert not any(w in prefix for w in words)
        assert seq not in numbers  # a sequence number, not the source's number


def test_selection_csv_has_output_name_opaque_id_status_verified_only(canary_run):
    path = canary_run["out"] / EVENT / "manifests" / "selection.csv"
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0] == ["output", "photo_id", "status", "verified"]
    assert len(rows) > 1
    for r in rows[1:]:
        assert re.fullmatch(r"[A-Z0-9]{1,8}_\d{4}\.jpg", r[0])
        assert r[1].isdigit()
        assert not re.search(r"[0-9a-f]{64}", ",".join(r))  # no source SHA-256


def test_crop_decisions_and_summary_carry_no_source_details(canary_run):
    base = canary_run["out"] / EVENT
    crops = json.loads((base / "manifests" / "crop_decisions.json").read_text())
    allowed = {"output", "target", "mode", "oriented_size", "px", "norm", "risk"}
    assert crops
    for pid, c in crops.items():
        assert pid.isdigit()
        assert set(c) <= allowed, sorted(set(c) - allowed)
    summary = json.loads((base / "reports" / "export_summary.json").read_text())
    item_keys = {"output", "status", "verified", "problems", "warnings", "size"}
    for pid, it in summary["items"].items():
        assert pid.isdigit()
        assert set(it) <= item_keys, sorted(set(it) - item_keys)
        for code in it.get("problems", []) + it.get("warnings", []):
            assert re.fullmatch(r"[A-Z][A-Z0-9_]+", code)  # codes, not message text
    text = json.dumps([crops, summary])
    assert not re.search(r"[0-9a-f]{64}", text)
    assert "source_fingerprint" not in text and "sha256" not in text and "capture" not in text


def test_leftover_tmp_from_interrupted_export_is_removed(canary_run):
    assert not canary_run["stale"].exists()
    assert not list((canary_run["out"] / EVENT).rglob("*.tmp"))
    out = json.loads(canary_run["procs"]["export"].stdout)
    assert out["tmp_removed"] == 1


# ---------------------------------------------------------------- command outputs are aggregates


def test_run_and_export_print_counts_not_paths(canary_run):
    for name in ("run", "export"):
        text = canary_run["procs"][name].stdout
        no_path_labels = "data dir" not in text and "folder" not in text
        assert no_path_labels, name
    exp = json.loads(canary_run["procs"]["export"].stdout)
    assert all(isinstance(v, int) for v in exp.values())


def test_integrity_prints_counts(canary_run):
    res = json.loads(canary_run["procs"]["integrity"].stdout)
    counts_only = (set(res) == {"checked", "changed"} and res["changed"] == 0
                   and isinstance(res["checked"], int) and res["checked"] > 0)
    assert counts_only, "integrity must print {checked: n, changed: 0}"


def test_integrity_writes_changed_list_to_data_dir_only(tmp_path, capsys):
    from fpc_poc import db
    from fpc_poc.__main__ import main

    data = tmp_path / f"data_{TOKEN}"
    data.mkdir()
    src = tmp_path / f"src_{TOKEN}" / "not_a_photo.txt"  # synthetic stand-in, never a photo
    src.parent.mkdir()
    src.write_text("synthetic")
    con = db.connect(data / "poc.db")
    con.execute("INSERT INTO photos(event, path, status, sha256) VALUES(?,?,?,?)",
                ("X", str(src), "ok", "0" * 64))
    con.commit()
    rc = main(["--data", str(data), "integrity", "--event", "X"])
    out = capsys.readouterr()
    assert rc == 1
    counts_only = json.loads(out.out) == {"checked": 1, "changed": 1}
    assert counts_only, "integrity must print counts only"
    _assert_clean({"stdout": leakscan.scan_text(out.out + out.err, [TOKEN], [str(tmp_path)])})
    listing = data / "reports" / "integrity_changed.json"
    assert json.loads(listing.read_text()) == [str(src)]  # detail stays in the data dir


SUMMARY_KEYS = {
    "photos": {"total", "discovered", "excluded", "unavailable", "ok", "import_error", "missing",
               "other"},
    "errors": {"E_DECODE", "E_CRASH", "E_SOURCE_CHANGED", "E_WORKER", "other"},
    "groups": {"exact", "near", "burst", "ambiguous", "other"},
    "ranking": {"scored", "recommended"},
    "reason_codes": {"FACES", "SOFT", "EXPOSURE", "LOW_RES", "INCOMPLETE_METADATA", "PET", "OTHER"},
    "decisions": {"approve", "reject", "log_rows", "undo"},
    "crop_risk": {"likely_safe", "needs_review", "crop_conflict", "manual", "other"},
    "exports": {"done", "skipped_existing", "error", "unavailable", "crashed", "verified",
                "warnings", "other"},
    "timings": {"scan_s", "ingest_s", "analyze_s", "rank_s", "crops_s", "export_s", "integrity_s"},
}


def test_summary_schema_is_aggregate_only(canary_run):
    p = canary_run["procs"]["summary"]
    rc = p.rc
    assert rc == 0
    s = json.loads(p.stdout)
    assert set(s) <= set(SUMMARY_KEYS), sorted(set(s) - set(SUMMARY_KEYS))
    assert {"photos", "errors", "decisions", "reason_codes", "crop_risk", "timings"} <= set(s)
    for section, body in s.items():
        assert isinstance(body, dict)
        assert set(body) <= SUMMARY_KEYS[section], (section, sorted(set(body) - SUMMARY_KEYS[section]))
        for k, v in body.items():
            assert type(v) in (int, float) and v >= 0, (section, k)  # a count or a timing
            if section == "timings":
                assert k.endswith("_s")
            else:
                assert type(v) is int  # counts are integers
    assert s["photos"]["total"] > 0 and s["decisions"]["approve"] > 0
    assert s["errors"]["E_CRASH"] >= 1 and s["errors"]["E_DECODE"] >= 1


# ---------------------------------------------------------------- uncaught exceptions


def test_forced_uncaught_exception_prints_class_and_code_only(canary_run):
    root, data = canary_run["root"], canary_run["data"]
    blocker = root / f"out_{TOKEN}"
    blocker.write_text("a file where the export folder should be")  # makes mkdir raise with the path
    p = _run(data, "export", "--event", EVENT, "--out", str(blocker), cwd=root)
    rc = p.rc
    assert rc == 3
    stdout_empty = p.stdout == ""
    assert stdout_empty
    shape_ok = bool(re.fullmatch(r"fpc_poc: error class=[A-Za-z_][A-Za-z0-9_]* code=E_UNCAUGHT\n",
                                 p.stderr))
    assert shape_ok, "stderr is not exactly 'fpc_poc: error class=<Name> code=E_UNCAUGHT'"
    lits = _literals(canary_run)
    results = {"stderr": leakscan.scan_text(p.stderr, [TOKEN], lits),
               "logs": leakscan.scan_tree(data / "logs", [TOKEN], lits)}
    _assert_clean(results)
    log = (data / "logs" / "crash.log").read_text()
    cls = p.stderr.split("class=")[1].split()[0]
    useful = cls in log and "E_UNCAUGHT" in log and "pipeline.py" in log  # useful, still clean
    assert useful


def test_uncaught_handler_in_process(tmp_path, monkeypatch, capsys):
    from fpc_poc import __main__ as cli_mod
    from fpc_poc import pipeline

    secret = str(tmp_path / f"{TOKEN}" / "IMG_0001.JPG") + " at 2024:04:02 10:00:00"

    def boom(*_a, **_k):
        raise RuntimeError(f"could not read {secret}")

    monkeypatch.setattr(pipeline, "integrity", boom)
    data = tmp_path / "data"
    rc = cli_mod.cli(["--data", str(data), "integrity", "--event", "X"])
    out = capsys.readouterr()
    assert rc == 3
    exact = out.out == "" and out.err == "fpc_poc: error class=RuntimeError code=E_UNCAUGHT\n"
    assert exact, "uncaught handler printed more than class and code"
    _assert_clean({"stderr": leakscan.scan_text(out.err, [TOKEN], [str(tmp_path)]),
                   "logs": leakscan.scan_tree(data / "logs", [TOKEN], [str(tmp_path)])})


def test_export_refusal_has_no_path(canary_run):
    root, data = canary_run["root"], canary_run["data"]
    p = _run(data, "export", "--event", EVENT, "--out", str(canary_run["event_dir"] / f"Day4_{TOKEN}"),
             cwd=root)
    rc = p.rc
    assert rc != 0
    refused = "refused" in p.stderr
    assert refused
    _assert_clean({"stderr": leakscan.scan_text(p.stdout + p.stderr, [TOKEN], _literals(canary_run))})


# ---------------------------------------------------------------- the scanner itself


def test_scanner_detects_every_kind_and_reports_counts_only():
    sample = (f"x {TOKEN.lower()} C:\\Users\\me\\a.jpg \\\\nas\\share\\b /home/me/c.jpg "
              "2024:04:02 2024-04-02 10:00:00")
    c = leakscan.scan_text(sample, [TOKEN])
    assert c["canary"] == 1 and c["abs_path"] == 3 and c["timestamp"] == 3
    assert leakscan.scan_bytes(TOKEN.encode("utf-16-be"), [TOKEN])["canary"] == 1
    clean = leakscan.scan_text('{"ok": 12, "seconds": 1.5, "ratio": "3:2", "print_ready/X_0001.jpg": 1}',
                               [TOKEN])
    assert leakscan.hits(clean) == 0
    rep = leakscan.report({"stdout": c})
    assert TOKEN not in rep and "home" not in rep and "2024" not in rep

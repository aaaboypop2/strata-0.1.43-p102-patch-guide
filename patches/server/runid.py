#!/usr/bin/env python3
"""Run ids: one id per distinct setup Strata ran with, kept in ~/strata-perf/runs.db (SQLite).

A setup is everything that can change speed or answers: the engine binary (sha256), its BUILD.json, the vision
binary, the server's code (sha256 of serve/server.py and which local patches it has), the run config file (without
its API key), the engine's arguments and environment (STRATA_* / MALLOC_* / CUDA_*), the GPU driver, the GPUs, the
CPU, the RAM and the OS.  The same setup started again gets the same id; anything different gets a new one.  Logs
and the per-request lines carry only the id; the database holds the details.

  runs    (id, seq, fingerprint, created, details)   details: the JSON above; fingerprint: its sha256
  starts  (t, run_id, pid, source)                   every engine start ("server": registered by Strata's server,
                                                     "log": read back from an old log, details partly unknown)
  hashes  (path, size, mtime, sha256)                file hashes, so a 100 MB binary is read once per build

The id is <host>-<seq>, e.g. p102-0007; <host> is the first word of ~/strata-perf/host-name, else the hostname.

  runid.py show [id]       the runs (or one run's details) as JSON
  runid.py diff <a> <b>    what differs between two runs
"""
import hashlib
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.join(os.path.expanduser("~"), "strata-perf")
DB = os.path.join(ROOT, "runs.db")
ENV_KEEP = ("STRATA_", "MALLOC_", "CUDA_")
PATCH_MARKS = {"splice": "_splice_held", "maintenance": "_maintenance(", "perflog": "_perf_write",
               "enginelog": "_stamp_engine_lines", "divergence": "differs from the conversation held",
               "pplog": "_pp_said", "stageprog": "stage_pos", "runid": "_runid_register"}


def connect():
    os.makedirs(ROOT, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, seq INTEGER, fingerprint TEXT UNIQUE, created REAL,
                                         details TEXT);
        CREATE TABLE IF NOT EXISTS starts (t REAL, run_id TEXT, pid INTEGER, source TEXT);
        CREATE INDEX IF NOT EXISTS starts_t ON starts (t);
        CREATE TABLE IF NOT EXISTS hashes (path TEXT, size INTEGER, mtime REAL, sha256 TEXT,
                                           PRIMARY KEY (path, size, mtime));
    """)
    return db


def host_name():
    try:
        with open(os.path.join(ROOT, "host-name"), encoding="utf-8") as f:
            w = f.read().split()
            if w:
                return w[0]
    except OSError:
        pass
    return socket.gethostname().lower()


def file_hash(db, path):
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return None
    row = db.execute("SELECT sha256 FROM hashes WHERE path=? AND size=? AND mtime=?",
                     (path, st.st_size, st.st_mtime)).fetchone()
    if row:
        return row[0]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    db.execute("INSERT OR REPLACE INTO hashes VALUES (?,?,?,?)", (path, st.st_size, st.st_mtime, h.hexdigest()))
    return h.hexdigest()


def machine():
    m = {"host": socket.gethostname(), "os": platform.platform(), "cpu": platform.processor() or platform.machine()}
    try:
        if sys.platform.startswith("linux"):
            for line in open("/proc/cpuinfo"):
                if line.startswith("model name"):
                    m["cpu"] = line.split(":", 1)[1].strip()
                    break
            m["ram_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
        else:
            import psutil
            m["ram_gb"] = round(psutil.virtual_memory().total / 2**30)
    except Exception:   # noqa: BLE001
        pass
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=index,name,pci.bus_id,memory.total,driver_version",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=20,
                             creationflags=0x08000000 if os.name == "nt" else 0).stdout
        gpus = [[x.strip() for x in line.split(",")] for line in out.strip().splitlines()]
        m["gpus"] = [g[:4] for g in gpus]
        m["driver"] = gpus[0][4] if gpus else None
    except Exception:   # noqa: BLE001
        pass
    return m


def config_of(argv):
    """The run config the server was started with (--config), without its API key."""
    if "--config" not in argv:
        return None, None
    path = argv[argv.index("--config") + 1]
    try:
        c = json.load(open(path, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return path, None
    c.pop("api_key", None)
    return path, c


def details(db, exe, args, env, server_py=None, argv=None):
    env = env if env is not None else dict(os.environ)
    d = {"engine": {"exe": exe, "sha256": file_hash(db, exe)}, "args": list(args),
         "env": {k: v for k, v in sorted(env.items()) if k.startswith(ENV_KEEP)}, "machine": machine()}
    try:
        d["engine"]["build"] = json.load(open(os.path.join(os.path.dirname(exe), "BUILD.json")))
    except (OSError, ValueError, TypeError):
        pass
    if server_py:
        d["server"] = {"file": server_py, "sha256": file_hash(db, server_py)}
        try:
            text = open(server_py, encoding="utf-8").read()
            d["server"]["patches"] = sorted(k for k, mark in PATCH_MARKS.items() if mark in text)
        except OSError:
            pass
    cpath, cfg = config_of(argv or [])
    if cpath:
        d["config"] = {"file": cpath, "content": cfg}
        vexe = ((cfg or {}).get("vision") or {}).get("exe")
        if vexe:
            d["vision"] = {"exe": vexe, "sha256": file_hash(db, vexe)}
    return d


def get_or_create(db, det, created=None):
    fp = hashlib.sha256(json.dumps(det, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    row = db.execute("SELECT id FROM runs WHERE fingerprint=?", (fp,)).fetchone()
    if row:
        return row[0], False
    host = host_name()
    seq = (db.execute("SELECT MAX(seq) FROM runs").fetchone()[0] or 0) + 1
    rid = f"{host}-{seq:04d}"
    db.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (rid, seq, fp, created or time.time(),
                                                       json.dumps(det, ensure_ascii=False)))
    return rid, True


def register(exe, args, env=None, pid=None, server_py=None, argv=None):
    """Called by Strata's server when it starts an engine: the run id of this setup (new if it is new)."""
    db = connect()
    with db:
        rid, new = get_or_create(db, details(db, exe, args, env, server_py, argv if argv is not None else sys.argv))
        db.execute("INSERT INTO starts VALUES (?,?,?,?)", (time.time(), rid, pid, "server"))
    db.close()
    return rid, new


def runs(db):
    out = []
    for rid, seq, created, det in db.execute("SELECT id, seq, created, details FROM runs ORDER BY seq"):
        out.append({"id": rid, "created": created, "details": json.loads(det)})
    return out


def flat(d, pre=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flat(v, f"{pre}{k}."))
    elif isinstance(d, list) and pre.endswith("args."):
        i = 0
        while i < len(d):   # arguments as flag -> value, so a changed value reads as one change
            if str(d[i]).startswith("--") and i + 1 < len(d) and not str(d[i + 1]).startswith("--"):
                out[pre + d[i]] = d[i + 1]; i += 2
            else:
                out[pre + str(d[i])] = True; i += 1
    else:
        out[pre[:-1]] = d
    return out


def diff(a, b):
    fa, fb = flat(a), flat(b)
    return {k: [fa.get(k), fb.get(k)] for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)}


def main():
    db = connect()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    rs = {r["id"]: r for r in runs(db)}
    if cmd == "show":
        print(json.dumps(rs[sys.argv[2]] if len(sys.argv) > 2 else list(rs.values()), ensure_ascii=False, indent=1))
    elif cmd == "diff":
        print(json.dumps(diff(rs[sys.argv[2]]["details"], rs[sys.argv[3]]["details"]), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

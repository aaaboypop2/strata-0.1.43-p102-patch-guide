#!/usr/bin/env python3
"""Patch Strata's server (after apply-perflog-patch.py): every engine start gets a run id (runid.py, copied next to
server.py), which the server and engine logs print and every per-request line carries as "run".  The details of each
id - binaries' hashes, server patches, config, arguments, environment, driver, hardware - are in
~/strata-perf/runs.db; a setup that differs in anything gets a new id.

  [strata] run p102-0007 (new setup)

Usage: apply-runid-patch.py <Strata source folder>
"""
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
p = src / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "_runid_register" in s:
    sys.exit("already patched")
if "_perf_write" not in s:
    sys.exit("apply apply-perflog-patch.py first")
shutil.copy(Path(__file__).with_name("runid.py"), src / "serve/runid.py")


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("serve/server.py: the text to replace was not found once:\n" + old[:200])
    s = s.replace(old, new, 1)


rep('''def _perf_write(rec):''', '''def _runid_register(exe, args, env, pid):
    """Local patch: this engine start's run id (runid.py; ~/strata-perf/runs.db), or None when it cannot be had."""
    try:
        import runid
        rid, new = runid.register(exe, args, env, pid, server_py=os.path.abspath(__file__))
        print(f"[strata] run {rid}" + (" (new setup)" if new else ""), flush=True)
        return rid
    except Exception as e:   # noqa: BLE001 - never stops an engine
        print(f"[strata] run id: not recorded ({e})", flush=True)
        return None


def _perf_write(rec):''')
rep('''        if log:   # local patch: each engine log line gets the time of day''', '''        self.run_id = _runid_register(exe, args, env, self.proc.pid)   # local patch: this start's run id
        if log and self.run_id:
            try:
                self.log.write(f"[strata] run {self.run_id}\\n")
                self.log.flush()
            except OSError:
                pass
        if log:   # local patch: each engine log line gets the time of day''')
rep('''                                _rec.update({"t": round(time.time(), 1), "model": self.model,''',
    '''                                _rec.update({"t": round(time.time(), 1), "model": self.model,
                                             "run": getattr(getattr(self, "engine", None), "run_id", None),''')
p.write_text(s, encoding="utf-8")
print("patched")

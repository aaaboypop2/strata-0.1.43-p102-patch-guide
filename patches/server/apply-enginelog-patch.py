#!/usr/bin/env python3
"""Patch Strata's server: every line the engine writes to its log gets the time of day in front (HH:MM:SS.mmm),
and the per-request statistics line (apply-perflog-patch.py) records the reasoning effort asked for.

The engine's stderr went straight into the log file, so its lines had no time.  Now it goes through a pipe and a
thread that stamps each line.  The server's own readers of that log search inside the line (re.search), so the
stamp does not change what they find; the one start-failure hint that compared the line's start skips the stamp.

Usage: apply-enginelog-patch.py <Strata source folder>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "_stamp_engine_lines" in s:
    sys.exit("already patched")


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("serve/server.py: the text to replace was not found once:\n" + old[:200])
    s = s.replace(old, new, 1)


rep('''ENGINE_REQUEST = re.compile(''', '''def _stamp_engine_lines(src, dst):
    """Local patch: the engine's stderr, line by line, into its log with the time of day in front."""
    try:
        for line in src:
            t = time.time()
            dst.write(time.strftime("%H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d} " + line)
            dst.flush()
    except (OSError, ValueError):   # the log closed under a dying engine
        pass


_STAMP = re.compile(r"^\\d\\d:\\d\\d:\\d\\d\\.\\d{3} ")


ENGINE_REQUEST = re.compile(''')

rep('''            if x.strip().startswith("strata mtp: ")''', '''            if _STAMP.sub("", x.strip()).startswith("strata mtp: ")''')

rep('''        self.proc = popen("the Strata engine", [exe, "--serve", *args], cwd=cwd, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=self.log, text=True, encoding="utf-8", bufsize=1, env=env, **pin)''',
    '''        self.proc = popen("the Strata engine", [exe, "--serve", *args], cwd=cwd, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE if log else self.log, text=True,
                          encoding="utf-8", errors="replace", bufsize=1, env=env, **pin)
        if log:   # local patch: each engine log line gets the time of day
            threading.Thread(target=_stamp_engine_lines, args=(self.proc.stderr, self.log), daemon=True).start()''')

rep('''                        "stream": bool(req.get("stream")), "last_role": _last.get("role"), "last_chars": len(str(_text or ""))}''',
    '''                        "stream": bool(req.get("stream")), "last_role": _last.get("role"), "last_chars": len(str(_text or "")),
                        "effort": req.get("reasoning_effort") or (req.get("reasoning") or {}).get("effort")
                        if isinstance(req.get("reasoning"), (dict, type(None))) else req.get("reasoning_effort")}''')
p.write_text(s, encoding="utf-8")
print("patched")

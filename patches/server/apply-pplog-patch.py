#!/usr/bin/env python3
"""Patch Strata's server (after apply-log-patch.py): when a request's first token comes, one line says how its
prompt was read, before the first thinking/answering line:

  01:25:40 prompt read: 148 tokens in 5.2 s (28.6 tok/s), 138,394 reused from the cache, first token after 5.3 s

Usage: apply-pplog-patch.py <Strata source folder>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "_pp_said" in s:
    sys.exit("already patched")


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("serve/server.py: the text to replace was not found once:\n" + old[:200])
    s = s.replace(old, new, 1)


rep('''        else:
            rate = s["generated"] / max(1e-6, now - s["first_token"])''', '''        else:
            if self.__dict__.get("_pp_said") != (id(st), s.get("started")):   # local patch: the prompt, once
                self._pp_said = (id(st), s.get("started"))
                try:
                    reused = int(getattr(self.engine, "reused", 0) or 0)
                    total = int(s.get("prompt_tokens") or 0)
                    pr = getattr(self.engine, "progress", None)
                    ms = getattr(self.engine, "progress_ms", 0) or 0
                    read = max(0, total - reused)
                    ft = s["first_token"] - s.get("started", s["first_token"])
                    secs = ms / 1000 if ms else ft
                    print(f"[strata] prompt read: {read:,} tokens in {secs:.1f} s ({read / max(secs, 1e-3):.1f} tok/s), "
                          f"{reused:,} reused from the cache, first token after {ft:.1f} s", flush=True)
                except Exception:   # noqa: BLE001 - a progress line must never fail a request
                    pass
            rate = s["generated"] / max(1e-6, now - s["first_token"])''')
p.write_text(s, encoding="utf-8")
print("patched")

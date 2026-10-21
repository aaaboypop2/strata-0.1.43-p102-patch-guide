#!/usr/bin/env python3
"""Patch Strata's server (after apply-log-patch.py and apply-perflog-patch.py): say where a request differs from the
conversation the engine holds when that is BEFORE the last answer - the case where the cache can reuse only up to a
checkpoint in front of it, or nothing (a client that rewrote its system prompt, a tool list that changed, an earlier
message edited).  One line in the server's window, and the per-request statistics get "diverged".

  note: the request differs from the conversation held at token 312 of 138,394 (message 0 of 390, the system
  prompt); before '...': held '...', the request '...'

Usage: apply-divergence-patch.py <Strata source folder>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "_perf_local.diverged" in s:
    sys.exit("already patched")
if "_perf_local.changed = {" not in s:
    sys.exit("apply apply-perflog-patch.py first")


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("serve/server.py: the text to replace was not found once:\n" + old[:200])
    s = s.replace(old, new, 1)


rep('''                ids = self._splice_held(held, ids, same)
''', '''                ids = self._splice_held(held, ids, same)
            elif same < held_prompt - 4:   # local patch: it differs inside the conversation, not in the last answer
                try:
                    start = self.tok.encode("<|im_start|>", parse_special=True)
                    mark = start[0] if len(start) == 1 else None
                    msg_no = sum(1 for t in held[:same] if t == mark) - 1 if mark is not None else -1
                    msgs = sum(1 for t in held[:held_prompt] if t == mark) if mark is not None else 0
                    where = (f"message {msg_no} of {msgs}" + (", the system prompt" if msg_no == 0 else "")) if msg_no >= 0 else "?"
                    before = self.tok.decode(held[max(0, same - 24):same])
                    was = self.tok.decode(held[same:same + 24])
                    now_ = self.tok.decode(list(ids[same:same + 24]))
                    print(f"[strata] note: the request differs from the conversation held at token {same:,} of "
                          f"{held_prompt:,} ({where}); before {before!r}: held {was!r}, the request {now_!r}", flush=True)
                    _perf_local.diverged = {"at": same, "of": held_prompt, "message": msg_no, "messages": msgs,
                                            "held": was[:80], "request": now_[:80]}
                except Exception:   # noqa: BLE001 - a diagnostic line must never fail a request
                    pass
''')
rep('''    _perf_local.changed = None
    _perf_local.spliced = None
''', '''    _perf_local.changed = None
    _perf_local.spliced = None
    _perf_local.diverged = None
''')
rep('''                                             "spliced": getattr(_perf_local, "spliced", None)})''',
    '''                                             "spliced": getattr(_perf_local, "spliced", None),
                                             "diverged": getattr(_perf_local, "diverged", None)})''')
p.write_text(s, encoding="utf-8")
print("patched")

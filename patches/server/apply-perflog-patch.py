#!/usr/bin/env python3
"""Patch Strata's server (after apply-log-patch.py and apply-splice-patch.py): one JSON line per request in
~/strata-perf/requests-YYYY-MM.jsonl, for statistics of real use.

Each line holds the server's own request record (prompt tokens, reused, read, prompt_ms, decode_ms, tok/s, drafts,
expert cache hit rate, finish) plus who sent it, the shape of the request (messages by role, tools, images, thinking,
max tokens, streamed, the last message's role and length), the time to the first token, the wall time from arrival,
and whether the last answer came back changed (where, how much was read again) or with the same text in other tokens.

Usage: apply-perflog-patch.py <Strata source folder>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "_perf_write" in s:
    sys.exit("already patched")
if "_splice_held" not in s:
    sys.exit("apply apply-splice-patch.py first")


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("serve/server.py: the text to replace was not found once:\n" + old[:200])
    s = s.replace(old, new, 1)


rep('''    builtins.print(*args, **kwargs)
''', '''    builtins.print(*args, **kwargs)


import threading as _pl_threading
_perf_local = _pl_threading.local()   # local patch: this request's shape, set as it arrives (the handler's thread)


def _perf_write(rec):
    """Local patch: one JSON line per request in ~/strata-perf/requests-YYYY-MM.jsonl (never fails a request)."""
    try:
        d = os.path.expanduser("~/strata-perf")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, time.strftime("requests-%Y-%m.jsonl")), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\\n")
    except Exception:   # noqa: BLE001
        pass
''')

rep('''    _preview = str(_text or "")[:100].replace(chr(10), " ")
''', '''    _preview = str(_text or "")[:100].replace(chr(10), " ")
    _perf_local.meta = {"api": api, "client": who, "messages": len(messages or []), "roles": _roles,
                        "tools": len(tools or []), "images": _images, "thinking": bool(thinking), "max_new": max_new,
                        "stream": bool(req.get("stream")), "last_role": _last.get("role"), "last_chars": len(str(_text or ""))}
    _perf_local.changed = None
    _perf_local.spliced = None
''')

rep('''                          f"{now_!r} - the {len(held) - same} tokens from there are read again", flush=True)
''', '''                          f"{now_!r} - the {len(held) - same} tokens from there are read again", flush=True)
                    _perf_local.changed = {"at": max(0, same - held_prompt), "of": len(held) - held_prompt,
                                           "reread": len(held) - same}
''')

rep('''                              f"{n}); the model's own tokens are kept, so the {n} tokens are not read again", flush=True)
''', '''                              f"{n}); the model's own tokens are kept, so the {n} tokens are not read again", flush=True)
                        _perf_local.spliced = n
''')

rep('''                            t = self.totals
                            t["requests"] += 1''', '''                            try:   # local patch: the request's line for the statistics
                                _rec = dict(self.history[-1])
                                _rec.update(getattr(_perf_local, "meta", None) or {})
                                _ft = st.get("first_token")
                                _rec.update({"t": round(time.time(), 1), "model": self.model,
                                             "first_token_s": round(_ft - started, 2) if _ft else None,
                                             "wall_s": round(time.time() - started, 2), "cancelled": cancel.is_set(),
                                             "changed": getattr(_perf_local, "changed", None),
                                             "spliced": getattr(_perf_local, "spliced", None)})
                                _perf_write(_rec)
                            except Exception:   # noqa: BLE001
                                pass
                            t = self.totals
                            t["requests"] += 1''')
p.write_text(s, encoding="utf-8")
print("patched")

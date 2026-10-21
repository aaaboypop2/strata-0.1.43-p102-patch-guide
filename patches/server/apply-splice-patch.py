#!/usr/bin/env python3
"""Patch Strata's server (after apply-log-patch.py): reuse the model's own tokens for an answer sent back unchanged,
and a maintenance switch.

Usage: apply-splice-patch.py <Strata source folder>

1. An answer the client sends back with the same text can still tokenize differently from the tokens the model
   generated (a word the model wrote in two pieces is one token when the text is read again).  The engine then holds
   different tokens from that point and reads the rest of the answer again - 15,290 tokens once in a real session.
   When the request's tokens from the first difference decode to exactly the text the model wrote, the request takes
   the model's own tokens for that stretch, so the engine reuses all of it.  Anything else (a changed answer, the
   thinking left out) is read as before.

2. Maintenance: while the file ~/strata-maintenance exists, /v1/* requests that do not come from this machine
   (127.0.0.1 without a Cloudflare header) get 503 with the file's text, so tests can run without users in between.
"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "_splice_held" in s:
    sys.exit("already patched")
if "the last answer came back changed" not in s:
    sys.exit("apply apply-log-patch.py first")


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("serve/server.py: the text to replace was not found once:\n" + old[:200])
    s = s.replace(old, new, 1)


rep('''                except Exception:   # noqa: BLE001 - a diagnostic line must never fail a request
                    pass
        defaults = {**self.sampling_defaults, **self.shared}''', '''                except Exception:   # noqa: BLE001 - a diagnostic line must never fail a request
                    pass
                ids = self._splice_held(held, ids, same)
        defaults = {**self.sampling_defaults, **self.shared}''')

rep('''    def _run(self, ids, thinking, tools, max_new, sampling, cancel, force=None) -> Iterator[tuple[str, object]]:''',
    '''    def _splice_held(self, held, ids, same):
        """Local patch: the request differs from the tokens the engine holds at `same`.  If its tokens from there
        decode to exactly the text of the held ones (the answer came back unchanged, only tokenized differently), the
        held tokens take their place, so the engine reuses the whole answer.  Otherwise `ids` as they are."""
        try:
            n = len(held) - same
            if n <= 0:
                return ids
            want = self.tok.decode(list(held[same:]))
            rest = list(ids[same:])
            if not want or not self.tok.decode(rest).startswith(want):
                return ids
            for d in range(0, 65):           # the same text in a few tokens more or fewer
                for L in ((n + d, n - d) if d else (n,)):
                    if 0 < L <= len(rest) and self.tok.decode(rest[:L]) == want:
                        out = [int(t) for t in held] + [int(t) for t in rest[L:]]
                        print(f"[strata] note: the answer came back with the same text in other tokens ({L} instead of "
                              f"{n}); the model's own tokens are kept, so the {n} tokens are not read again", flush=True)
                        return out
        except Exception:   # noqa: BLE001 - never fail a request over this
            pass
        return ids

    def _run(self, ids, thinking, tools, max_new, sampling, cancel, force=None) -> Iterator[tuple[str, object]]:''')

rep('''        def _post(self):
            if not self._authorized():
                return
            path = self.path.split("?")[0].rstrip("/")   # issue #55: Claude Code posts /v1/messages?beta=true''',
    '''        def _post(self):
            if not self._authorized():
                return
            path = self.path.split("?")[0].rstrip("/")   # issue #55: Claude Code posts /v1/messages?beta=true
            if path.startswith("/v1/") and self._maintenance():
                return''')

rep('''        def _authorized(self) -> bool:''', '''        def _maintenance(self) -> bool:
            """Local patch: while ~/strata-maintenance exists, only this machine's own requests get through."""
            f = os.path.expanduser("~/strata-maintenance")
            if not os.path.exists(f):
                return False
            if self.client_address[0] in ("127.0.0.1", "::1") and not self.headers.get("CF-Connecting-IP"):
                return False
            try:
                why = open(f, encoding="utf-8").read().strip()
            except OSError:
                why = ""
            print(f"[strata] maintenance: refused a request from "
                  f"{self.headers.get('CF-Connecting-IP') or self.client_address[0]}", flush=True)
            self._json(503, {"error": {"type": "overloaded_error",
                                       "message": "this AI server is under maintenance" + (f": {why}" if why else "") +
                                                  " - try again later"}})
            return True

        def _authorized(self) -> bool:''')
p.write_text(s, encoding="utf-8")
print("patched")

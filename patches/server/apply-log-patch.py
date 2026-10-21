#!/usr/bin/env python3
"""Patch Strata's server: the progress lines also show the speed over the last 3 seconds.

Usage: apply-log-patch.py <Strata source folder>

  14:02:11 reading the prompt: 13,824 of 16,789 tokens, 15 s so far, pp last chunk 1380 tok/s
  14:03:02 answering: 2799 of max 32768 tokens, 50.8 tok/s, tg 3s 41.3 tok/s, 66 s

The "[strata] " prefix of the server's lines is replaced by the time of day (HH:MM:SS).

The first figure of a generation line stays the average since the first token; "tg 3s" is what the engine did in the
last 3 seconds, so a slow stretch shows at once instead of pulling the average down slowly.  The prompt is read in
chunks and its position moves in steps, so a 3-second figure would read 0 between steps: "pp last chunk" is the
speed of the latest step instead (from the second step on; the first may be a reused prefix).
"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "_recent_rate" in s:
    sys.exit("already patched")


def rep(old, new):
    global s
    if old not in s:
        sys.exit("serve/server.py: the text to replace was not found:\n" + old[:200])
    s = s.replace(old, new, 1)


rep('''    def _progress(self, last_print, every=1.0, st=None):
        """A progress line in the server window every `every` seconds while a request runs."""
''', '''    def _recent_rate(self, key, request, value, now, window=3.0):
        """Local patch: tokens per second over the last `window` seconds of one counter of the running request (the
        prompt position or the generated tokens).  None until two samples lie apart; another request (its start
        time) or a counter that went back starts over."""
        slot = self.__dict__.setdefault("_recent_hist", {}).setdefault(key, [request, []])
        if slot[0] != request or (slot[1] and value < slot[1][-1][1]):
            slot[0], slot[1] = request, []
        hist = slot[1]
        hist.append((now, value))
        while len(hist) > 2 and now - hist[1][0] >= window:   # keep one sample at least `window` old
            hist.pop(0)
        t0, v0 = hist[0]
        return (value - v0) / (now - t0) if now - t0 >= 0.5 else None

    def _chunk_rate(self, key, request, value, now):
        """Local patch: the prompt is read in chunks, so its position moves in steps.  This is the speed of the
        latest step - the tokens it advanced by over the time since the step before - held until the next one.
        None until a second step (the first one may be a reused prefix, which costs no reading)."""
        slot = self.__dict__.setdefault("_chunk_hist", {}).setdefault(key, [None, None, None, None])
        if slot[0] != request or (slot[2] is not None and value < slot[2]):
            slot[:] = [request, None, None, None]
        if slot[2] is None:
            slot[1], slot[2] = now, value
        elif value != slot[2]:
            slot[3] = (value - slot[2]) / max(1e-6, now - slot[1])
            slot[1], slot[2] = now, value
        return slot[3]

    def _progress(self, last_print, every=1.0, st=None):
        """A progress line in the server window every `every` seconds while a request runs."""
''')
rep('''            print(f"[strata] reading the prompt: {done} tokens, {el:.0f} s so far", flush=True)
        else:''', '''            pp = (self._chunk_rate(("pp", id(st) if st is not None else 0), s.get("started"), pr[0], now)
                  if pr and pr[1] else None)
            print(f"[strata] reading the prompt: {done} tokens, {el:.0f} s so far"
                  + (f", pp last chunk {pp:.0f} tok/s" if pp is not None else ""), flush=True)
        else:''')
rep('''            print(f"[strata] {s['phase']}: {s['generated']} of {limit} tokens, {rate:.1f} tok/s, "
                  f"{el:.0f} s", flush=True)''', '''            tg = self._recent_rate(("tg", id(st) if st is not None else 0), s.get("started"), s["generated"], now)
            print(f"[strata] {s['phase']}: {s['generated']} of {limit} tokens, {rate:.1f} tok/s, "
                  + (f"tg 3s {tg:.1f} tok/s, " if tg is not None else "") + f"{el:.0f} s", flush=True)''')
# the server's "[strata] " line prefix becomes the time of day, so a saved log shows when each line was written
rep('''from typing import Iterator, Protocol
''', '''from typing import Iterator, Protocol


def print(*args, **kwargs):   # noqa: A001 - local patch: this module's lines carry a timestamp instead of "[strata]"
    """Every line this server prints with the "[strata] " prefix gets the time of day in its place."""
    import builtins
    if args and isinstance(args[0], str) and args[0].startswith("[strata] "):
        args = (time.strftime("%H:%M:%S ") + args[0][len("[strata] "):],) + args[1:]
    builtins.print(*args, **kwargs)
''')
# when a request sends the turn the model just generated back changed, say where: that turn is then read again
rep("""        defaults = {**self.sampling_defaults, **self.shared}   # the config's, then the Chat settings shared with apps
""", """        held, held_prompt = self.__dict__.get("_held_ids"), self.__dict__.get("_held_prompt", 0)
        if held and len(ids) > held_prompt:      # local patch: does this request continue the turn generated last?
            same, upto = 0, min(len(held), len(ids))
            while same < upto and held[same] == ids[same]:
                same += 1
            if held_prompt - 4 <= same < len(held) - 1:   # -4: the turn's opening tokens can merge differently
                try:
                    was = self.tok.decode(held[same:same + 16])
                    now_ = self.tok.decode(list(ids[same:same + 16]))
                    before = self.tok.decode(held[max(0, same - 16):same])
                    print(f"[strata] note: the last answer came back changed at token {max(0, same - held_prompt)} of its "
                          f"{len(held) - held_prompt}; after {before!r} the model had written {was!r}, the request has "
                          f"{now_!r} - the {len(held) - same} tokens from there are read again", flush=True)
                except Exception:   # noqa: BLE001 - a diagnostic line must never fail a request
                    pass
        defaults = {**self.sampling_defaults, **self.shared}   # the config's, then the Chat settings shared with apps
""")
rep("""                            if os.environ.get("STRATA_DEBUG") and raw_ids:
""", """                            try:   # local patch: what the engine now holds, for the note above
                                self._held_ids, self._held_prompt = [int(t) for t in ids] + [int(t) for t in raw_ids], len(ids)
                            except (TypeError, ValueError):
                                self._held_ids = None
                            if os.environ.get("STRATA_DEBUG") and raw_ids:
""")
# one line per request as it arrives (always, not only with STRATA_DEBUG), naming the client
rep('''def _debug_req(api, req, messages, tools, max_new, thinking, prompt_tokens):
    """One compact line per request while diagnosing blank/empty turns. Set STRATA_DEBUG=1 to enable."""
    if not os.environ.get("STRATA_DEBUG"):
        return
''', '''def _debug_req(api, req, messages, tools, max_new, thinking, prompt_tokens, who=None):
    """One line per request as it arrives (local patch: always on; STRATA_DEBUG=1 adds the raw-limits line)."""
    _last = messages[-1] if messages else {}
    _text = _last.get("content")
    _images = 0
    _roles = {}
    for _m in messages or []:
        _roles[_m.get("role")] = _roles.get(_m.get("role"), 0) + 1
        if isinstance(_m.get("content"), list):
            _images += sum(1 for _p in _m["content"] if isinstance(_p, dict) and _p.get("type") in ("image_url", "image", "input_image"))
    if isinstance(_text, list):
        _text = " ".join(_p.get("text", "") for _p in _text if isinstance(_p, dict))
    _preview = str(_text or "")[:100].replace(chr(10), " ")
    print(f"[strata] request ({api}) from {who or '?'}: {prompt_tokens:,} prompt tokens, {len(messages)} messages ("
          + ", ".join(f"{v} {k}" for k, v in _roles.items()) + f"), {len(tools or [])} tools"
          + (f", {_images} images" if _images else "") + f", thinking {'on' if thinking else 'off'}, "
          f"max {max_new} new tokens, {'streamed' if req.get('stream') else 'not streamed'}; "
          f"last {_last.get('role')}: {_preview!r}", flush=True)
    if not os.environ.get("STRATA_DEBUG"):
        return
''')
for api in ("openai", "responses", "anthropic"):
    rep(f'_debug_req("{api}", req, messages, tools, max_new, thinking, len(ids))',
        f'_debug_req("{api}", req, messages, tools, max_new, thinking, len(ids), '
        f'who=(self.headers.get("CF-Connecting-IP") or self.client_address[0]) + " " + (self.headers.get("User-Agent") or "")[:48])')

# a llama.cpp-style summary after every request
rep('''                            if finish == "length" and parser.state in ("reasoning", "rcall"):   # #530
''', '''                            if timings:   # local patch: a summary in llama.cpp's words
                                _pn, _pms = timings.get("prompt_n") or 0, timings.get("prompt_ms") or 0.0
                                _dn, _dms = timings.get("predicted_n") or 0, timings.get("predicted_ms") or 0.0
                                print(f"[strata]   prompt eval time = {_pms:10.2f} ms / {_pn:6d} tokens ("
                                      f"{(_pms / _pn if _pn else 0):8.2f} ms per token, "
                                      f"{(_pn / (_pms / 1000) if _pms else 0):8.2f} tokens per second); "
                                      f"{timings.get('cache_n', 0):,} more reused from the cache", flush=True)
                                _dr = (f"; drafts accepted {timings['draft_n_accepted']} of {timings['draft_n']} "
                                       f"({100 * timings['draft_n_accepted'] / max(1, timings['draft_n']):.0f}%)"
                                       if timings.get("draft_n") else "")
                                print(f"[strata]          eval time = {_dms:10.2f} ms / {_dn:6d} tokens ("
                                      f"{(_dms / _dn if _dn else 0):8.2f} ms per token, "
                                      f"{(_dn / (_dms / 1000) if _dms else 0):8.2f} tokens per second){_dr}", flush=True)
                                print(f"[strata]         total time = {_pms + _dms:10.2f} ms / {_pn + _dn:6d} tokens "
                                      f"(wall {el * 1000:.0f} ms from arrival)", flush=True)
                            if finish == "length" and parser.state in ("reasoning", "rcall"):   # #530
''')
p.write_text(s, encoding="utf-8")
print("patched")

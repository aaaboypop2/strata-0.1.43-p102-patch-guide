#!/usr/bin/env python3
"""Patch Strata's server (after apply-log-patch.py): while a prompt is read on a layer split, the progress line says
where each card is - which part of the prompt it reads and at which layer - from the engine's PL lines
(STRATA_STAGE_PROGRESS=1 in the config's env, engine with the multi-helper patch):

  01:40:12 reading the prompt: 0 of 74,978 tokens, 20 s so far; GPU0 done 0-4K, GPU1 layer 21 (12-24) of 0-4K, GPU2 -, GPU3 -

Usage: apply-stageprog-patch.py <Strata source folder>
"""
import sys
from pathlib import Path

p = Path(sys.argv[1]) / "serve/server.py"
s = p.read_text(encoding="utf-8")
if "stage_pos" in s:
    sys.exit("already patched")


def rep(old, new):
    global s
    if s.count(old) != 1:
        sys.exit("serve/server.py: the text to replace was not found once:\n" + old[:200])
    s = s.replace(old, new, 1)


# the engine object: where each card is
rep('''        self.progress = None             # (read, total) prompt tokens while a prompt is read, from PP lines''',
    '''        self.progress = None             # (read, total) prompt tokens while a prompt is read, from PP lines
        self.stage_pos = {}              # local patch: device -> (layer, first, end, chunk start, chunk tokens, time), PL lines''')
rep('''            if not line.startswith(("PP ", "INFO")):
                btrace("ctl<", self._ctl_mode, line.strip()[:60])''',
    '''            if line.startswith("PL "):          # local patch: a card's place in the prompt (STRATA_STAGE_PROGRESS)
                f = line.split()
                try:
                    self.stage_pos[int(f[1])] = (int(f[2]), int(f[3]), int(f[4]), int(f[5]), int(f[6]), time.time())
                except (ValueError, IndexError):
                    pass
                if time.monotonic() - self.__dict__.get("_pl_yield", 0.0) >= 3.0:   # let the progress line say it
                    self._pl_yield = time.monotonic()
                    yield None
                continue
            if not line.startswith(("PP ", "INFO")):
                btrace("ctl<", self._ctl_mode, line.strip()[:60])''')

# the progress line
rep('''            print(f"[strata] reading the prompt: {done} tokens, {el:.0f} s so far"
                  + (f", pp last chunk {pp:.0f} tok/s" if pp is not None else ""), flush=True)''',
    '''            cards = ""
            sp = getattr(self.engine, "stage_pos", None)
            if sp:   # local patch: where each card is (only what moved during this request)
                k = lambda v: f"{v / 1000:.0f}K" if v >= 1000 else str(v)   # noqa: E731
                parts = []
                for dev, (layer, lb, le, p0, T, t) in sorted(sp.items(), key=lambda kv: kv[1][1]):
                    if t < s.get("started", now):
                        parts.append(f"GPU{dev} -")
                    elif layer >= le:
                        parts.append(f"GPU{dev} done {k(p0)}-{k(p0 + T)}")
                    else:
                        parts.append(f"GPU{dev} layer {layer} ({lb}-{le - 1}) of {k(p0)}-{k(p0 + T)}")
                cards = "; " + ", ".join(parts)
            print(f"[strata] reading the prompt: {done} tokens, {el:.0f} s so far"
                  + (f", pp last chunk {pp:.0f} tok/s" if pp is not None else "") + cards, flush=True)''')
# the serial request loop (the one a normal request reads with) has its own reader
rep('''                if line.startswith("T "):
                    allow = silence
                    if cancel.is_set():
                        return
                    yield int(line[2:])''', '''                if line.startswith("PL "):   # local patch: a card's place in the prompt (STRATA_STAGE_PROGRESS)
                    f = line.split()
                    try:
                        self.stage_pos[int(f[1])] = (int(f[2]), int(f[3]), int(f[4]), int(f[5]), int(f[6]), time.time())
                    except (ValueError, IndexError):
                        pass
                    if time.monotonic() - self.__dict__.get("_pl_yield", 0.0) >= 3.0:
                        self._pl_yield = time.monotonic()
                        yield None
                    continue
                if line.startswith("T "):
                    allow = silence
                    if cancel.is_set():
                        return
                    yield int(line[2:])''')
p.write_text(s, encoding="utf-8")
print("patched")

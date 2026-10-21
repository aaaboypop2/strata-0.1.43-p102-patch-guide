# Quick Strata benchmark: decode speed (short chat) and prompt-reading speed (long prompt).
# Usage: .venv\Scripts\python.exe ..\bench.py <label>
import json
import os
import random
import statistics
import sys
import time

import requests

URL = os.environ.get("STRATA_URL", "http://127.0.0.1:22221") + "/v1/chat/completions"
label = sys.argv[1] if len(sys.argv) > 1 else "run"
import os
KEYFILE = os.path.expanduser("~/.strata-api-key")
HEADERS = {"Authorization": "Bearer " + open(KEYFILE).read().strip()} if os.path.exists(KEYFILE) else {}


def ask(content, max_tokens, effort="none"):
    r = requests.post(URL, headers=HEADERS, json={"model": "strata", "messages": [{"role": "user", "content": content}],
                                 "max_tokens": max_tokens, "reasoning_effort": effort, "temperature": 0}, timeout=1800)
    r.raise_for_status()
    return r.json()


def long_prompt(words, seed):
    rnd = random.Random(seed)
    vocab = ("server memory layer expert cache token window prompt graphics card driver system file model "
             "network buffer thread kernel compile python function value result table index report").split()
    body = " ".join(rnd.choice(vocab) for _ in range(words))
    return f"Run {seed}. Here are log notes:\n{body}\n\nIn one sentence, what are these notes about?"


out = {"label": label, "decode": [], "prompt": []}

j = ask("สวัสดี ช่วยอธิบายสั้น ๆ เป็นภาษาไทย 2 ประโยคว่า GPU ต่างจาก CPU อย่างไร", 200)
out["thai_reply"] = j["choices"][0]["message"]["content"]

decode_prompts = ["Write a 350-word short story about a lighthouse keeper who finds a message in a bottle.",
                  "Write a Python class implementing an LRU cache with get/put, with docstrings and a small usage example."]
for p in decode_prompts:
    for _ in range(2):
        t = ask(p, 500)["timings"]
        out["decode"].append(round(t["predicted_per_second"], 1))

sizes = [3000, 12000] + [int(x) for x in sys.argv[2:]] + [int(x) for x in os.environ.get("BENCH_EXTRA", "").split(",") if x]
for n, words in enumerate(sizes):
    seed = int(time.time()) + n
    t = ask(long_prompt(words, seed), 24)["timings"]
    out["prompt"].append({"tokens": t["prompt_n"], "tok_s": round(t["prompt_per_second"], 1)})

out["decode_median"] = statistics.median(out["decode"])
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
print(json.dumps(out, ensure_ascii=False, indent=1))
with open(os.path.expanduser("~/bench-results.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps(out, ensure_ascii=False) + "\n")

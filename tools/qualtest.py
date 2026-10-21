#!/usr/bin/env python3
# Quality check for prompt-path settings: facts hidden in long filler (3K/6K/9K tokens) and short arithmetic,
# greedy, thinking off.  Prints the score and saves every reply to ~/qual-<label>.jsonl to compare answers.
import json, os, random, sys, requests
URL = os.environ.get("STRATA_URL", "http://127.0.0.1:22221") + "/v1/chat/completions"
H = {"Authorization": "Bearer " + open(os.path.expanduser("~/.strata-api-key")).read().strip()}
label = sys.argv[1]
words = ("the system reads each layer of the model while the cache keeps experts close to the card and the "
         "driver moves data over the bus when the prompt is long and the window grows with every new token").split()
names = ["Xylo", "Brindle", "Quarto", "Mosswick", "Tellurian", "Pembrook", "Vantor", "Oskelin", "Druma", "Fenwick",
         "Galloway", "Hestia", "Ilvern", "Jorund", "Kestrel", "Lumora", "Marrow", "Nyssa", "Orrin", "Pallas"]
def ask(content, n=40):
    r = requests.post(URL, headers=H, timeout=1800, json={"model": "strata", "messages": [{"role": "user", "content": content}],
                      "max_tokens": n, "reasoning_effort": "none", "temperature": 0})
    r.raise_for_status(); return r.json()["choices"][0]["message"]["content"] or ""
out = open(os.path.expanduser(f"~/qual-{label}.jsonl"), "w", encoding="utf-8")
score = {}
for size in [int(x) for x in os.environ.get("QSIZES", "3000,6000,9000").split(",")]:
    rng = random.Random(size)
    facts = {n: rng.randint(1000, 9999) for n in names[:8]}
    body = [rng.choice(words) for _ in range(size)]
    for i, (n, code) in enumerate(facts.items()):
        pos = int((i + 0.5) / len(facts) * size)
        body.insert(pos, f". Note: the access code of project {n} is {code}. ")
    doc = " ".join(body)
    for n, code in facts.items():
        a = ask(f"{doc}\n\nQuestion: what is the access code of project {n}? Answer with the number only.")
        ok = str(code) in a
        score.setdefault(f"recall {size}", []).append(ok)
        out.write(json.dumps({"q": f"{size}:{n}", "ok": ok, "a": a}, ensure_ascii=False) + "\n")
rng = random.Random(7)
for i in range(20):
    a_, b_, c_ = rng.randint(12, 99), rng.randint(12, 99), rng.randint(100, 999)
    ans = a_ * b_ + c_
    a = ask(f"Compute {a_} * {b_} + {c_}. Answer with the number only.", 20)
    ok = str(ans) in a.replace(",", "")
    score.setdefault("arith", []).append(ok)
    out.write(json.dumps({"q": f"arith{i}", "ok": ok, "a": a}, ensure_ascii=False) + "\n")
for i, q in enumerate(["Explain in three sentences why the sky is blue.",
                       "Write a Python function that returns the n-th Fibonacci number iteratively.",
                       "อธิบายสั้นๆ ว่า CPU กับ GPU ต่างกันอย่างไร",
                       "List five prime numbers greater than 100."]):
    out.write(json.dumps({"q": f"free{i}", "ok": None, "a": ask(q, 120)}, ensure_ascii=False) + "\n")
print(label, " ".join(f"{k} {sum(v)}/{len(v)}" for k, v in score.items()), flush=True)

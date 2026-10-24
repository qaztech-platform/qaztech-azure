#!/usr/bin/env python3
"""Held-out evaluation: QAZTECH Azure Engineer 8B vs base Qwen3-8B on eval_full.jsonl (200), real-tool code checks."""
import collections, json, pathlib, subprocess, tempfile, time
import ollama
H = pathlib.Path.home() / "azure-engineer"
exec((H / "validate.py").read_text(encoding="utf-8").split("\nrecs = []")[0])
exec((H / "build_dataset.py").read_text(encoding="utf-8").split("\nsrc = {}")[0])
rows = [json.loads(l) for l in open(D / "dataset/eval_full.jsonl", encoding="utf-8")]
meta = [m for m in (json.loads(l) for l in open(D / "dataset/meta.jsonl", encoding="utf-8")) if m["split"] == "eval"]
assert len(rows) == len(meta), "eval и meta не совпали"
MODELS = [("QAZTECH Azure Engineer 8B", "qaztech-platform/azure-engineer:8b"), ("Qwen3-8B (база)", "qwen3:8b")]
client = ollama.Client(timeout=900)
res, ps_jobs = [], []
for name, model in MODELS:
    t_all = time.time()
    for i, (r, m) in enumerate(zip(rows, meta)):
        try:
            out = client.chat(model, messages=r["messages"][:2], think=False, keep_alive="30m",
                              options={"temperature": 0.3, "num_ctx": 8192, "num_predict": 3000})
            a = out["message"]["content"]
        except Exception as e:
            a = ""
            print("  ОШИБКА", i, repr(e)[:100], flush=True)
        rec = {"model": name, "i": i, "lang": m["lang"], "mode": m["mode"], "answer": a, "lang_ok": lang_of(a) in (None, m["lang"]),
               "think": "<think>" in a, "blocks": 0, "checked": 0, "bad_blocks": 0, "problems": []}
        for mm in FENCE.finditer(a):
            rec["blocks"] += 1
            tool = TOOL.get(mm.group(1).lower())
            if tool == "ps":
                ps_jobs.append((len(res), mm.group(2)))
            elif tool:
                try:
                    p = CHECK[tool](mm.group(2))
                except Exception as e:
                    p = [f"сбой проверки | {e!r}"[:100]]
                if p is not None:
                    rec["checked"] += 1
                    rec["bad_blocks"] += bool(p)
                    rec["problems"] += p
        res.append(rec)
        if (i + 1) % 20 == 0:
            print(f"{time.strftime('%H:%M')} {name}: {i + 1}/200, {(time.time() - t_all) / (i + 1):.0f} с на ответ", flush=True)
if ps_jobs:
    print("проверка PowerShell...", flush=True)
    with tempfile.TemporaryDirectory() as t:
        items, script = pathlib.Path(t) / "items.json", pathlib.Path(t) / "check.ps1"
        items.write_text(json.dumps([{"i": k, "code": c} for k, (_, c) in enumerate(ps_jobs)]), encoding="utf-8")
        script.write_text(PS1, encoding="utf-8")
        o = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), str(items)], capture_output=True, text=True, timeout=7200, env=ENV)
    line = next((l for l in reversed(o.stdout.splitlines()) if l.startswith("[")), "[]")
    for x in json.loads(line):
        rec = res[ps_jobs[x["i"]][0]]
        rec["checked"] += 1
        rec["bad_blocks"] += bool(x["problems"])
        rec["problems"] += x["problems"]
with open(D / "eval_results.jsonl", "w", encoding="utf-8") as f:
    for rec in res:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
lines = ["", "=== ОЦЕНКА НА 200 ОТЛОЖЕННЫХ ПРИМЕРАХ ===",
         f"{'модель':28} {'язык верный':>12} {'<think>':>8} {'с кодом':>8} {'блоков проверено':>17} {'блоков с ошибками':>18} {'весь код верен':>15}"]
for name, _ in MODELS:
    rr = [r for r in res if r["model"] == name]
    wc = [r for r in rr if r["checked"]]
    ch, bad = sum(r["checked"] for r in rr), sum(r["bad_blocks"] for r in rr)
    lines.append(f"{name:28} {100 * sum(r['lang_ok'] for r in rr) / len(rr):11.0f}% {sum(r['think'] for r in rr):8} {100 * sum(r['blocks'] > 0 for r in rr) / len(rr):7.0f}% "
                 f"{ch:17} {100 * bad / max(1, ch):17.0f}% {100 * sum(r['bad_blocks'] == 0 for r in wc) / max(1, len(wc)):14.0f}%")
for name, _ in MODELS:
    rr = [r for r in res if r["model"] == name]
    by = collections.defaultdict(list)
    for r in rr:
        by[r["lang"]].append(r["lang_ok"])
    lines.append(f"{name}: язык верный по языкам: " + ", ".join(f"{k} {100 * sum(v) / len(v):.0f}%" for k, v in sorted(by.items())))
    lines.append(f"{name}: частые ошибки кода: " + str(collections.Counter(p.split(' | ')[0] for r in rr for p in r["problems"]).most_common(5)))
print("\n".join(lines), flush=True)
(D / "eval_summary.txt").write_text("\n".join(lines), encoding="utf-8")
print("ОЦЕНКА ЗАВЕРШЕНА", time.strftime("%H:%M"), flush=True)

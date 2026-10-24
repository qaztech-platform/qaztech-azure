#!/usr/bin/env python3
"""QAZTECH Azure Engineer 8B: build train/eval sets (full = both teachers, core = Qwen only) from validated + repaired examples."""
import collections, hashlib, json, pathlib, random, re

D = pathlib.Path.home() / "azure-engineer/data"
OUT = D / "dataset"
OUT.mkdir(exist_ok=True)
SYSTEM = ("You are QAZTECH Azure Engineer, an assistant for Azure engineers. "
          "Reply in the user's language; keep code, commands and code comments in English.")
FENCE = re.compile(r"```([A-Za-z0-9_+-]*)[^\n]*\n(.*?)```", re.S)
TFCMD = re.compile(r"terraform\s+(init|plan|apply|validate|fmt|show|destroy|output|import|state|workspace|providers|version|login|graph|refresh|taint|untaint|console|get|test)\b")
PLACE = re.compile(r"<[A-Za-z][A-Za-z0-9 _.-]{1,40}>")
SHELL = {"bash", "sh", "shell", "console", "azurecli", "azurecli-interactive", "powershell", "azurepowershell", "azurepowershell-interactive", "pwsh", "ps1"}
LEAK = re.compile(r"the reference|reference above|documentation above|example above|provided reference|<<<|>>>|<think>", re.I)
KK = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")

TPL = {
    "write": {"en": "Write the code for this task:", "ru": "Напиши код для этой задачи:", "kk": "Осы тапсырмаға код жазып беріңіз:",
              "ar": "اكتب الكود لهذه المهمة:", "es": "Escribe el código para esta tarea:"},
    "fix": {"en": "This code doesn't work. Find and fix the error:", "ru": "Этот код не работает. Найди и исправь ошибку:",
            "kk": "Бұл код жұмыс істемейді. Қатені тауып, түзетіңіз:", "ar": "هذا الكود لا يعمل. ابحث عن الخطأ وأصلحه:",
            "es": "Este código no funciona. Encuentra y corrige el error:"},
    "explain": {"en": "Explain what this code does:", "ru": "Объясни, что делает этот код:", "kk": "Бұл код не істейтінін түсіндіріп беріңіз:",
                "ar": "اشرح ما يفعله هذا الكود:", "es": "Explica qué hace este código:"},
    "finops": {"en": "Review this code for cost and add the required tags:", "ru": "Проверь этот код на стоимость и добавь обязательные теги:",
               "kk": "Осы кодты шығын тұрғысынан тексеріп, міндетті тегтерді қосыңыз:", "ar": "راجع هذا الكود من حيث التكلفة وأضف الوسوم المطلوبة:",
               "es": "Revisa este código en cuanto a costes y añade las etiquetas obligatorias:"},
    "troubleshoot": {"en": "The deployment failed. Find the cause and fix it:", "ru": "Развёртывание упало. Найди причину и исправь:",
                     "kk": "Орналастыру сәтсіз аяқталды. Себебін тауып, түзетіңіз:", "ar": "فشل النشر. حدّد السبب وأصلحه:",
                     "es": "El despliegue falló. Encuentra la causa y corrígelo:"},
}
PSH = {"powershell", "azurepowershell", "azurepowershell-interactive", "pwsh", "ps1"}

def user_lang(u, lang):
    t = re.sub(r"`[^`\n]*`", " ", FENCE.sub(" ", u))
    ar = sum("\u0600" <= c <= "\u06ff" for c in t)
    cy = sum("\u0400" <= c <= "\u04ff" for c in t)
    kk = sum(c in KK for c in t)
    low = t.lower()
    if lang == "ar":
        return "ok" if ar >= 5 else "add"
    if lang == "kk":
        return "ok" if kk >= 2 else ("drop" if cy >= 30 else "add")
    if lang == "ru":
        return "drop" if kk >= 2 else ("ok" if cy >= 5 else "add")
    if lang == "es":
        es = len(re.findall(r"[ñáéíóú¿¡]", low)) + len(re.findall(r"\b(el|los|las|que|para|una|con|del|es|por|cómo|qué)\b", low))
        return "ok" if es >= 2 else ("drop" if cy or ar else "add")
    return "drop" if cy or ar else "ok"

def load(name):
    p = D / name
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()] if p.exists() else []

def retag(text):
    def f(m):
        lines = [x.strip() for x in m.group(2).splitlines() if x.strip() and not x.strip().startswith("#")]
        if m.group(1).lower() in ("hcl", "terraform", "tf") and lines and all(TFCMD.match(x) for x in lines):
            return "```bash\n" + m.group(2) + "```"
        return m.group(0)
    return FENCE.sub(f, text)

def lang_of(t):
    t = re.sub(r"`[^`\n]*`", " ", FENCE.sub(" ", t))
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 30:
        return None
    n = len(letters)
    if sum("\u0600" <= c <= "\u06ff" for c in letters) / n > 0.4:
        return "ar"
    if sum("\u0400" <= c <= "\u04ff" for c in letters) / n > 0.4:
        return "kk" if sum(c in KK for c in t) >= 3 else "ru"
    low = t.lower()
    es = len(re.findall(r"[ñáéíóú¿¡]", low)) + len(re.findall(r"\b(el|los|las|que|para|una|con|del|se|es)\b", low))
    en = len(re.findall(r"\b(the|and|to|of|is|for|with|this|you)\b", low))
    return "es" if es > en else "en"

src = {}
for name in ("pilot.jsonl", "pilot_retry.jsonl", "qwen_main.jsonl", "minimax_hard.jsonl"):
    for r in load(name):
        if r.get("ok"):
            r.setdefault("teacher", "minimax-m3.1-flash:cloud")
            src[r["id"]] = r
val = {v["id"]: v for v in load("validation.jsonl")}
cand = {r["id"]: r for r in load("repaired.jsonl")}
for i, v in val.items():
    if v["pass"] and i in src:
        cand[i] = src[i]
drop, seen, keep = collections.Counter(), set(), []
for r in cand.values():
    u, a = r["messages"][0]["content"].strip(), retag(r["messages"][1]["content"].strip())
    if LEAK.search(u + "\n" + a):
        drop["утечка подсказки/рассуждений"] += 1; continue
    if any(m.group(1).lower() in PSH and PLACE.search(m.group(2)) for m in FENCE.finditer(a)):
        drop["заглушки <...> в коде PowerShell"] += 1; continue
    la = lang_of(a)
    if la and la != r["lang"]:
        drop[f"ответ не на том языке ({r['lang']})"] += 1; continue
    st = user_lang(u, r["lang"])
    if st == "drop" or (st == "add" and r["mode"] not in TPL):
        drop[f"вопрос не на том языке или без текста ({r['lang']}, {r['mode']})"] += 1; continue
    if st == "add":
        u = TPL[r["mode"]][r["lang"]] + "\n\n" + u
        drop["(не отброшено) добавлена подводка на языке"] += 1
    h = hashlib.sha1(re.sub(r"\s+", " ", u.lower()).encode()).hexdigest()
    if h in seen:
        drop["дубликат вопроса"] += 1; continue
    seen.add(h)
    keep.append({"id": r["id"], "teacher": "qwen" if "Qwen" in r["teacher"] else "minimax", "mode": r["mode"], "kind": r["kind"],
                 "lang": r["lang"], "repaired": bool(r.get("repaired")),
                 "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": u}, {"role": "assistant", "content": a}]})
random.Random(42).shuffle(keep)
ev, tr = keep[:200], keep[200:]
def write(name, rows):
    with open(OUT / name, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"messages": r["messages"]}, ensure_ascii=False) + "\n")
write("train_full.jsonl", tr); write("eval_full.jsonl", ev)
write("train_core.jsonl", [r for r in tr if r["teacher"] == "qwen"]); write("eval_core.jsonl", [r for r in ev if r["teacher"] == "qwen"])
with open(OUT / "meta.jsonl", "w", encoding="utf-8") as f:
    for r in keep:
        f.write(json.dumps({k: r[k] for k in ("id", "teacher", "mode", "kind", "lang", "repaired")} | {"split": "eval" if r in ev else "train"}, ensure_ascii=False) + "\n")
print(f"КАНДИДАТОВ {len(cand)} | ОТБРОШЕНО {sum(drop.values())} | В ДАТАСЕТЕ {len(keep)} (обучение {len(tr)}, оценка {len(ev)})")
for k, v in drop.most_common():
    print(f"  отброшено {v:4} × {k}")
C = lambda key: dict(collections.Counter(r[key] for r in keep).most_common())
print("учителя:", C("teacher"), "| исправленных:", sum(r["repaired"] for r in keep))
print("режимы:", C("mode")); print("языки:", C("lang")); print("инструменты:", C("kind"))
print(f"core (только Qwen): обучение {sum(r['teacher'] == 'qwen' for r in tr)}, оценка {sum(r['teacher'] == 'qwen' for r in ev)}")
print("самый длинный пример, символов:", max(sum(len(m["content"]) for m in r["messages"]) for r in keep))

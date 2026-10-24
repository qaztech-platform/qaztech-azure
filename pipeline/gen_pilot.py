#!/usr/bin/env python3
"""QAZTECH Azure Engineer 8B: corpus inventory + 14-example pilot. Teacher: MiniMax M3.1 Flash (Ollama cloud)."""
import collections, hashlib, json, pathlib, random, re, time
from concurrent.futures import ThreadPoolExecutor
import ollama
CLIENT = ollama.Client(timeout=240)

HOME = pathlib.Path.home()
OUT = HOME / "azure-engineer/data"
TEACHER = "minimax-m3.1-flash:cloud"
NAME = {"cli": "Azure CLI", "powershell": "Azure PowerShell", "bicep": "Bicep", "arm": "ARM JSON template",
        "terraform": "Terraform (azurerm/azapi)", "governance": "Azure Policy or custom RBAC role JSON",
        "kql": "KQL (Log Analytics / Resource Graph)", "pipeline": "Azure Pipelines or GitHub Actions YAML"}
TAG = {"cli": "bash", "powershell": "powershell", "bicep": "bicep", "arm": "json", "terraform": "hcl",
       "governance": "json", "kql": "kusto", "pipeline": "yaml"}
FIXED = {"azurecli": "cli", "azurecli-interactive": "cli", "azurepowershell": "powershell",
         "azurepowershell-interactive": "powershell", "powershell": "powershell", "bicep": "bicep",
         "terraform": "terraform", "hcl": "terraform", "kusto": "kql", "kql": "kql"}
DEPLOY = ["cli", "powershell", "bicep", "arm", "terraform"]
LANG = {"en": "English", "ru": "Russian", "kk": "Kazakh", "ar": "Arabic", "es": "Spanish"}
MODES = {
    "write": (list(NAME), "The engineer asks for working code for a realistic task based on the reference. The assistant gives complete code, then a short explanation and one security or cost note."),
    "fix": (list(NAME), "The engineer pastes code with ONE realistic bug (wrong parameter, wrong property, missing dependency or wrong scope) and the symptom. The assistant names the bug, gives the corrected code and explains why it failed."),
    "explain": (list(NAME), "The engineer pastes code from the reference and asks what it does. The assistant explains it step by step and says what to check before running it."),
    "convert": (["cli", "powershell", "bicep", "terraform"], "The engineer gives code in {src} and asks for the same in {dst}. The assistant gives the converted code and lists the important differences."),
    "migrate": (["arm"], "The engineer pastes an ARM JSON template and asks to migrate it to {dst}. The assistant gives clean, parameterized {dst} code (parameters with descriptions, no hard-coded names or locations, symbolic references instead of resourceId() and dependsOn where {dst} allows), then lists what changed and how to test it (what-if or plan)."),
    "finops": (DEPLOY, "The engineer pastes deployment code and asks for a cost review. The assistant returns improved code: adds missing tags Environment, CostCenter and Owner, parameterizes the environment, and for dev/test selects lower-cost options (for example B-series VM sizes, Standard_LRS storage, Basic or Free tiers where they exist) while keeping production settings unchanged. It lists each change and why. Never state exact prices: they change by region and over time."),
    "troubleshoot": (DEPLOY + ["pipeline"], "The engineer pastes deployment code or a pipeline plus a realistic failed deployment or pipeline log that contains ONE real Azure error code (for example AuthorizationFailed, ParentResourceNotFound, InvalidTemplate, InvalidTemplateDeployment, SkuNotAvailable, QuotaExceeded, ResourceGroupNotFound, InvalidResourceReference, RequestDisallowedByPolicy). The assistant explains the cause, gives the fix in code or commands, and how to verify it. Use only real Azure error codes."),
}
FENCE = re.compile(r"```([A-Za-z-]+)[^\n]*\n(.*?)```", re.S)

def classify(tag, code):
    t = tag.lower()
    if t in FIXED:
        return FIXED[t]
    if t == "json":
        if "deploymentTemplate" in code:
            return "arm"
        if '"policyRule"' in code or "assignableScopes" in code.replace("AssignableScopes", "assignableScopes"):
            return "governance"
    if t in ("yaml", "yml") and ("steps:" in code or "jobs:" in code) and ("azure" in code.lower()):
        return "pipeline"
    return None

samples, seen, files = collections.defaultdict(list), set(), 0
for p in (HOME / "tutor").rglob("*.md"):
    try:
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    files += 1
    for m in FENCE.finditer(t):
        code = m.group(2).strip()
        k = classify(m.group(1), code)
        h = hashlib.md5(code.encode()).hexdigest()
        if k and h not in seen and 3 <= code.count("\n") + 1 <= 120:
            seen.add(h)
            samples[k].append({"path": str(p), "ctx": t[max(0, m.start() - 1500):m.end() + 300]})
print("ФАЙЛОВ .md ПРОСМОТРЕНО:", files)
for k in NAME:
    print(f"  {NAME[k]:42} уникальных примеров кода: {len(samples[k])}")

random.seed(42)
langs, jobs, i = list(LANG), [], 0
for mode, (kinds, desc0) in MODES.items():
    avail = [k for k in kinds if samples[k]]
    if not avail:
        print("  НЕТ ПРИМЕРОВ для режима", mode)
        continue
    for rep in range(2):
        k = random.choice(avail)
        lang = langs[i % 5]
        s = random.choice(samples[k])
        desc, tag = desc0, TAG[k]
        if mode == "convert":
            dst = random.choice([x for x in kinds if x != k])
            desc, tag = desc0.format(src=NAME[k], dst=NAME[dst]), f"{TAG[k]} and {TAG[dst]}"
        if mode == "migrate":
            dst = ["Bicep", "Terraform (azurerm)"][rep]
            desc, tag = desc0.format(dst=dst), "json and " + ("bicep" if rep == 0 else "hcl")
        prompt = f"""You create ONE training example for QAZTECH Azure Engineer, an assistant for Azure engineers.
Mode: {mode}. {desc}
Main tool: {NAME[k]}.
Engineer's language: {LANG[lang]}. The engineer's message and all of the assistant's prose must be in {LANG[lang]}; code, commands, property names, log lines and code comments stay in English.
Rules:
- Use only commands, parameters, resource types and API versions that appear in the reference or that you are certain exist. Never invent flags or error codes.
- When code creates resources that support tags, include the tags Environment, CostCenter and Owner (as parameters where the tool supports parameters).
- Use placeholder names such as rg-demo, vnet-demo, stdemo001; no real subscription IDs, keys or passwords.
- Put code in fenced blocks with the language tag {tag}.
- The assistant's answer must stand alone: never mention "the reference", "the documentation above" or "the example".
Reference (Microsoft Learn, CC BY 4.0):
<<<
{s['ctx']}
>>>
Return JSON only: {{"user": "...", "assistant": "..."}}"""
        jobs.append({"id": f"pilot-{i:02d}", "kind": k, "mode": mode, "lang": lang, "source": s["path"], "prompt": prompt})
        i += 1

def gen(j):
    t0 = time.time()
    try:
        msgs = [{"role": "user", "content": j["prompt"]}]
        try:
            r = CLIENT.chat(TEACHER, messages=msgs, think="medium", options={"temperature": 0.7})
        except Exception as e1:
            if "think" not in str(e1).lower():
                raise
            r = CLIENT.chat(TEACHER, messages=msgs, options={"temperature": 0.7})
        print(f"  готов {j['id']} {j['mode']:12} {time.time()-t0:5.0f} с | ответ {len(r['message']['content']):6} симв. | рассуждения {len(r['message'].get('thinking') or ''):6} симв.", flush=True)
        m = re.search(r"\{.*\}", r["message"]["content"], re.S)
        d = json.loads(m.group(0))
        ok = isinstance(d.get("user"), str) and "```" in str(d.get("assistant", ""))
        return {**{x: j[x] for x in ("id", "kind", "mode", "lang", "source")}, "ok": ok, "sec": round(time.time() - t0, 1),
                "tokens": r.get("eval_count", 0), "messages": [{"role": "user", "content": d["user"]}, {"role": "assistant", "content": d["assistant"]}]}
    except Exception as e:
        print(f"  ОШИБКА {j['id']} {j['mode']} {time.time()-t0:.0f} с: {repr(e)[:120]}", flush=True)
        return {**{x: j[x] for x in ("id", "kind", "mode", "lang")}, "ok": False, "sec": round(time.time() - t0, 1), "err": repr(e)[:200]}

t0 = time.time()
with ThreadPoolExecutor(4) as ex:
    res = list(ex.map(gen, jobs))
wall = time.time() - t0
with open(OUT / "pilot.jsonl", "w", encoding="utf-8") as f:
    for r in res:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
good = [r for r in res if r["ok"]]
print(f"\nПИЛОТ: годных {len(good)}/{len(res)} | за {wall:.0f} с | в среднем {sum(r['sec'] for r in res)/max(1, len(res)):.0f} с на пример")
for r in res:
    print(" ", r["id"], r["kind"], r["mode"], r["lang"], "OK" if r["ok"] else "ОШИБКА " + r.get("err", "нет кода/JSON"))
ru = next((r for r in good if r["lang"] == "ru"), good[0] if good else None)
if ru:
    print("\n=== ПРИМЕР", ru["id"], ru["kind"], ru["mode"], "===\nИНЖЕНЕР:", ru["messages"][0]["content"], "\n\nОТВЕТ:", ru["messages"][1]["content"][:2500])

#!/usr/bin/env python3
"""QAZTECH Azure Engineer: repair answers that failed validation. Teacher Qwen3-30B, tool-grounded hints, up to 2 attempts, revalidated.
Usage: repair.py [MAX_ITEMS]"""
import collections, json, pathlib, re, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
import ollama
HOME = pathlib.Path.home()
exec((HOME / "azure-engineer/validate.py").read_text(encoding="utf-8").split("\nrecs = []")[0])

TEACHER = "hf.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF:UD-Q4_K_XL"
CLIENT = ollama.Client(timeout=900)
SCHEMA = {"type": "object", "properties": {"assistant": {"type": "string"}}, "required": ["assistant"]}
MAXN, WORKERS = (int(sys.argv[1]) if len(sys.argv) > 1 else 10 ** 9), 3
OK_F, FAIL_F = D / "repaired.jsonl", D / "repaired_fail.jsonl"

_TFS = {}
if (D / "tf_schema.json").exists():
    _ps = json.load(open(D / "tf_schema.json"))["provider_schemas"]
    _TFS = {**_ps.get("registry.terraform.io/hashicorp/azurerm", {}).get("resource_schemas", {}), **_ps.get("registry.terraform.io/azure/azapi", {}).get("resource_schemas", {})}

def tf_extra(answer, probs):
    if not any(p.startswith("tf ") for p in probs):
        return ""
    names = sorted(set(re.findall(r'resource\s+"((?:azurerm|azapi)_[a-z0-9_]+)"', answer)) & set(_TFS))[:5]
    out = ['Terraform: target hashicorp/azurerm 5.x and azure/azapi 2.x. Use ONLY the arguments and nested blocks listed below. '
           'If azapi resources are used, declare azapi in required_providers with source "azure/azapi".']
    for n in names:
        b = _TFS[n]["block"]
        at = {a: v for a, v in b.get("attributes", {}).items() if not v.get("deprecated")}
        req = [a for a, v in at.items() if v.get("required")]
        opt = [a for a, v in at.items() if v.get("optional") and a != "id"]
        blocks = [bn + (" (required)" if bv.get("min_items", 0) > 0 else "") for bn, bv in b.get("block_types", {}).items()]
        out.append(f"- {n}: required: {', '.join(req) or 'none'}; optional: {', '.join(opt[:80])}; nested blocks: {', '.join(blocks[:40]) or 'none'}")
    return "\n".join(out) + "\n"

LANGN = {"en": "English", "ru": "Russian", "kk": "Kazakh", "ar": "Arabic", "es": "Spanish"}

def run(j):
    try:
        return j, CHECK[j[1]](j[2])
    except Exception as e:
        return j, [f"{j[1]} сбой проверки | {repr(e)[:80]}"]

def pwsh_json(script, args):
    with tempfile.TemporaryDirectory() as t:
        s = pathlib.Path(t) / "s.ps1"
        s.write_text(script, encoding="utf-8")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(s), *args], capture_output=True, text=True, timeout=7200, env=ENV)
    line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith(("[", "{"))), None)
    return json.loads(line) if line else None

PS_PARAMS = r"""$WarningPreference = 'SilentlyContinue'; $ProgressPreference = 'SilentlyContinue'
$common = [System.Management.Automation.PSCmdlet]::CommonParameters + [System.Management.Automation.PSCmdlet]::OptionalCommonParameters
$o = @{}
foreach ($n in $args) { $c = Get-Command -Name $n -ErrorAction SilentlyContinue; if ($c) { $o[$n] = @($c.Parameters.Keys | Where-Object { $common -notcontains $_ }) } }
$o | ConvertTo-Json -Depth 3 -Compress
"""
PSP = {}
def need_params(problem_lists):
    names = {m.group(1) for ps_ in problem_lists for p in ps_ for m in [re.search(r"нет параметра \| -\S+ у (\S+)", p)] if m} - set(PSP)
    if names:
        PSP.update(pwsh_json(PS_PARAMS, sorted(names)) or {})

def hints(problems):
    out = []
    for p in problems:
        k, _, d = p.partition(" | ")
        if k == "az неизвестный флаг":
            w = d.split()
            cmd, flag = " ".join(w[1:-1]), w[-1]
            valid = sorted(x for x in (az_help(cmd) or set()) if x.startswith("--"))
            out.append(f"- `{flag}` is not a valid option of `az {cmd}` in the current Azure CLI. Valid options: {', '.join(valid[:70])}")
        elif k == "az неизвестная команда":
            out.append(f"- `{d}` is not an existing Azure CLI command. Use the correct existing command.")
        elif k == "ps нет параметра":
            m = re.match(r"-(\S+) у (\S+)", d)
            if m:
                out.append(f"- `{m.group(2)}` has no parameter `-{m.group(1)}`. Valid parameters: {', '.join(PSP.get(m.group(2), [])[:70])}")
        elif k == "ps нет командлета":
            out.append(f"- `{d}` is not an existing Az PowerShell cmdlet. Use the correct existing cmdlet.")
        elif k == "ps синтаксис":
            out.append(f"- PowerShell syntax error: {d}. Do not use <placeholders>; use names such as rg-demo.")
        elif k == "незакрытый блок кода":
            out.append("- A fenced code block is not closed: close every ``` block.")
        else:
            out.append(f"- {k}: {d}")
    return "\n".join(dict.fromkeys(out))

def validate_answers(items):
    res, jobs, ps = {k: [] for k, _ in items}, [], []
    for k, a in items:
        if a.count("```") % 2:
            res[k].append("незакрытый блок кода | ")
        for m in FENCE.finditer(a):
            tool = TOOL.get(m.group(1).lower())
            if tool == "ps":
                ps.append((k, m.group(2)))
            elif tool:
                jobs.append((k, tool, m.group(2)))
    if any(j[1] == "tf" for j in jobs):
        tf_prepare()
    with ThreadPoolExecutor(6) as ex:
        for (k, _, _), out in ex.map(run, jobs):
            if out:
                res[k] += out
    if ps:
        for x in pwsh_json(PS1, [None]) if False else []:
            pass
        with tempfile.TemporaryDirectory() as t:
            items_f = pathlib.Path(t) / "items.json"
            items_f.write_text(json.dumps([{"i": i, "code": c} for i, (_, c) in enumerate(ps)]), encoding="utf-8")
            out = pwsh_json(PS1, [str(items_f)]) or []
        for x in out:
            if x["problems"]:
                res[ps[x["i"]][0]] += x["problems"]
    return res

recs = {}
for name in ("pilot.jsonl", "pilot_retry.jsonl", "qwen_main.jsonl", "minimax_hard.jsonl"):
    if (D / name).exists():
        for l in open(D / name, encoding="utf-8"):
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("ok"):
                r.setdefault("teacher", "minimax-m3.1-flash:cloud")
                recs[r["id"]] = r
val = {v["id"]: v for v in (json.loads(l) for l in open(D / "validation.jsonl", encoding="utf-8"))}
done = {json.loads(l)["id"] for f in (OK_F, FAIL_F) if f.exists() for l in open(f, encoding="utf-8")}
todo = [i for i, v in val.items() if not v["pass"] and i in recs and i not in done and not any(p.startswith(("bicep",) if os.environ.get("ONLY") == "tf " else ("tf ", "bicep")) for p in v["problems"]) and (not os.environ.get("ONLY") or any(p.startswith(os.environ["ONLY"]) for p in v["problems"]))][:MAXN]
print(f"НА ИСПРАВЛЕНИЕ: {len(todo)} (Terraform отложен) | потоков {WORKERS}", flush=True)
cur = {i: (recs[i]["messages"][1]["content"], val[i]["problems"]) for i in todo}
fixed_ok, attempt_of = {}, {}

def fix(item):
    rid, (answer, probs) = item
    t0 = time.time()
    prompt = f"""You are fixing ONE training answer of QAZTECH Azure Engineer, an assistant for Azure engineers.
Real tools (current Azure CLI, Az PowerShell modules, Bicep CLI) found these errors in the assistant's answer:
{hints(probs)}
{tf_extra(answer, probs)}Rewrite the assistant's answer so that every error is fixed:
- Write ALL prose in {LANGN[recs[rid]['lang']]}, exactly as in the original answer (code, commands and code comments stay in English). Keep the same structure and intent; change only what is needed; keep the tags Environment, CostCenter and Owner where resources support tags.
- Use only commands, parameters, properties, resource types and API versions that really exist; if unsure, use a simpler construct that is certainly correct.
- Code must be complete and runnable as shown: no <placeholders> and no "..." inside code; use names such as rg-demo.
- Close every fenced code block.
- Bicep: current syntax only. Decorators such as @description('...') go on the line above a param; never the legacy `param x string {{ ... }}` form. Never break a line directly after `=`. Use only resource types and API versions you are certain exist.
Engineer's message:
<<<
{recs[rid]['messages'][0]['content']}
>>>
Assistant's answer with errors:
<<<
{answer}
>>>
Return JSON only: {{"assistant": "..."}}"""
    try:
        r = CLIENT.chat(TEACHER, messages=[{"role": "user", "content": prompt}], format=SCHEMA, options={"temperature": 0.3, "num_ctx": 12288, "num_predict": 4096}, keep_alive=-1)
        a = json.loads(r["message"]["content"], strict=False)["assistant"]
        print(f"  {time.strftime('%H:%M')} {rid} попытка {attempt}: ответ за {time.time() - t0:.0f} с", flush=True)
        return rid, a
    except Exception as e:
        print(f"  {time.strftime('%H:%M')} {rid} попытка {attempt}: ОШИБКА {repr(e)[:100]}", flush=True)
        return rid, None

for attempt in (1,):
    if not cur:
        break
    t0 = time.time()
    need_params([p for _, p in cur.values()])
    with ThreadPoolExecutor(WORKERS) as ex:
        out = list(ex.map(fix, cur.items()))
    print("  проверка исправленных ответов инструментами...", flush=True)
    vres = validate_answers([(rid, a) for rid, a in out if a])
    nxt = {}
    for rid, a in out:
        if a and not vres[rid]:
            fixed_ok[rid], attempt_of[rid] = a, attempt
        else:
            nxt[rid] = (a or cur[rid][0], vres.get(rid) or cur[rid][1])
    print(f"ПОПЫТКА {attempt}: исправлено {sum(1 for v in attempt_of.values() if v == attempt)} из {len(cur)} за {time.time() - t0:.0f} с", flush=True)
    cur = nxt

with open(OK_F, "a", encoding="utf-8") as f:
    for rid, a in fixed_ok.items():
        r = recs[rid]
        f.write(json.dumps({**r, "messages": [r["messages"][0], {"role": "assistant", "content": a}], "repaired": attempt_of[rid], "repair_teacher": TEACHER}, ensure_ascii=False) + "\n")
with open(FAIL_F, "a", encoding="utf-8") as f:
    for rid, (a, probs) in cur.items():
        f.write(json.dumps({"id": rid, "problems": probs}, ensure_ascii=False) + "\n")
tw = lambda i: "qwen" if "Qwen" in recs[i]["teacher"] else "minimax"
print(f"\nИТОГ: исправлено {len(fixed_ok)}/{len(todo)} | по учителям: {dict(collections.Counter(tw(i) for i in fixed_ok))} из {dict(collections.Counter(tw(i) for i in todo))}")
print("не исправлено, частые причины:", collections.Counter(p.split(' | ')[0] for _, ps_ in cur.values() for p in ps_).most_common(5))
ex_id = next((i for i in fixed_ok if any(p.startswith(("ps нет параметра", "az неизвестный флаг", "bicep BCP0")) for p in val[i]["problems"])), next(iter(fixed_ok), None))
if ex_id:
    print(f"\n=== ПРИМЕР {ex_id} ({recs[ex_id]['mode']}, {recs[ex_id]['lang']}) ===\nБЫЛО ОШИБОК:", *val[ex_id]["problems"][:5], sep="\n  ")
    print("\nСТАЛО (начало ответа):\n" + fixed_ok[ex_id][:2000])

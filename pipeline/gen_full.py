#!/usr/bin/env python3
"""QAZTECH Azure Engineer 8B: full data generation, resumable.
Usage: gen_full.py TEACHER OUTFILE WORKERS MODES(comma-separated) TARGET [DEADLINE_UTC]"""
import datetime as dt, json, pathlib, random, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
SRC = (pathlib.Path.home() / "azure-engineer/gen_pilot.py").read_text(encoding="utf-8")
exec(SRC.split("random.seed(42)")[0])

TEACHER, OUTNAME, WORKERS, SEL, TARGET = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4].split(","), int(sys.argv[5])
DEADLINE = dt.datetime.fromisoformat(sys.argv[6].replace("Z", "+00:00")) if len(sys.argv) > 6 else None
OUTF, PFX = OUT / OUTNAME, OUTNAME.split(".")[0]
CLIENT = ollama.Client(timeout=600)
LOCAL = not TEACHER.endswith(":cloud")
SCHEMA = {"type": "object", "properties": {"user": {"type": "string"}, "assistant": {"type": "string"}}, "required": ["user", "assistant"]}
LIMIT, LANGS, LOCK = 1200, list(LANG), threading.Lock()
assert all(m in MODES for m in SEL), "неизвестный режим"
TFS = {}
_tf = HOME / "azure-engineer/data/tf_schema.json"
if _tf.exists():
    _ps = json.load(open(_tf))["provider_schemas"]
    TFS = {**_ps.get("registry.terraform.io/hashicorp/azurerm", {}).get("resource_schemas", {}), **_ps.get("registry.terraform.io/azure/azapi", {}).get("resource_schemas", {})}

def _snake(t):
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", t)).lower()
    return t[:-2] if t.endswith("sses") else t[:-3] + "y" if t.endswith("ies") else t[:-1] if t.endswith("s") and not t.endswith("ss") else t

ALIAS = {
    "keyvault/vaults": ["azurerm_key_vault"], "containerservice/managedclusters": ["azurerm_kubernetes_cluster"],
    "operationalinsights/workspaces": ["azurerm_log_analytics_workspace"], "operationalinsights/clusters": ["azurerm_log_analytics_cluster"],
    "web/sites": ["azurerm_linux_web_app", "azurerm_windows_web_app"], "web/serverfarms": ["azurerm_service_plan"],
    "web/staticsites": ["azurerm_static_web_app"], "compute/virtualmachines": ["azurerm_linux_virtual_machine", "azurerm_windows_virtual_machine"],
    "compute/virtualmachinescalesets": ["azurerm_linux_virtual_machine_scale_set"], "compute/disks": ["azurerm_managed_disk"],
    "network/loadbalancers": ["azurerm_lb"], "documentdb/databaseaccounts": ["azurerm_cosmosdb_account"],
    "containerregistry/registries": ["azurerm_container_registry"], "insights/components": ["azurerm_application_insights"],
    "insights/diagnosticsettings": ["azurerm_monitor_diagnostic_setting"], "insights/actiongroups": ["azurerm_monitor_action_group"],
    "insights/metricalerts": ["azurerm_monitor_metric_alert"], "insights/scheduledqueryrules": ["azurerm_monitor_scheduled_query_rules_alert_v2"],
    "authorization/policyassignments": ["azurerm_resource_group_policy_assignment", "azurerm_subscription_policy_assignment"],
    "authorization/policysetdefinitions": ["azurerm_policy_set_definition"], "recoveryservices/vaults": ["azurerm_recovery_services_vault"],
    "automation/automationaccounts": ["azurerm_automation_account"], "eventhub/namespaces": ["azurerm_eventhub_namespace"],
    "servicebus/namespaces": ["azurerm_servicebus_namespace"], "app/containerapps": ["azurerm_container_app"],
    "app/managedenvironments": ["azurerm_container_app_environment"], "cdn/profiles": ["azurerm_cdn_frontdoor_profile"],
    "cognitiveservices/accounts": ["azurerm_cognitive_account"], "machinelearningservices/workspaces": ["azurerm_machine_learning_workspace"],
    "sql/servers": ["azurerm_mssql_server"], "dbforpostgresql/flexibleservers": ["azurerm_postgresql_flexible_server"],
    "dbformysql/flexibleservers": ["azurerm_mysql_flexible_server"], "cache/redis": ["azurerm_redis_cache"],
    "apimanagement/service": ["azurerm_api_management"], "network/frontdoorwebapplicationfirewallpolicies": ["azurerm_cdn_frontdoor_firewall_policy"],
}

def tf_candidates(ctx):
    names = set(re.findall(r"\b(azurerm_[a-z0-9_]+|azapi_[a-z_]+)\b", ctx)) & set(TFS)
    for ns, t in re.findall(r"Microsoft\.([A-Za-z]+)/([A-Za-z]+)", ctx):
        w, nsl = _snake(t).split("_"), ns.lower()
        al = [k for k in ALIAS.get(f"{nsl}/{t.lower()}", []) if k in TFS]
        if al:
            names.update(al)
            continue
        for n in range(len(w), 0, -1):
            c = "_".join(w[:n])
            hit = ([k for k in TFS if k == "azurerm_" + c] or [k for k in TFS if k == f"azurerm_{nsl}_{c}"]
                   or sorted((k for k in TFS if k.startswith("azurerm_") and k.endswith("_" + c) and nsl in k), key=len)[:1])
            if hit:
                names.update(hit)
                break
    return sorted(names)[:4]

def tf_schema_text(names):
    out = []
    for n in names:
        b = TFS[n]["block"]
        at = {a: v for a, v in b.get("attributes", {}).items() if not v.get("deprecated")}
        req = [a for a, v in at.items() if v.get("required")]
        opt = [a for a, v in at.items() if v.get("optional") and a != "id"]
        blocks = []
        for bn, bv in b.get("block_types", {}).items():
            breq = [a for a, v in bv.get("block", {}).get("attributes", {}).items() if v.get("required")]
            blocks.append(bn + (" (required)" if bv.get("min_items", 0) > 0 else "") + (" {" + ", ".join(breq) + "}" if breq else ""))
        out.append(f"- {n}: required arguments: {', '.join(req) or 'none'}; optional arguments: {', '.join(opt[:60])}; nested blocks: {', '.join(blocks[:30]) or 'none'}")
    return "\n".join(out)

def build(n):
    rng = random.Random(1000003 * n + 7)
    mode, lang = SEL[n % len(SEL)], LANGS[(n // len(SEL)) % 5]
    kinds, desc0 = MODES[mode]
    if LOCAL:
        kinds = [x for x in kinds if x not in ("terraform", "bicep")]
    k = rng.choice([x for x in kinds if samples[x]])
    s = rng.choice(samples[k])
    desc, tag = desc0, TAG[k]
    if mode == "convert":
        dst = rng.choice([x for x in kinds if x != k])
        desc, tag = desc0.format(src=NAME[k], dst=NAME[dst]), f"{TAG[k]} and {TAG[dst]}"
    if mode == "migrate":
        dst = "Bicep" if LOCAL else rng.choice(["Bicep", "Terraform (azurerm)"])
        desc, tag = desc0.format(dst=dst), "json and " + ("bicep" if dst == "Bicep" else "hcl")
    tfrule = ""
    if TFS and (k == "terraform" or "Terraform" in desc):
        names = tf_candidates(s["ctx"])
        tfrule = ("- Terraform: target provider hashicorp/azurerm 5.x (and azure/azapi 2.x). Use ONLY resource types, arguments and blocks that exist in this version. "
                  "If a resource is not in azurerm, use azapi_resource with the ARM type and API version.\n")
        if names:
            tfrule += "Authoritative azurerm 5.x schema of the relevant resources:\n" + tf_schema_text(names) + "\n"
    prompt = f"""You create ONE training example for QAZTECH Azure Engineer, an assistant for Azure engineers.
Mode: {mode}. {desc}
Main tool: {NAME[k]}.
Engineer's language: {LANG[lang]}. The engineer's message and all of the assistant's prose must be in {LANG[lang]}; code, commands, property names, log lines and code comments stay in English.
Rules:
- Use only commands, parameters, resource types and API versions that appear in the reference or that you are certain exist. Never invent flags or error codes.
- When code creates resources that support tags, include the tags Environment, CostCenter and Owner (as parameters where the tool supports parameters).
- Bicep: current syntax only. Put decorators such as @description('...') and @allowed([...]) on the line above a param; never use the legacy `param x string { ... }` form. Never break a line directly after `=`: keep the expression on one line or wrap it in parentheses. Use resource types and API versions that appear in the reference, or GA versions you are certain exist.
- Use placeholder names such as rg-demo, vnet-demo, stdemo001; no real subscription IDs, keys or passwords.
- Put code in fenced blocks with the language tag {tag}.
- The assistant's answer must stand alone: never mention "the reference", "the documentation above" or "the example".
{tfrule}Reference (Microsoft Learn, CC BY 4.0):
<<<
{s['ctx']}
>>>
Return JSON only: {{"user": "...", "assistant": "..."}}"""
    return {"id": f"{PFX}-{n:06d}", "kind": k, "mode": mode, "lang": lang, "source": s["path"], "prompt": prompt}

def call(msgs):
    out, thinking, ntok, t0 = [], 0, 0, time.time()
    for ch in CLIENT.chat(TEACHER, messages=msgs, stream=True, options={"temperature": 0.7, "num_ctx": 12288, **({"num_predict": 4096} if LOCAL else {})}, **({"format": SCHEMA, "keep_alive": -1} if LOCAL else {})):
        msg = ch["message"]
        if msg.get("content"):
            out.append(msg["content"])
        if msg.get("thinking"):
            thinking += len(msg["thinking"])
        if ch.get("done"):
            ntok = ch.get("eval_count") or 0
        if time.time() - t0 > LIMIT:
            raise TimeoutError(f"дольше {LIMIT} с")
    return "".join(out), thinking, ntok

done = set()
if OUTF.exists():
    done = {json.loads(l)["id"] for l in open(OUTF, encoding="utf-8") if l.strip()}
todo = [n for n in range(TARGET) if f"{PFX}-{n:06d}" not in done]
stat = {"ok": 0, "bad": 0, "t0": time.time()}
print(f"УЧИТЕЛЬ {TEACHER} | режимы {SEL} | цель {TARGET} | уже есть {len(done)} | осталось {len(todo)} | потоков {WORKERS} | стоп {DEADLINE}", flush=True)

def gen(n):
    if DEADLINE and dt.datetime.now(dt.timezone.utc) > DEADLINE:
        return
    j = build(n)
    base = {x: j[x] for x in ("id", "kind", "mode", "lang", "source")}
    t0 = time.time()
    try:
        for tr in range(4):
            try:
                content, thinking, ntok = call([{"role": "user", "content": j["prompt"]}])
                break
            except Exception as e0:
                if tr == 3 or isinstance(e0, TimeoutError):
                    raise
                time.sleep(60)
        d = json.loads(re.search(r"\{.*\}", content, re.S).group(0), strict=False)
        ok = isinstance(d.get("user"), str) and isinstance(d.get("assistant"), str) and "```" in d["user"] + d["assistant"]
        rec = {**base, "teacher": TEACHER, "ok": ok, "sec": round(time.time() - t0, 1), "thinking_chars": thinking, "tokens": ntok,
               "messages": [{"role": "user", "content": d["user"]}, {"role": "assistant", "content": d["assistant"]}]}
    except Exception as e:
        ok, rec = False, {**base, "teacher": TEACHER, "ok": False, "sec": round(time.time() - t0, 1), "err": repr(e)[:200]}
    with LOCK:
        with open(OUTF, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        stat["ok" if ok else "bad"] += 1
        h = (time.time() - stat["t0"]) / 3600
        print(f"{time.strftime('%H:%M')} {j['id']} {j['mode']:12} {j['lang']} {'OK  ' if ok else 'БРАК'} {rec['sec']:5.0f} с | годных {stat['ok']} брак {stat['bad']} | {stat['ok']/max(h, 1e-6):.1f} годных/час", flush=True)

with ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(gen, todo))
print("ГЕНЕРАЦИЯ ГОТОВА: годных", stat["ok"], "брак", stat["bad"], flush=True)

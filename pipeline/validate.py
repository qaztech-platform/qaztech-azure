#!/usr/bin/env python3
"""QAZTECH Azure Engineer: check code blocks in assistant answers with real tools (az, pwsh+Az, bicep, terraform)."""
import collections, functools, json, os, pathlib, re, subprocess, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
try:
    import yaml
except ImportError:
    yaml = None

HOME = pathlib.Path.home()
D = HOME / "azure-engineer/data"
BICEP, PWSH = str(HOME / ".azure/bin/bicep"), str(HOME / ".local/bin/pwsh")
TFC = HOME / "azure-engineer/tfcache"
ENV = {**os.environ, "AZURE_EXTENSION_USE_DYNAMIC_INSTALL": "no", "AZURE_CORE_COLLECT_TELEMETRY": "no",
       "SuppressAzurePowerShellBreakingChangeWarnings": "true", "DOTNET_CLI_TELEMETRY_OPTOUT": "1"}
TFENV = {**ENV, "TF_PLUGIN_CACHE_DIR": str(TFC / "plugins"), "TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE": "true",
         "TF_IN_AUTOMATION": "1", "CHECKPOINT_DISABLE": "1"}
FENCE = re.compile(r"```([A-Za-z0-9_+-]*)[^\n]*\n(.*?)```", re.S)
TOOL = {"bash": "az", "sh": "az", "shell": "az", "console": "az", "azurecli": "az", "azurecli-interactive": "az", "azure-cli": "az",
        "powershell": "ps", "azurepowershell": "ps", "azurepowershell-interactive": "ps", "pwsh": "ps", "ps1": "ps",
        "bicep": "bicep", "hcl": "tf", "terraform": "tf", "tf": "tf", "json": "json", "yaml": "yaml", "yml": "yaml"}
AZ_GLOBAL = {"--output", "-o", "--query", "--subscription", "--debug", "--verbose", "--only-show-errors", "--help", "-h"}
AUX_REQ = 'terraform {\n  required_providers {\n    azurerm = { source = "hashicorp/azurerm" }\n    azapi = { source = "azure/azapi" }\n  }\n}\n'
AUX_PROV = 'provider "azurerm" {\n  features {}\n}\n'
TF_LOCK = threading.Lock()

@functools.lru_cache(maxsize=None)
def az_help(cmd):
    r = subprocess.run(["az", *cmd.split(), "--help"], capture_output=True, text=True, timeout=120, env=ENV)
    return None if r.returncode else set(re.findall(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*|-[a-zA-Z])\b", r.stdout))

def az_cmds(code):
    code = re.sub(r"\\\n", " ", code)
    out = []
    for m in re.finditer(r"(?:^|[\s;(&|$])az\s+([^\n;|&)]*)", code, re.M):
        toks, words = m.group(1).split(), []
        for t in toks:
            if not re.fullmatch(r"[a-z][a-z0-9-]*", t):
                break
            words.append(t)
        if words:
            out.append((" ".join(words), [t.split("=")[0] for t in toks if re.fullmatch(r"--?[A-Za-z][A-Za-z0-9-]*(=.*)?", t)]))
    return out

def check_az(code):
    cmds = az_cmds(code)
    if not cmds:
        return None
    out = []
    for cmd, flags in cmds:
        h = az_help(cmd)
        if h is None:
            out.append(f"az неизвестная команда | az {cmd}")
            continue
        out += [f"az неизвестный флаг | az {cmd} {f}" for f in flags if f not in h and f not in AZ_GLOBAL]
    return out

def check_bicep(code):
    if re.match(r"\s*using\s", code):
        return None
    if not re.search(r"^\s*(targetScope|param|var|resource|module|output|metadata|type|func|import|extension)\b", code, re.M):
        return None
    with tempfile.TemporaryDirectory() as t:
        f = pathlib.Path(t) / "main.bicep"
        f.write_text(code, encoding="utf-8")
        r = subprocess.run([BICEP, "build", str(f), "--stdout"], capture_output=True, text=True, timeout=180, env=ENV)
    out = []
    for l in r.stderr.splitlines():
        m = re.search(r"\((\d+),\d+\) : (Error|Warning) (BCP\d+): (.*)", l)
        if m and (m.group(2) == "Error" or m.group(3) in ("BCP081", "BCP037", "BCP035")):
            ln = int(m.group(1))
            src = code.splitlines()[ln - 1].strip()[:80] if 0 < ln <= len(code.splitlines()) else ""
            out.append(f"bicep {m.group(3)} | line {ln}: `{src}`: {m.group(4)[:90]}")
    if r.returncode and not out:
        out.append("bicep ошибка сборки | " + r.stderr.strip()[-90:])
    return out

def tf_run(args, cwd):
    return subprocess.run(["terraform", *args], cwd=cwd, capture_output=True, text=True, timeout=600, env=TFENV)

def tf_prepare():
    base = TFC / "base"
    (TFC / "plugins").mkdir(parents=True, exist_ok=True)
    base.mkdir(parents=True, exist_ok=True)
    (base / "main.tf").write_text(AUX_REQ + AUX_PROV, encoding="utf-8")
    r = tf_run(["init", "-backend=false", "-input=false", "-no-color"], base)
    print("TERRAFORM КЭШ ПРОВАЙДЕРОВ:", "OK" if r.returncode == 0 else "ОШИБКА " + r.stderr.strip()[-300:], flush=True)

def check_tf(code):
    cmd_lines = [x.strip() for x in code.splitlines() if x.strip() and not x.strip().startswith("#")]
    if cmd_lines and all(re.match(r"terraform\s+(init|plan|apply|validate|fmt|show|destroy|output|import|state|workspace|providers|version|login|graph|refresh|taint|untaint|console|get|test)\b", x) for x in cmd_lines):
        return None
    if not re.search(r"^\s*(resource|data|module|variable|output|provider|terraform|locals)\b", code, re.M):
        return None
    with tempfile.TemporaryDirectory() as t:
        (pathlib.Path(t) / "main.tf").write_text(code, encoding="utf-8")
        aux = ("" if "required_providers" in code else AUX_REQ) + ("" if re.search(r'provider\s+"azurerm"', code) else AUX_PROV)
        if aux:
            (pathlib.Path(t) / "zz_aux.tf").write_text(aux, encoding="utf-8")
        with TF_LOCK:
            r = tf_run(["init", "-backend=false", "-input=false", "-no-color"], t)
            if r.returncode:
                time.sleep(5)
                r = tf_run(["init", "-backend=false", "-input=false", "-no-color"], t)
        if r.returncode:
            return ["tf init | " + next((l.strip() for l in r.stderr.splitlines() if "Error" in l), r.stderr.strip()[-90:])[:90]]
        r = tf_run(["validate", "-json", "-no-color"], t)
    try:
        v = json.loads(r.stdout)
    except Exception:
        return ["tf validate | нет JSON"]
    return [f"tf {d.get('summary', '')[:60]} | {(d.get('detail') or '')[:80]}" for d in v.get("diagnostics", []) if d.get("severity") == "error" and not d.get("summary", "").startswith("Reference to undeclared")]

def check_json(code):
    s = code.strip()
    if not s.startswith(("{", "[")):
        return None
    for cand in (s, re.sub(r"(?m)^\s*//.*$", "", s)):
        try:
            json.loads(cand)
            return []
        except Exception as e:
            err = str(e)
    return [f"json не разбирается | {err[:60]}"]

def check_yaml(code):
    if yaml is None:
        return None
    try:
        list(yaml.safe_load_all(code))
        return []
    except Exception as e:
        return [f"yaml не разбирается | {str(e).splitlines()[0][:60]}"]

CHECK = {"az": check_az, "bicep": check_bicep, "tf": check_tf, "json": check_json, "yaml": check_yaml}
PS1 = r"""
$ProgressPreference = 'SilentlyContinue'; $WarningPreference = 'SilentlyContinue'
$items = Get-Content $args[0] -Raw | ConvertFrom-Json
$cache = @{}
$res = foreach ($it in $items) {
  $tokens = $null; $errs = $null; $problems = @()
  $ast = [System.Management.Automation.Language.Parser]::ParseInput($it.code, [ref]$tokens, [ref]$errs)
  foreach ($e in $errs) { $problems += "ps синтаксис | " + $e.Message }
  $cmds = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)
  foreach ($c in $cmds) {
    $name = $c.GetCommandName()
    if (-not $name -or $name -notmatch '^[A-Za-z]+-Az[A-Za-z0-9]+$') { continue }
    if (-not $cache.ContainsKey($name)) { $cache[$name] = Get-Command -Name $name -ErrorAction SilentlyContinue }
    $gc = $cache[$name]
    if (-not $gc) { $problems += "ps нет командлета | $name"; continue }
    $known = @{}
    foreach ($p in $gc.Parameters.Values) { $known[$p.Name.ToLower()] = 1; foreach ($a in $p.Aliases) { $known[$a.ToLower()] = 1 } }
    foreach ($el in $c.CommandElements) {
      if ($el -is [System.Management.Automation.Language.CommandParameterAst]) {
        $pn = $el.ParameterName.ToLower()
        if (-not $known.ContainsKey($pn) -and @($known.Keys | Where-Object { $_.StartsWith($pn) }).Count -eq 0) {
          $problems += "ps нет параметра | -$($el.ParameterName) у $name"
        }
      }
    }
  }
  [pscustomobject]@{ i = $it.i; problems = $problems }
}
ConvertTo-Json -InputObject @($res) -Depth 4 -Compress
"""

recs = []
for name in ("pilot.jsonl", "pilot_retry.jsonl", "qwen_main.jsonl", "minimax_hard.jsonl"):
    if not (D / name).exists():
        continue
    for l in open(D / name, encoding="utf-8"):
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get("ok"):
            r.setdefault("teacher", "minimax-m3.1-flash:cloud")
            recs.append(r)
who = lambda r: "qwen" if "Qwen" in r["teacher"] else "minimax"
res = collections.defaultdict(list)
jobs, ps = [], []
for ri, r in enumerate(recs):
    a = r["messages"][1]["content"]
    if a.count("```") % 2:
        res[ri].append("незакрытый блок кода | ")
    for m in FENCE.finditer(a):
        tool = TOOL.get(m.group(1).lower())
        if tool == "ps":
            ps.append((ri, m.group(2)))
        elif tool:
            jobs.append((ri, tool, m.group(2)))
print(f"ПРИМЕРОВ: {len(recs)} | блоков кода: {len(jobs) + len(ps)} (PowerShell {len(ps)})", flush=True)
if any(j[1] == "tf" for j in jobs):
    tf_prepare()
checked, failed = collections.Counter(), collections.Counter()

def run(j):
    try:
        return j, CHECK[j[1]](j[2])
    except Exception as e:
        return j, [f"{j[1]} сбой проверки | {repr(e)[:80]}"]

with ThreadPoolExecutor(6) as ex:
    for (ri, tool, _), out in ex.map(run, jobs):
        if out is None:
            continue
        checked[(who(recs[ri]), tool)] += 1
        if out:
            failed[(who(recs[ri]), tool)] += 1
            res[ri] += out
print("az, bicep, terraform, json, yaml: готово", flush=True)
if ps:
    with tempfile.TemporaryDirectory() as t:
        items, script = pathlib.Path(t) / "items.json", pathlib.Path(t) / "check.ps1"
        items.write_text(json.dumps([{"i": k, "code": c} for k, (_, c) in enumerate(ps)]), encoding="utf-8")
        script.write_text(PS1, encoding="utf-8")
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), str(items)], capture_output=True, text=True, timeout=7200, env=ENV)
    line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith("[")), "[]")
    for x in json.loads(line):
        ri = ps[x["i"]][0]
        checked[(who(recs[ri]), "ps")] += 1
        if x["problems"]:
            failed[(who(recs[ri]), "ps")] += 1
            res[ri] += x["problems"]
    print("PowerShell: готово", "" if line != "[]" else "| НЕТ РЕЗУЛЬТАТА: " + r.stderr.strip()[-300:], flush=True)

with open(D / "validation.jsonl", "w", encoding="utf-8") as f:
    for ri, r in enumerate(recs):
        f.write(json.dumps({"id": r["id"], "teacher": who(r), "mode": r["mode"], "pass": not res[ri], "problems": res[ri]}, ensure_ascii=False) + "\n")
print("\n=== БЛОКИ КОДА ===\nучитель  инструмент  проверено  с ошибками")
for key in sorted(checked):
    print(f"{key[0]:8} {key[1]:10} {checked[key]:9}  {failed[key]:5} ({100 * failed[key] / checked[key]:.0f}%)")
print("\n=== ПРИМЕРЫ ЦЕЛИКОМ БЕЗ ОШИБОК ===")
for w in ("qwen", "minimax"):
    rr = [ri for ri, r in enumerate(recs) if who(r) == w]
    ok = sum(1 for ri in rr if not res[ri])
    if rr:
        print(f"{w:8} {ok}/{len(rr)} ({100 * ok / len(rr):.0f}%)")
print("\n=== ЧАСТЫЕ ПРОБЛЕМЫ ===")
cnt, ex1 = collections.Counter(), {}
for ri, ps_ in res.items():
    for p in ps_:
        k = f"{who(recs[ri])}: {p.split(' | ')[0]}"
        cnt[k] += 1
        ex1.setdefault(k, (recs[ri]["id"], p.split(" | ", 1)[-1]))
for k, v in cnt.most_common(15):
    print(f"{v:4} × {k:45} напр. {ex1[k][0]}: {ex1[k][1][:70]}")

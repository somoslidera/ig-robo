"""Lê o texto da página de notificações do Instagram (stdin ou arquivo) e alimenta o Lidera Social:
quem "começou a seguir você" vira seguidor registrado; os novos viram lead NS e vão para a triagem/ativação pela IA.
Uso: python3 notif.py <conta_id> [arquivo.txt]   (sem arquivo, lê do stdin)
Segredos vêm do .env ao lado (SUPABASE_URL, SUPABASE_SERVICE_KEY, IG_CRON_SECRET)."""
import json, pathlib, re, sys, time, datetime, urllib.request, urllib.parse

env = {}
for ln in pathlib.Path(__file__).with_name(".env").read_text().splitlines():
    if "=" in ln and not ln.startswith("#"): k, v = ln.split("=", 1); env[k.strip()] = v.strip()
URL, KEY, CRON = env["SUPABASE_URL"], env["SUPABASE_SERVICE_KEY"], env["IG_CRON_SECRET"]
conta_id = sys.argv[1]
txt = pathlib.Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else sys.stdin.read()

def req(method, path, body=None, headers=None):
    h = {"apikey": KEY, "Authorization": "Bearer " + KEY, "Content-Type": "application/json", "Prefer": "return=representation"}
    h.update(headers or {})
    r = urllib.request.Request(URL + path, data=json.dumps(body).encode() if body is not None else None, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp: return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: {e.code} {e.read().decode()[:300]}")

# "nome\ncomeçou a seguir você" (linhas) ou "nome começou a seguir você" (inline)
linhas = [l.strip() for l in txt.splitlines()]
seg = []
for i, l in enumerate(linhas):
    m = re.match(r"^@?([a-z0-9._]{2,30})\s+começou a seguir você", l, re.I)
    if m: seg.append(m.group(1).lower()); continue
    if l.lower().startswith("começou a seguir você") and i > 0:
        u = linhas[i - 1].strip().lstrip("@")
        if re.fullmatch(r"[a-z0-9._]{2,30}", u, re.I): seg.append(u.lower())
seg = list(dict.fromkeys(seg))
hoje = datetime.date.today().isoformat()
if not seg: print("Nenhum 'começou a seguir você' no texto."); sys.exit(0)
ex = req("GET", f"/rest/v1/ig_seguidores?conta_id=eq.{conta_id}&username=in.({','.join(seg)})&select=username")
exset = {x["username"] for x in ex}
novos = [u for u in seg if u not in exset]
req("POST", "/rest/v1/ig_seguidores?on_conflict=conta_id,username",
    [{"conta_id": conta_id, "username": u, "ultimo_em": hoje, **({} if u in exset else {"primeiro_em": hoje})} for u in seg],
    {"Prefer": "resolution=merge-duplicates,return=representation"})
# já existe lead?
leads = req("GET", f"/rest/v1/ig_leads?conta_id=eq.{conta_id}&username=in.({','.join(novos) or 'x'})&select=username") if novos else []
ja = {l["username"] for l in leads}
feitos = []
for u in novos:
    if u in ja: continue
    r = urllib.request.Request(URL + "/functions/v1/ig-api/lead/pesquisar", data=json.dumps({"conta_id": conta_id, "username": u, "fluxo": "NS", "sinal": "novo seguidor (notificações " + hoje + ")"}).encode(), headers={"x-cron": CRON, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=120) as resp: j = json.loads(resp.read().decode())
        ia = j.get("ia") if isinstance(j, dict) else {}; icp = (ia if isinstance(ia, dict) else {}).get("icp", "?"); feitos.append(f"@{u} ({icp})")
        if isinstance(j, dict) and j.get("lead_id"): req("PATCH", f"/rest/v1/ig_seguidores?conta_id=eq.{conta_id}&username=eq.{u}", {"lead_id": j["lead_id"]})
    except Exception as e: feitos.append(f"@{u} (erro: {str(e)[:60]})")
    time.sleep(2)
print(f"Seguidores no texto: {len(seg)} · novos: {len(novos)} · leads criados/pesquisados: {len(feitos)}")
for f in feitos: print(" -", f)

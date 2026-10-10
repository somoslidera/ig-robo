"""ig-robo: motor de ações do Lidera Social (instagrapi).

Ciclo (a cada ROBO_INTERVAL_MIN, só em horário comercial):
  1. garante sessão de cada conta
  2. novos seguidores: lê os ~60 mais recentes, compara com ig_seguidores, cria lead NS e pede
     pesquisa/triagem + mensagem para a ig-api (/lead/pesquisar), que já enfileira seguir + ativar
  3. executa ações pendentes da fila (ig_acoes) respeitando a rampa diária
  4. grava estado em ig_contas.config.robo_estado e tudo em ig_log

Bloqueio do Instagram (FeedbackRequired / Challenge / LoginRequired) → pausa a conta por 24 h e avisa o n8n.
"""
import json, os, random, threading, time, traceback
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, Header, HTTPException, Request
import uvicorn
from instagrapi import Client
from instagrapi.exceptions import (ChallengeRequired, FeedbackRequired, LoginRequired, PleaseWaitFewMinutes,
                                   RateLimitError, TwoFactorRequired, UserNotFound, ClientError)

TZ = ZoneInfo("America/Sao_Paulo")
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
CRON_SECRET = os.environ.get("IG_CRON_SECRET", "")
ROBO_SECRET = os.environ.get("ROBO_SECRET", "")
N8N_ALERT = os.environ.get("N8N_ALERT_URL", "")
INTERVAL = int(os.environ.get("ROBO_INTERVAL_MIN", "15"))
HORA_INI = int(os.environ.get("ROBO_HORA_INI", "8"))
HORA_FIM = int(os.environ.get("ROBO_HORA_FIM", "20"))
ACCOUNTS = json.loads(os.environ.get("IG_ACCOUNTS", "[]"))
PROXY = os.environ.get("IG_PROXY", "")  # ex.: http://user:pass@host:port (residencial/móvel)
DATA = Path(os.environ.get("ROBO_DATA", "/data"))
(DATA / "sessions").mkdir(parents=True, exist_ok=True)

# limites diários (teto duro; a rampa de mensagens vem de ig_contas.config / playbook)
LIM_SEGUIR = int(os.environ.get("LIM_SEGUIR", "30"))
LIM_CURTIR = int(os.environ.get("LIM_CURTIR", "60"))
LIM_STORY = int(os.environ.get("LIM_STORY", "40"))
LIM_MSG_MAX = int(os.environ.get("LIM_MSG_MAX", "80"))

H = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
clients: dict[str, Client] = {}
pending_2fa: dict[str, dict] = {}
estado: dict[str, dict] = {}
lock = threading.Lock()


# ---------------- supabase (PostgREST) ----------------
def sb_get(table, params):
    r = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=H, params=params, timeout=30)
    r.raise_for_status(); return r.json()

def sb_post(table, rows, upsert_on=None):
    h = dict(H)
    if upsert_on: h["Prefer"] = "resolution=merge-duplicates,return=representation"
    r = requests.post(f"{SUPABASE_URL}/rest/v1/{table}" + (f"?on_conflict={upsert_on}" if upsert_on else ""), headers=h, json=rows, timeout=30)
    r.raise_for_status(); return r.json()

def sb_patch(table, params, data):
    r = requests.patch(f"{SUPABASE_URL}/rest/v1/{table}", headers=H, params=params, json=data, timeout=30)
    r.raise_for_status(); return r.json()

def log(tipo, conta_id, detalhe):
    try: sb_post("ig_log", [{"tipo": tipo, "conta_id": conta_id, "detalhe": detalhe}])
    except Exception as e: print("log falhou", e)
    print(datetime.now(TZ).strftime("%H:%M:%S"), tipo, conta_id, json.dumps(detalhe, ensure_ascii=False)[:300])

def ig_api(rota, body):
    r = requests.post(f"{SUPABASE_URL}/functions/v1/ig-api{rota}", headers={"x-cron": CRON_SECRET, "Content-Type": "application/json"}, json=body, timeout=120)
    try: j = r.json()
    except Exception: j = {"erro": r.text[:200]}
    if r.status_code >= 400 or j.get("erro"): raise RuntimeError(f"ig-api {rota}: {j.get('erro') or r.status_code}")
    return j

def hoje(): return datetime.now(TZ).date().isoformat()
def alerta(msg, conta_id=None):
    log("alerta", conta_id, {"msg": msg})
    if N8N_ALERT:
        try: requests.post(N8N_ALERT, json={"origem": "ig-robo", "conta_id": conta_id, "msg": msg}, timeout=15)
        except Exception as e: print("alerta n8n falhou", e)


# ---------------- sessão ----------------
def conta_cfg(conta_id):
    rows = sb_get("ig_contas", {"id": f"eq.{conta_id}", "select": "id,username,config,conectado_em,ativo"})
    return rows[0] if rows else None

def salvar_estado(conta_id, **kv):
    c = conta_cfg(conta_id)
    if not c: return
    cfg = c.get("config") or {}
    est = cfg.get("robo_estado") or {}
    est.update(kv); est["atualizado_em"] = datetime.now(TZ).isoformat()
    cfg["robo_estado"] = est
    sb_patch("ig_contas", {"id": f"eq.{conta_id}"}, {"config": cfg})

def pausar(conta_id, horas, motivo):
    c = conta_cfg(conta_id); cfg = (c or {}).get("config") or {}
    cfg["robo_pausado_ate"] = (datetime.now(TZ) + timedelta(hours=horas)).isoformat()
    cfg["robo_motivo_pausa"] = motivo
    sb_patch("ig_contas", {"id": f"eq.{conta_id}"}, {"config": cfg})
    alerta(f"Robô pausado {horas} h em @{(c or {}).get('username')}: {motivo}", conta_id)

def cliente(acc) -> Client | None:
    u = acc["username"]
    if u in clients: return clients[u]
    cl = Client(); cl.delay_range = [2, 6]; cl.set_user_agent(os.environ.get('IG_USER_AGENT') or 'Instagram 382.0.0.44.76 Android (34/14; 480dpi; 1080x2340; samsung; SM-S918B; dm3q; qcom; pt_BR; 735618641)')
    if PROXY: cl.set_proxy(PROXY)
    path = DATA / "sessions" / f"{u}.json"
    try:
        if path.exists():
            cl.load_settings(path)
            try:
                cl.account_info()  # sessão importada/salva ainda vale? então nem tenta login
            except Exception:
                if not acc.get("password"): raise LoginRequired("sessão expirou; gere outra com login_local.py")
                cl.login(u, acc["password"])
                cl.account_info()
        else:
            if not acc.get("password"): raise LoginRequired("sem sessão; gere com login_local.py")
            cl.login(u, acc["password"])
        cl.dump_settings(path); clients[u] = cl
        log("robo", acc["conta_id"], {"login": "ok", "username": u})
        return cl
    except TwoFactorRequired:
        pending_2fa[u] = {"cl": cl, "acc": acc}
        alerta(f"@{u}: Instagram pediu código 2FA. Envie com POST /2fa {{username, code}}.", acc["conta_id"])
        return None
    except (ChallengeRequired, LoginRequired) as e:
        pausar(acc["conta_id"], 24, f"login: {type(e).__name__}")
        if path.exists(): path.unlink()
        return None
    except Exception as e:
        log("erro", acc["conta_id"], {"onde": "login", "erro": str(e)[:300]})
        return None


# ---------------- novos seguidores ----------------
def novos_seguidores(acc, cl: Client):
    cid = acc["conta_id"]
    try:
        users, _ = cl.user_followers_v1_chunk(cl.user_id, max_amount=60)
    except Exception as e:
        raise
    nomes = [u.username.lower() for u in users]
    if not nomes: return 0
    ex = sb_get("ig_seguidores", {"conta_id": f"eq.{cid}", "username": f"in.({','.join(nomes)})", "select": "username"})
    exset = {x["username"] for x in ex}
    base = sb_get("ig_seguidores", {"conta_id": f"eq.{cid}", "select": "username", "limit": "1"})
    novos = [u for u in users if u.username.lower() not in exset]
    d = hoje()
    sb_post("ig_seguidores", [{"conta_id": cid, "username": u.username.lower(), "nome": u.full_name, "ultimo_em": d, **({} if u.username.lower() in exset else {"primeiro_em": d})} for u in users], upsert_on="conta_id,username")
    if not base:
        log("robo", cid, {"seguidores": "base inicial registrada", "n": len(users)})
        return 0
    criados = 0
    for u in novos[:25]:
        try:
            r = ig_api("/lead/pesquisar", {"conta_id": cid, "username": u.username, "fluxo": "NS", "sinal": "novo seguidor (detectado pelo robô)"})
            if r.get("lead_id"): sb_patch("ig_seguidores", {"conta_id": f"eq.{cid}", "username": f"eq.{u.username.lower()}"}, {"lead_id": r["lead_id"]})
            criados += 1
        except Exception as e:
            log("erro", cid, {"onde": "pesquisar novo seguidor", "u": u.username, "erro": str(e)[:200]})
        time.sleep(random.uniform(2, 5))
    log("robo", cid, {"novos_seguidores": len(novos), "pesquisados": criados})
    return len(novos)


# ---------------- ações ----------------
def feitas_hoje(cid, tipos):
    rows = sb_get("ig_acoes", {"conta_id": f"eq.{cid}", "data": f"eq.{hoje()}", "status": "eq.feita", "feita_por": "eq.robo", "tipo": f"in.({','.join(tipos)})", "select": "id"})
    return len(rows)

def limite_msgs(c):
    cfg = c.get("config") or {}
    inicio = cfg.get("inicio") or (c.get("conectado_em") or "")[:10]
    try: dias = (date.today() - date.fromisoformat(inicio)).days
    except Exception: dias = 0
    base = 10 if dias < 7 else 15 if dias < 14 else 25 if dias < 60 else 40
    return min(base, int(cfg.get("max_dia") or LIM_MSG_MAX))

def tem_placeholder(t): return "[" in (t or "") and "]" in (t or "")

def executar_acoes(acc, cl: Client, c):
    cid = acc["conta_id"]
    lim_msg = limite_msgs(c)
    msgs = feitas_hoje(cid, ["ativar", "followup"]); seg = feitas_hoje(cid, ["seguir"]); sto = feitas_hoje(cid, ["reagir_story", "visita"])
    curtidas = 0
    acoes = sb_get("ig_acoes", {"conta_id": f"eq.{cid}", "status": "eq.pendente", "data": f"lte.{hoje()}", "tipo": "in.(seguir,ativar,followup,reagir_story,visita)", "select": "*,ig_leads(id,username,icp,etapa,toques,ig_user_id)", "order": "bloco.asc,criado_em.asc", "limit": "40"})
    feitas = 0
    for a in acoes:
        l = a.get("ig_leads") or {}
        if not l.get("username"): continue
        if l.get("icp") == "nao_passa" or l.get("etapa") == "fora_icp":
            sb_patch("ig_acoes", {"id": f"eq.{a['id']}"}, {"status": "pulada", "feita_por": "robo", "feita_em": datetime.now(TZ).isoformat()}); continue
        tipo = a["tipo"]
        if tipo in ("ativar", "followup") and msgs >= lim_msg: continue
        if tipo == "seguir" and seg >= LIM_SEGUIR: continue
        if tipo in ("reagir_story", "visita") and sto >= LIM_STORY: continue
        if tipo in ("ativar", "followup") and (not a.get("texto") or tem_placeholder(a["texto"])):
            continue  # texto incompleto fica para a pessoa
        if tipo == "ativar" and l.get("icp") != "passa":
            continue  # só manda mensagem para quem passou na triagem
        try:
            uid = l.get("ig_user_id")
            if not uid or not str(uid).isdigit():
                uid = str(cl.user_id_from_username(l["username"]))
                sb_patch("ig_leads", {"id": f"eq.{l['id']}"}, {"ig_user_id": uid})
            det = {}
            if tipo == "seguir":
                cl.user_follow(uid); seg += 1; det["seguiu"] = True
                time.sleep(random.uniform(8, 20))
                try:
                    medias = cl.user_medias_v1(uid, amount=2)
                    for m in medias:
                        if curtidas >= LIM_CURTIR: break
                        cl.media_like(m.id); curtidas += 1; time.sleep(random.uniform(6, 15))
                    det["curtiu"] = len(medias)
                except Exception as e: det["curtir_erro"] = str(e)[:120]
                try:
                    stories = cl.user_stories(uid)
                    if stories: cl.story_like(stories[0].pk); det["story"] = True; sto += 1
                except Exception as e: det["story_erro"] = str(e)[:120]
                sb_patch("ig_leads", {"id": f"eq.{l['id']}"}, {"aquecimento_dia": 1, "aquecido_em": hoje(), "ultimo_toque": datetime.now(TZ).isoformat()})
            elif tipo in ("reagir_story", "visita"):
                stories = cl.user_stories(uid)
                if stories: cl.story_like(stories[0].pk); det["story"] = True
                else:
                    medias = cl.user_medias_v1(uid, amount=1)
                    if medias and curtidas < LIM_CURTIR: cl.media_like(medias[0].id); curtidas += 1; det["curtiu"] = 1
                sto += 1
            elif tipo in ("ativar", "followup"):
                cl.direct_send(a["texto"], user_ids=[int(uid)]); msgs += 1; det["mensagem"] = a["texto"][:80]
                toques = int(l.get("toques") or 0) + 1
                upd = {"ultimo_toque": datetime.now(TZ).isoformat(), "toques": toques}
                if tipo == "ativar": upd.update({"etapa": "ativado", "proximo_toque": (date.today() + timedelta(days=2)).isoformat()})
                else:
                    upd["proximo_toque"] = None if toques >= 4 else (date.today() + timedelta(days=3 if toques == 2 else 5)).isoformat()
                    if toques >= 4: upd["etapa"] = "frio"
                sb_patch("ig_leads", {"id": f"eq.{l['id']}"}, upd)
            sb_patch("ig_acoes", {"id": f"eq.{a['id']}"}, {"status": "feita", "feita_por": "robo", "feita_em": datetime.now(TZ).isoformat()})
            log("acao", cid, {"robo": tipo, "lead": l["username"], **det})
            feitas += 1
            time.sleep(random.uniform(120, 360))  # 2 a 6 minutos entre ações
        except (FeedbackRequired, ChallengeRequired, LoginRequired) as e:
            pausar(cid, 24, f"{type(e).__name__}: {str(e)[:120]}")
            clients.pop(acc["username"], None)
            return feitas
        except (PleaseWaitFewMinutes, RateLimitError) as e:
            log("erro", cid, {"onde": tipo, "erro": "rate limit, esperando 20 min"}); time.sleep(1200); return feitas
        except UserNotFound:
            sb_patch("ig_acoes", {"id": f"eq.{a['id']}"}, {"status": "pulada", "feita_por": "robo"})
        except Exception as e:
            log("erro", cid, {"onde": tipo, "lead": l.get("username"), "erro": str(e)[:250]})
            time.sleep(random.uniform(30, 90))
    return feitas


# ---------------- ciclo ----------------
def dentro_do_horario():
    h = datetime.now(TZ); return HORA_INI <= h.hour < HORA_FIM and h.weekday() < 6

def ciclo(forcar=False):
    if not lock.acquire(blocking=False): return {"ocupado": True}
    try:
        out = {}
        for acc in ACCOUNTS:
            cid = acc["conta_id"]
            c = conta_cfg(cid)
            if not c or not c.get("ativo"): out[cid] = "conta inativa"; continue
            cfg = c.get("config") or {}
            if cfg.get("robo") is False: out[cid] = "robô desligado no app"; salvar_estado(cid, status="desligado"); continue
            pa = cfg.get("robo_pausado_ate")
            if pa and datetime.fromisoformat(pa) > datetime.now(TZ): out[cid] = "pausado até " + pa; salvar_estado(cid, status="pausado", motivo=cfg.get("robo_motivo_pausa")); continue
            if not forcar and not dentro_do_horario(): out[cid] = "fora do horário"; salvar_estado(cid, status="aguardando horário"); continue
            cl = cliente(acc)
            if not cl: out[cid] = "sem sessão"; salvar_estado(cid, status="sem sessão (2FA ou bloqueio)"); continue
            try:
                n = novos_seguidores(acc, cl)
                f = executar_acoes(acc, cl, c)
                out[cid] = {"novos": n, "acoes": f}
                salvar_estado(cid, status="ativo", ultimo_ciclo=datetime.now(TZ).isoformat(), novos=n, acoes=f, limite_msgs=limite_msgs(c))
            except (FeedbackRequired, ChallengeRequired, LoginRequired) as e:
                pausar(cid, 24, f"{type(e).__name__}"); clients.pop(acc["username"], None); out[cid] = "bloqueio"
            except Exception as e:
                log("erro", cid, {"onde": "ciclo", "erro": str(e)[:300], "tb": traceback.format_exc()[-600:]}); out[cid] = "erro"
        return out
    finally:
        lock.release()

def loop():
    time.sleep(10)
    while True:
        try: print("ciclo", ciclo())
        except Exception as e: print("ciclo falhou", e)
        time.sleep(INTERVAL * 60)


# ---------------- http ----------------
app = FastAPI()
def auth(x_robo):
    if ROBO_SECRET and x_robo != ROBO_SECRET: raise HTTPException(401, "não autorizado")

@app.get("/")
def root():
    return {"ok": True, "contas": [a["username"] for a in ACCOUNTS], "sessoes": list(clients.keys()), "aguardando_2fa": list(pending_2fa.keys()), "horario": dentro_do_horario(), "intervalo_min": INTERVAL}

@app.post("/rodar")
def rodar(x_robo: str = Header(default="")):
    auth(x_robo); return ciclo(forcar=True)

@app.post("/2fa")
async def dois_fa(req: Request, x_robo: str = Header(default="")):
    auth(x_robo); b = await req.json(); u = b.get("username"); code = str(b.get("code", "")).strip()
    p = pending_2fa.get(u)
    if not p: raise HTTPException(404, "nenhum login aguardando 2FA para esse usuário")
    cl, acc = p["cl"], p["acc"]
    cl.login(u, acc["password"], verification_code=code)
    cl.dump_settings(DATA / "sessions" / f"{u}.json"); clients[u] = cl; pending_2fa.pop(u, None)
    log("robo", acc["conta_id"], {"login": "ok com 2FA"})
    return {"ok": True}

@app.post("/sessao")
async def sessao(req: Request, x_robo: str = Header(default="")):
    """Recebe o JSON de settings do instagrapi gerado em outro lugar (ex.: no Mac, IP residencial)."""
    auth(x_robo); b = await req.json(); u = b.get("username"); settings = b.get("settings")
    if not u or not settings: raise HTTPException(400, "username e settings")
    (DATA / "sessions" / f"{u}.json").write_text(json.dumps(settings))
    clients.pop(u, None)
    acc = next((a for a in ACCOUNTS if a["username"] == u), None)
    if acc: log("robo", acc["conta_id"], {"sessao": "importada"})
    return {"ok": True}

@app.post("/pausar")
async def pausar_http(req: Request, x_robo: str = Header(default="")):
    auth(x_robo); b = await req.json(); pausar(b["conta_id"], int(b.get("horas", 24)), b.get("motivo", "manual")); return {"ok": True}

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

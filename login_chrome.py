"""Abre um navegador no seu Mac; você faz o login no Instagram nele (uma vez). O script captura a sessão e envia ao robô."""
import getpass, json, pathlib, time, warnings
warnings.filterwarnings("ignore")
import requests
from playwright.sync_api import sync_playwright
from instagrapi import Client

ROBO_URL = "https://ig-robo-ig-robo.8rp28i.easypanel.host"
ROBO_SECRET = ""
env = pathlib.Path(__file__).with_name(".env")
if env.exists():
    for ln in env.read_text().splitlines():
        if ln.startswith("ROBO_SECRET="): ROBO_SECRET = ln.split("=", 1)[1].strip()
perfil = pathlib.Path.home() / ".ig-robo-chrome"
print("Abrindo o navegador. Faça o login no Instagram na janela que vai abrir (conta @lidera.food).")
with sync_playwright() as p:
    import os
    ctx = p.chromium.launch_persistent_context(str(perfil), headless=os.environ.get("HEADLESS")=="1", viewport={"width": 1100, "height": 820}, locale="pt-BR",
        args=["--disable-blink-features=AutomationControlled"])
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://www.instagram.com/")
    sid = None
    for _ in range(600):  # até 10 min
        cks = {c["name"]: c["value"] for c in ctx.cookies("https://www.instagram.com")}
        if cks.get("sessionid") and cks.get("ds_user_id"):
            sid = cks["sessionid"]; break
        time.sleep(1)
    if not sid: raise SystemExit("Não detectei o login em 10 minutos. Rode de novo.")
    print("Login detectado. Capturando a sessão…")
    ctx.close()
cl = Client(); cl.delay_range = [1, 3]
cl.login_by_sessionid(sid)
info = cl.account_info()
print("Sessão ok como @%s" % info.username)
try:
    seg, _ = cl.user_followers_v1_chunk(cl.user_id, max_amount=5)
    print("Leitura de seguidores ok:", ", ".join("@" + u.username for u in seg))
except Exception as e:
    print("Aviso: leitura de seguidores falhou:", str(e)[:150])
r = requests.post(f"{ROBO_URL}/sessao", headers={"x-robo": ROBO_SECRET}, json={"username": info.username, "settings": cl.get_settings()}, timeout=30)
print("Envio para o robô:", r.status_code, r.text[:200])

"""Cria a sessão do robô a partir do cookie `sessionid` do Instagram (Chrome logado na conta) e envia para o VPS.
Como pegar o sessionid: Chrome logado no Instagram com a conta certa -> ⌥⌘I (DevTools) -> aba Application ->
Storage > Cookies > https://www.instagram.com -> linha `sessionid` -> copiar o Value.
"""
import getpass, json, os, pathlib, warnings
warnings.filterwarnings("ignore")
import requests
from instagrapi import Client

ROBO_URL = "https://ig-robo-ig-robo.8rp28i.easypanel.host"
ROBO_SECRET = ""
env = pathlib.Path(__file__).with_name(".env")
if env.exists():
    for ln in env.read_text().splitlines():
        if ln.startswith("ROBO_SECRET="): ROBO_SECRET = ln.split("=", 1)[1].strip()
if not ROBO_SECRET: ROBO_SECRET = getpass.getpass("ROBO_SECRET: ").strip()
print("Robô:", ROBO_URL)
sid = getpass.getpass("Cole o sessionid (não aparece ao colar) e aperte Enter: ").strip().strip('"')
if not sid: raise SystemExit("sessionid vazio")
cl = Client(); cl.delay_range = [1, 3]
cl.login_by_sessionid(sid)
info = cl.account_info()
print("Sessão ok como @%s (%s)" % (info.username, cl.user_id))
try:
    seg, _ = cl.user_followers_v1_chunk(cl.user_id, max_amount=5)
    print("Leitura de seguidores ok:", ", ".join("@" + u.username for u in seg))
except Exception as e:
    print("Aviso: leitura de seguidores falhou:", str(e)[:150])
r = requests.post(f"{ROBO_URL}/sessao", headers={"x-robo": ROBO_SECRET}, json={"username": info.username, "settings": cl.get_settings()}, timeout=30)
print("Envio para o robô:", r.status_code, r.text[:200])

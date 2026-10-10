"""Login do Instagram feito no SEU computador (IP residencial) e envio da sessão para o robô no VPS.
Uso (uma vez por conta):
    pip3 install instagrapi requests
    python3 login_local.py
Pede usuário, senha e, se o Instagram exigir, o código 2FA. Nada fica salvo aqui além do que você digitar.
"""
import getpass, json, os, requests, pathlib
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

ROBO_URL = "https://ig-robo-ig-robo.8rp28i.easypanel.host"
ROBO_SECRET = ""
env = pathlib.Path(__file__).with_name(".env")
if env.exists():
    for ln in env.read_text().splitlines():
        if ln.startswith("ROBO_SECRET="): ROBO_SECRET = ln.split("=", 1)[1].strip()
if not ROBO_SECRET: ROBO_SECRET = getpass.getpass("ROBO_SECRET: ").strip()
print("Robô:", ROBO_URL)
u = (input("Usuário do Instagram [lidera.food]: ").strip().lstrip("@") or "lidera.food")
p = getpass.getpass("Senha do Instagram: ")
cl = Client(); cl.delay_range = [1, 3]; cl.set_user_agent('Instagram 382.0.0.44.76 Android (34/14; 480dpi; 1080x2340; samsung; SM-S918B; dm3q; qcom; pt_BR; 735618641)')
try:
    cl.login(u, p)
except TwoFactorRequired:
    code = input("Código 2FA (app autenticador ou SMS): ").strip()
    cl.login(u, p, verification_code=code)
cl.get_timeline_feed()
print("Login ok como", cl.username)
r = requests.post(f"{ROBO_URL}/sessao", headers={"x-robo": ROBO_SECRET}, json={"username": u, "settings": cl.get_settings()}, timeout=30)
print("Envio para o robô:", r.status_code, r.text[:200])

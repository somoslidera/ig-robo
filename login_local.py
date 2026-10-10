"""Login do Instagram feito no SEU computador (IP residencial) e envio da sessão para o robô no VPS.
Uso (uma vez por conta):
    pip3 install instagrapi requests
    python3 login_local.py
Pede usuário, senha e, se o Instagram exigir, o código 2FA. Nada fica salvo aqui além do que você digitar.
"""
import getpass, json, requests
from instagrapi import Client
from instagrapi.exceptions import TwoFactorRequired

ROBO_URL = input("URL do robô [https://ig-robo-ig-robo.8rp28i.easypanel.host]: ").strip() or "https://ig-robo-ig-robo.8rp28i.easypanel.host"
ROBO_SECRET = getpass.getpass("ROBO_SECRET (está no .env do ig-robo): ").strip()
u = input("Usuário do Instagram: ").strip().lstrip("@")
p = getpass.getpass("Senha do Instagram: ")
cl = Client(); cl.delay_range = [1, 3]
try:
    cl.login(u, p)
except TwoFactorRequired:
    code = input("Código 2FA (app autenticador ou SMS): ").strip()
    cl.login(u, p, verification_code=code)
cl.get_timeline_feed()
print("Login ok como", cl.username)
r = requests.post(f"{ROBO_URL}/sessao", headers={"x-robo": ROBO_SECRET}, json={"username": u, "settings": cl.get_settings()}, timeout=30)
print("Envio para o robô:", r.status_code, r.text[:200])

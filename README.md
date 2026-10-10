# ig-robo · motor de ações do Lidera Social

Serviço Python (instagrapi) que roda no VPS e executa, por conta do Instagram:
- detecção de novos seguidores a cada 15 min (diferença da lista) → lead NS + pesquisa/triagem pela IA (via `ig-api`)
- ações pendentes da fila (`ig_acoes`): seguir + curtir 2 posts + reagir a 1 story, 1ª mensagem (ativar), follow-ups sem placeholder, reagir a story
- limites diários pela rampa do playbook, intervalos aleatórios, só em horário comercial
- pausa automática por 24 h se o Instagram devolver bloqueio de ação / challenge, com alerta opcional no n8n

## Variáveis de ambiente
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (service_role do projeto flow)
- `IG_CRON_SECRET` (o mesmo das Edge Functions; usado para chamar `ig-api`)
- `ROBO_SECRET` (protege os endpoints HTTP)
- `IG_ACCOUNTS` = JSON: `[{"conta_id":"1784...","username":"lidera.food","password":"..."}]`
- `N8N_ALERT_URL` (opcional, webhook que avisa no WhatsApp)
- `ROBO_INTERVAL_MIN` (padrão 15), `ROBO_HORA_INI` (8), `ROBO_HORA_FIM` (20)

Sessões ficam em `/data/sessions/<username>.json` (volume). Primeiro login pode pedir código 2FA: ver logs e usar `POST /2fa {username, code}`.

## Endpoints
- `GET /` estado
- `POST /rodar` força um ciclo agora (header `x-robo: ROBO_SECRET`)
- `POST /2fa` `{username, code}` conclui login com 2FA
- `POST /pausar` `{conta_id, horas}`

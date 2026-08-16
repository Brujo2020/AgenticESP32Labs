# Desplegar el panel en el EC2

Requiere que `002-sdk-mcp-scaffolding` (paquete `sdk_mcp/`) y `panel_api.py`
ya esten en `/home/ubuntu/esp32-hud-idf/servidor` (mismo repo, `git pull`).

```bash
cd /home/ubuntu/esp32-hud-idf/servidor
source venv/bin/activate
pip install -r requirements.txt        # trae fastapi/uvicorn nuevos

# Token del panel -- unico, no lo subas a git (va en .env, ya ignorado)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
echo 'PANEL_TOKEN="<pega el token de arriba>"' >> .env

sudo cp deploy/agentic-voz.service deploy/agentic-panel.service /etc/systemd/system/
sudo cp deploy/agentic-panel.sudoers /etc/sudoers.d/agentic-panel
sudo visudo -c    # valida el sudoers antes de confiar en el
sudo systemctl daemon-reload
sudo systemctl enable --now agentic-voz agentic-panel

# nginx + TLS (una vez, edita el server_name primero)
sudo cp deploy/nginx-panel.conf /etc/nginx/sites-available/panel
sudo ln -s /etc/nginx/sites-available/panel /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d TU-DOMINIO
```

Verificar:

```bash
systemctl status agentic-voz agentic-panel
curl -s http://127.0.0.1:8766/api/salud
```

El panel queda en `https://TU-DOMINIO/` pidiendo el `PANEL_TOKEN` de arriba
para entrar. El bridge de voz (`agentic-voz`) es independiente: si el panel
se cae, la voz sigue — confirma el criterio de aceptación de
`specs/001-panel-administracion-mcp/spec.md`.

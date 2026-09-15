# Desplegar el panel en el EC2

Requiere que `sdk_mcp/` y `panel_api.py` ya esten en el servidor (mismo
repo, `git pull`). **Nota real de despliegue (16 ago 2026)**: el servidor
es una instancia de **AWS Lightsail** (no EC2 puro), y la carpeta del repo
ahi se llama `~/AgenticESP32Labs`, no `~/esp32-hud-idf` -- las unidades
systemd de este directorio (`deploy/*.service`) traen la ruta generica
`esp32-hud-idf` como ejemplo; hay que ajustarla con `sed` (ver mas abajo) a
la ruta real de cada servidor antes de copiarlas a `/etc/systemd/system/`.
Confirmado funcionando: panel accesible por IP con token, `agentic-voz` y
`agentic-panel` corriendo como servicios systemd independientes.

## Camino recomendado (ola 8, operacion): `instalar.sh`

Todo lo de mas abajo -- venv, requirements, token del panel, ajuste de ruta
de las unidades systemd, arranque -- lo hace **un solo script idempotente**,
pensado para "clonar y correr" en un servidor nuevo Y para "volver a correr"
en uno existente tras un `git pull` sin duplicar nada ni pisar secretos:

```bash
cd ~/AgenticESP32Labs && git pull
cd servidor/deploy
./instalar.sh
```

Variables opcionales (ver la cabecera del script para el detalle):

- `SIN_SERVICIOS=1 ./instalar.sh` -- deja el venv y el `.env` listos sin
  tocar systemd (util para probar en local antes de instalar como servicio).
- `TOKEN_OBLIGATORIO=1 ./instalar.sh` -- ademas genera `HUD_TOKEN` y exige
  que los dispositivos se autentiquen (por defecto el servidor arranca en
  modo abierto, una decision explicita -- ver `PROTOCOLO.md`). Genera el
  valor en el `.env` del servidor; grabar el MISMO valor en la NVS de cada
  cuerpo (bola, Stick) sigue siendo un paso aparte, de firmware, que este
  script no puede hacer.

Verificar: `curl -s http://127.0.0.1:8766/api/salud`. Los pasos manuales de
mas abajo son el detalle de lo que `instalar.sh` automatiza -- utiles para
entender que hace, o si algo falla y hay que repetir un paso a mano.

## Arranque de hoy: sin dominio, sin TLS

`agentic-panel.service` escucha en `0.0.0.0:8766` (todas las interfaces) --
el candado queda solo en el token del panel (`PANEL_TOKEN`), no en TLS.
Valido para uso personal; si el panel se va a compartir con alguien mas o
exponer mas alla de eso, hace falta el paso de nginx+TLS de mas abajo.

Conectar por SSH: en Lightsail no hace falta el `.pem` a mano -- consola de
Lightsail -> la instancia -> pestana "Connect" -> boton "Connect using SSH"
(terminal en el navegador, ya autenticada). Si el servidor fuera EC2 clasico
seria `ssh -i tu-llave.pem ubuntu@<ip>`.

```bash
cd ~/AgenticESP32Labs     # o el nombre real de la carpeta en ese servidor
git pull

cd servidor
python3 -m venv venv               # solo si el venv no existe todavia
source venv/bin/activate
pip install -r requirements.txt    # trae fastapi/uvicorn nuevos

# Token del panel -- unico, no se sube a git (va en .env, ya ignorado)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
echo 'PANEL_TOKEN="<pega aqui el token que imprimio el comando de arriba>"' >> .env

# Ajustar la ruta de ejemplo de las unidades a la ruta real de este servidor
# ANTES de copiarlas, si difiere de esp32-hud-idf:
sed -i 's|/home/ubuntu/esp32-hud-idf|/home/ubuntu/AgenticESP32Labs|g'   deploy/agentic-voz.service deploy/agentic-panel.service

sudo cp deploy/agentic-voz.service deploy/agentic-panel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentic-voz agentic-panel

systemctl status agentic-voz agentic-panel --no-pager
curl -s http://127.0.0.1:8766/api/salud
```

**Firewall**: hay que abrir el puerto **8766/tcp** (ademas del 8765 que ya
estaba abierto para el WebSocket de voz).

- **Lightsail**: consola -> la instancia -> pestana "Networking" -> IPv4
  Firewall -> "Add rule" -> Custom / TCP / puerto 8766 -> restringir a tu IP
  si la opcion aparece.
- **EC2 clasico**: Security Groups -> el que usa la instancia -> Inbound
  rules -> Add rule -> Custom TCP, puerto 8766, origen "My IP" (mejor que
  0.0.0.0/0, ya que el trafico va sin cifrar sin el paso de TLS de mas
  abajo).

El panel queda en `http://15.229.88.144:8766/`, pidiendo el `PANEL_TOKEN`
de arriba para entrar. El bridge de voz (`agentic-voz`) es independiente: si
el panel se cae, la voz sigue -- confirma el criterio de aceptacion de
`specs/001-panel-administracion-mcp/spec.md`.

## Mas adelante: dominio + TLS

Si en algun momento hay un dominio apuntando a `15.229.88.144`, se puede
poner nginx delante y candado real. Ahi conviene volver
`agentic-panel.service` a escuchar solo en `127.0.0.1` (nginx hace de unica
puerta publica) -- cambiar `--host 0.0.0.0` por `--host 127.0.0.1` en el
`ExecStart` antes de este paso:

```bash
sudo cp deploy/agentic-panel.sudoers /etc/sudoers.d/agentic-panel
sudo visudo -c    # valida el sudoers antes de confiar en el

sudo cp deploy/nginx-panel.conf /etc/nginx/sites-available/panel
# editar server_name en ese archivo con el dominio real antes de continuar
sudo ln -s /etc/nginx/sites-available/panel /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d TU-DOMINIO
```

`nginx-panel.conf` proxyea DOS cosas: `/` al panel (8766) y `/voz` al
puente de voz (8765, con las cabeceras `Upgrade`/`Connection` que un
WebSocket necesita). Tras el `certbot` de arriba, el firmware podria
conectar a `wss://TU-DOMINIO/voz` en vez de `ws://IP:8765` en claro -- esa
mitad (validar el certificado en el ESP32) todavia no esta hecha, depende
de las olas de firmware (3-7); el lado servidor ya esta listo esperandola.

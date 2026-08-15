#!/usr/bin/env python3
"""
MCP: utilidades de macOS. Volumen, notificaciones nativas y avisos hablados.

Existia en config.yaml y en mcp_catalogo.yaml (servidor 'sistema', activado
por defecto en tools_to_enable) pero el fichero nunca se escribio: activarlo
fallaba con "No such file or directory". Este es el servidor que faltaba,
con el mismo patron sin dependencias externas que mcps/mac.py (todo sale de
herramientas ya presentes en macOS: osascript, afplay, say).
"""
import shlex
import subprocess
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sistema")


def _sh(cmd: str, timeout: int = 6) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ""


def _osascript(script: str, timeout: int = 6) -> str:
    try:
        r = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return f"error: {e}"


@mcp.tool()
def volumen() -> dict:
    """Volumen actual del sistema (0-100) y si esta silenciado."""
    salida = _osascript("output volume of (get volume settings)")
    silenciado = _osascript("output muted of (get volume settings)")
    try:
        pct = int(salida)
    except ValueError:
        pct = None
    return {"volumen_pct": pct, "silenciado": silenciado.strip().lower() == "true"}


@mcp.tool()
def volumen_fijar(pct: int) -> dict:
    """Fija el volumen del sistema.

    Args:
        pct: 0-100. Se recorta a ese rango si llega fuera.
    """
    pct = max(0, min(100, int(pct)))
    _osascript(f"set volume output volume {pct}")
    return volumen()


@mcp.tool()
def silenciar(activo: bool = True) -> dict:
    """Silencia o restaura el sonido del sistema sin tocar el nivel de volumen."""
    _osascript(f"set volume output muted {'true' if activo else 'false'}")
    return volumen()


@mcp.tool()
def notificar(titulo: str, mensaje: str, sonido: bool = True) -> str:
    """Muestra una notificacion nativa de macOS (Centro de Notificaciones).

    Distinto de hud_notificar (que pinta en el ESP32): esta aparece en la
    pantalla del Mac, para cuando el usuario esta mirando el ordenador y no
    el HUD.

    Args:
        titulo: cabecera de la notificacion.
        mensaje: cuerpo del aviso.
        sonido: si reproduce el sonido de notificacion por defecto.
    """
    t = titulo.replace('"', "'")
    m = mensaje.replace('"', "'")
    script = f'display notification "{m}" with title "{t}"'
    if sonido:
        script += ' sound name "default"'
    _osascript(script)
    return f"Notificado: {titulo} — {mensaje}"


@mcp.tool()
def decir(texto: str, voz: str = "") -> str:
    """Sintetiza voz por el altavoz del Mac con 'say'.

    Util para avisos que el usuario debe oir aunque no mire ninguna pantalla.
    Distinto de la voz del ESP32 (componente audio/voice del firmware): esta
    sale por el altavoz del ordenador que ejecuta el puente.

    Args:
        texto: lo que se va a decir. Frases cortas y naturales.
        voz: nombre de una voz instalada (ej. 'Monica', 'Jorge'). Vacio = la
             voz por defecto del sistema.
    """
    cmd = ["say"]
    if voz:
        cmd += ["-v", voz]
    cmd.append(texto)
    try:
        subprocess.run(cmd, timeout=30)
        return f"Dicho: {texto}"
    except Exception as e:
        return f"No se pudo hablar: {e}"


@mcp.tool()
def no_molestar(activo: bool | None = None) -> dict:
    """Consulta o cambia el modo 'No molestar' (Focus) de macOS.

    Sin 'activo' solo informa del estado. Requiere macOS con atajos de
    Focus disponibles por linea de comandos (shortcuts); si no estan, se
    devuelve un aviso en vez de fallar en silencio.

    Args:
        activo: True para activarlo, False para desactivarlo, None para
                solo consultar.
    """
    if activo is not None:
        atajo = "Activar No Molestar" if activo else "Desactivar No Molestar"
        salida = _sh(f'shortcuts run {shlex.quote(atajo)}')
        if not salida and salida != "":
            pass  # 'shortcuts' no devuelve nada en exito; no es señal de fallo
    estado = _sh("defaults -currentHost read com.apple.notificationcenterui doNotDisturb 2>/dev/null").strip()
    return {
        "activo": estado == "1" if estado in ("0", "1") else None,
        "nota": ("Cambiar el estado requiere un atajo de macOS llamado "
                 "'Activar No Molestar' / 'Desactivar No Molestar' en la app "
                 "Atajos; sin el, esta tool solo informa.") if activo is not None else None,
    }


@mcp.tool()
def abrir_app(nombre: str) -> str:
    """Abre (o trae al frente) una aplicacion por su nombre, ej. 'Blender', 'Safari'."""
    n = nombre.replace('"', "'")
    salida = _sh(f'open -a "{n}" 2>&1')
    if salida.strip():
        return f"No se pudo abrir '{nombre}': {salida.strip()}"
    return f"'{nombre}' abierta."


if __name__ == "__main__":
    mcp.run()

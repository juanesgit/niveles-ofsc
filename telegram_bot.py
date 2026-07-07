import re
import time
import logging
import threading
import requests
from dotenv import load_dotenv
import os

from oracle import buscar_cuenta, cerrar_driver, renovar_cookies_manual, _verificar_cookies_validas

# ─────────────────────────────────────────────────────
# ENTORNO
# ─────────────────────────────────────────────────────

load_dotenv()

# ─────────────────────────────────────────────────────
# LOGGING — reutiliza la config centralizada de oracle.py
# ─────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# CONFIG  (desde .env)
# ─────────────────────────────────────────────────────

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN", "")
_chat_id_raw    = os.getenv("ALLOWED_CHAT_ID", "")
ALLOWED_CHAT_ID = int(_chat_id_raw) if _chat_id_raw.strip().lstrip("-").isdigit() else None

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN no definido en .env")

# ─────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Evita búsquedas simultáneas en Oracle (el driver no es thread-safe)
_lock = threading.Lock()


def enviar(chat_id, texto):
    """Envía un mensaje de texto al chat indicado."""
    try:
        requests.post(
            f"{BASE_URL}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       texto,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
    except Exception as e:
        log.error(f"Error enviando mensaje: {e}")


def procesar_cuentas(chat_id, cuentas: list[str]):
    """
    Busca una o varias cuentas en Oracle de forma secuencial.
    Corre en hilo separado para no bloquear el polling.
    Al finalizar lotes > 1 envía un resumen tabular.
    """
    resultados = []   # lista de (cuenta, resultado)

    with _lock:
        for cuenta in cuentas:
            enviar(chat_id, f"🔎 Buscando *{cuenta}*...")
            resultado = buscar_cuenta(cuenta)
            enviar(chat_id, resultado)
            resultados.append((cuenta, resultado))
            if len(cuentas) > 1:
                time.sleep(1)

    if len(cuentas) > 1:
        ok    = sum(1 for _, r in resultados if r.startswith("✅"))
        ya    = sum(1 for _, r in resultados if r.startswith("ℹ️"))
        error = len(resultados) - ok - ya

        lineas = [f"*Resumen — {len(cuentas)} cuentas procesadas*"]
        lineas.append(f"✅ Validadas: {ok}  |  ℹ️ Ya validadas: {ya}  |  ❌ Errores: {error}")
        lineas.append("")
        for cuenta, resultado in resultados:
            icono = "✅" if resultado.startswith("✅") else ("ℹ️" if resultado.startswith("ℹ️") else "❌")
            lineas.append(f"{icono} `{cuenta}`")

        enviar(chat_id, "\n".join(lineas))


def extraer_cuentas(texto: str) -> list[str]:
    """
    Extrae todos los números de cuenta del mensaje.
    Acepta cuentas separadas por espacios, comas, saltos de línea o punto y coma.
    """
    tokens = re.split(r"[\s,;\n]+", texto.strip())
    # Filtra tokens que parezcan números de cuenta (solo dígitos, mín. 5 chars)
    return [t for t in tokens if t.isdigit() and len(t) >= 5]

# ─────────────────────────────────────────────────────
# POLLING
# ─────────────────────────────────────────────────────

def _renovar_en_hilo(chat_id):
    """Ejecuta renovar_cookies_manual() en hilo separado y notifica el resultado."""
    with _lock:
        enviar(chat_id,
            "🔑 *Renovación de cookies iniciada*\n"
            "Se abrirá una ventana de Edge. Resuelve el MFA de Microsoft y el script "
            "detectará automáticamente cuando hayas entrado a Oracle (máx. 5 min)."
        )
        ok = renovar_cookies_manual(timeout=300)
        if ok:
            enviar(chat_id, "✅ *Cookies renovadas correctamente.* El bot está listo para buscar cuentas.")
        else:
            enviar(chat_id, "❌ *No se pudieron renovar las cookies.* Revisa los logs o intenta de nuevo.")


def iniciar_bot():
    offset = 0
    log.info("🤖 Bot Telegram iniciado, esperando mensajes...")

    while True:
        try:
            resp = requests.get(
                f"{BASE_URL}/getUpdates",
                params={"timeout": 30, "offset": offset},
                timeout=40,
            )
            data = resp.json()

            for update in data.get("result", []):
                offset  = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                texto   = msg.get("text", "").strip()

                if not texto or not chat_id:
                    continue

                # ── Filtrar chats no autorizados ──────────────────────────
                if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
                    log.warning(f"⚠️  Chat no autorizado: {chat_id}")
                    enviar(chat_id, "🚫 No tienes permiso para usar este bot.")
                    continue

                log.info(f"📩 [{chat_id}] → {texto}")

                # ── Comandos especiales ───────────────────────────────────
                if texto.lower() == "/start":
                    enviar(chat_id,
                        "👋 *Bot Oracle activo*\n\n"
                        "Mándame un número de cuenta y lo busco en Oracle.\n"
                        "También puedes enviar varios separados por coma o salto de línea.\n\n"
                        "Comandos:\n"
                        "• /estado — estado del bot\n"
                        "• /verificar — comprueba si la sesión está activa\n"
                        "• /renovar — renueva las cookies (abre Edge)\n"
                        "• /cerrar — cierra el navegador Edge\n"
                        "• /ayuda — muestra esta ayuda"
                    )
                    continue

                if texto.lower() in ("/ayuda", "/help"):
                    enviar(chat_id,
                        "📖 *Ayuda*\n\n"
                        "Envía uno o varios números de cuenta:\n"
                        "`123456789`\n"
                        "`123456789, 987654321`\n"
                        "`123456789\n987654321`\n\n"
                        "Comandos disponibles:\n"
                        "• /start — bienvenida\n"
                        "• /estado — estado del bot\n"
                        "• /verificar — comprueba si la sesión de Oracle está activa\n"
                        "• /renovar — renueva las cookies de sesión (abre Edge visible)\n"
                        "• /cerrar — cierra Edge\n"
                        "• /ayuda — esta ayuda"
                    )
                    continue

                if texto.lower() == "/estado":
                    ocupado = _lock.locked()
                    estado  = "🔄 Procesando una búsqueda ahora mismo." if ocupado else "✅ Libre y listo para buscar."
                    enviar(chat_id, f"*Estado del bot:* {estado}")
                    continue

                if texto.lower() == "/verificar":
                    enviar(chat_id, "🔍 Verificando estado de la sesión...")
                    ok = _verificar_cookies_validas()
                    if ok:
                        enviar(chat_id, "✅ *Sesión activa* — las cookies son válidas.")
                    else:
                        enviar(chat_id,
                            "❌ *Sesión expirada* — las cookies no funcionan.\n"
                            "Usa /renovar para renovarlas."
                        )
                    continue

                if texto.lower() == "/renovar":
                    if _lock.locked():
                        enviar(chat_id, "⚠️ Hay una búsqueda en curso. Espera a que termine antes de renovar.")
                        continue
                    hilo_renovar = threading.Thread(
                        target=_renovar_en_hilo,
                        args=(chat_id,),
                        daemon=True,
                    )
                    hilo_renovar.start()
                    continue

                if texto.lower() == "/cerrar":
                    enviar(chat_id, "🛑 Cerrando Edge...")
                    cerrar_driver()
                    enviar(chat_id, "✅ Edge cerrado. La próxima búsqueda lo abrirá de nuevo.")
                    continue

                # ── Búsqueda de cuenta(s) ─────────────────────────────────
                cuentas = extraer_cuentas(texto)

                if not cuentas:
                    enviar(chat_id,
                        "⚠️  No reconocí ningún número de cuenta en tu mensaje.\n"
                        "Envía solo dígitos, por ejemplo: `123456789`"
                    )
                    continue

                if len(cuentas) == 1:
                    enviar(chat_id, f"🔎 Buscando cuenta *{cuentas[0]}* en Oracle...")
                else:
                    enviar(chat_id, f"🔎 Encontré *{len(cuentas)} cuentas* para buscar, procesando...")

                hilo = threading.Thread(
                    target=procesar_cuentas,
                    args=(chat_id, cuentas),
                    daemon=True,
                )
                hilo.start()

        except requests.exceptions.ReadTimeout:
            pass   # timeout normal del long-polling, se reintenta solo

        except Exception as e:
            log.exception(f"Error en loop Telegram: {e}")
            time.sleep(5)

# ─────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        iniciar_bot()
    except KeyboardInterrupt:
        log.info("🛑 Bot detenido por el usuario")
        cerrar_driver()
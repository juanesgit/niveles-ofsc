import os
import glob
import time
import json
import logging
import threading
from pathlib import Path

from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    from selenium.webdriver.edge.service import Service
    from selenium.webdriver.edge.options import Options
else:
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options

# ─────────────────────────────────────────────────────
# ENTORNO
# ─────────────────────────────────────────────────────

load_dotenv()

# ─────────────────────────────────────────────────────
# LOGS — configuración única centralizada
# ─────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "oracle_bot.log"),
            encoding="utf-8",
        ),
    ],
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# CONFIG  (valores desde .env con fallback)
# ─────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PERFIL_BROWSER       = os.path.join(BASE_DIR, "browser_perfil")
COOKIES_FILE         = os.path.join(BASE_DIR, "oracle_cookies.json")
MSEDGEDRIVER_MANUAL  = os.path.join(BASE_DIR, "msedgedriver.exe")
SCREENSHOTS_MAX      = 10   # máximo de screenshots de error a conservar

URL_ORACLE  = os.getenv("ORACLE_URL",     "https://amx-res-co.fs.ocs.oraclecloud.com/")
USUARIO     = os.getenv("ORACLE_USUARIO", "38101491@claro.com.co")

KEEPALIVE_SEGUNDOS = int(os.getenv("KEEPALIVE_SEGUNDOS", "240"))
PERMITIR_SSO_SILENCIOSO_WINDOWS = os.getenv(
    "PERMITIR_SSO_SILENCIOSO_WINDOWS", "true"
).lower() == "true"

# ─────────────────────────────────────────────────────
# SELECTORES  (centralizar facilita adaptar cambios de UI)
# ─────────────────────────────────────────────────────

SEL_BARRA_BUSQUEDA  = "input.global-search-bar-input-button"
SEL_OVERLAY         = "#plugin-overlay-window"
SEL_ACTIVITY_ICON   = "div.activity-icon.icon"
SEL_FOUND_ITEM      = "div.found-item-activity"
SEL_ACTIVITY_TITLE  = "div.activity-title"
COLOR_ORDEN_ACTIVA  = "#a7d100"   # también rgb(167,209,0)

XP_CHECKBOX_HFC = [
    "//input[@type='checkbox' and contains(@aria-label, 'Validacion Niveles HFC')]",
    "//label[contains(text(), 'Validacion Niveles HFC')]/preceding-sibling::input[@type='checkbox']",
    "//*[contains(text(), 'Validacion Niveles HFC')]//ancestor::tr//input[@type='checkbox']",
    "//span[contains(text(), 'Validacion Niveles HFC')]/preceding::input[@type='checkbox'][1]",
    "//input[@type='checkbox']",
]

XP_BOTON_OK = [
    "//button[@type='submit' and contains(.,'OK')]",
    "//button[contains(text(),'OK')]",
    "//button[contains(.,'OK')]",
    "//*[contains(@class,'btn') and contains(.,'OK')]",
]


# ─────────────────────────────────────────────────────
# EXCEPCIONES PROPIAS
# ─────────────────────────────────────────────────────

class SesionExpiradaError(Exception):
    """
    Se lanza cuando la sesión de Oracle expiró y no se pudo
    renovar automáticamente (ni con cookies ni con SSO silencioso),
    y hace falta reautenticación manual (MFA) en modo visible.
    """
    pass


# ─────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────

def _guardar_screenshot(driver, nombre: str) -> str:
    """
    Guarda un screenshot de error y elimina los más antiguos
    para no acumular más de SCREENSHOTS_MAX archivos.
    """
    ruta = os.path.join(BASE_DIR, f"{nombre}.png")
    try:
        driver.save_screenshot(ruta)
        log.info(f"📸 Screenshot guardado: {ruta}")
        _limpiar_screenshots()
    except Exception as e:
        log.warning(f"⚠️ No se pudo guardar screenshot: {e}")
    return ruta


def _limpiar_screenshots():
    """Mantiene solo los SCREENSHOTS_MAX screenshots más recientes."""
    patron = os.path.join(BASE_DIR, "error_*.png")
    archivos = sorted(glob.glob(patron), key=os.path.getmtime)
    for viejo in archivos[:-SCREENSHOTS_MAX]:
        try:
            os.remove(viejo)
            log.info(f"🗑️ Screenshot antiguo eliminado: {viejo}")
        except Exception:
            pass


def _esperar_elemento(driver, by, selector, timeout=15):
    """Espera a que un elemento sea visible y lo devuelve."""
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, selector))
    )


def _esperar_clickable(driver, xpath, timeout=15):
    """Espera a que un elemento sea clickable por XPath y lo devuelve."""
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.XPATH, xpath))
    )


# ─────────────────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────────────────

_driver      = None
_buscando    = False          # True mientras hay una búsqueda activa
_keepalive_t = None           # hilo keepalive
_driver_lock = threading.Lock()


def _crear_service():
    """
    Obtiene el driver correcto según el SO:
    - Windows → msedgedriver (Edge)
    - Linux/LXC → chromedriver (Chrome, igual que BotCCOT)

    Orden de búsqueda en Windows:
      1. webdriver-manager (descarga automática)
      2. msedgedriver.exe local
      3. PATH del sistema

    Orden en Linux:
      1. Selenium Manager (automático, incluido en Selenium 4.x)
      2. chromedriver en el PATH
    """
    if IS_WINDOWS:
        # ─ Windows: Edge ───────────────────────────────────────
        try:
            from webdriver_manager.microsoft import EdgeChromiumDriverManager
            ruta = EdgeChromiumDriverManager().install()
            log.info(f"✅ msedgedriver via webdriver-manager: {ruta}")
            return Service(ruta)
        except Exception as e:
            log.warning(f"⚠️ webdriver-manager falló: {e}")
        if os.path.isfile(MSEDGEDRIVER_MANUAL):
            log.info(f"✅ msedgedriver local: {MSEDGEDRIVER_MANUAL}")
            return Service(MSEDGEDRIVER_MANUAL)
        log.info("✅ Usando msedgedriver del PATH del sistema")
        return Service()
    else:
        # ─ Linux/LXC: Chrome (igual que BotCCOT) ──────────────────
        import shutil
        pth = shutil.which("chromedriver")
        if pth:
            log.info(f"✅ chromedriver del PATH: {pth}")
            return Service(pth)
        log.info("✅ Usando Selenium Manager para chromedriver (descarga automática)")
        return Service()


def obtener_driver():
    global _driver

    with _driver_lock:
        if _driver is not None:
            try:
                _ = _driver.current_url
                log.info("♻️ Reutilizando sesión existente")
                return _driver
            except Exception:
                log.warning("⚠️ Sesión anterior inválida, creando nueva...")
                try:
                    _driver.quit()
                except Exception:
                    pass
                _driver = None

        # ⭐ DETECTAR SI HAY COOKIES GUARDADAS ⭐
        hay_cookies = os.path.exists(COOKIES_FILE)

        # ⭐ VERIFICAR QUE NO ESTÉ VACÍO ⭐
        cookies_validas = False
        if hay_cookies:
            try:
                with open(COOKIES_FILE, 'r') as f:
                    cookies_data = json.load(f)
                    if cookies_data and len(cookies_data) > 0:
                        cookies_validas = True
            except Exception:
                pass

        if cookies_validas:
            log.info("🚀 Iniciando Edge en modo HEADLESS (sin ventana) - ya hay cookies guardadas")
            modo_headless = True
        else:
            log.info("🚨 PRIMERA VEZ - No hay cookies válidas")
            log.info("🚀 Iniciando Edge en modo VISIBLE para login manual (solo esta vez)")
            modo_headless = False

        options = Options()

        if IS_WINDOWS:
            # ─ Windows: opciones específicas de Edge ──────────────────
            if not PERMITIR_SSO_SILENCIOSO_WINDOWS:
                options.add_argument("-inprivate")
                options.add_argument(
                    "--disable-features=msEdgeProfileSigninConstraints,msSingleSignOnOSExchange"
                )
            options.add_argument(f"--user-data-dir={PERFIL_BROWSER}")
            options.add_argument("--profile-directory=Default")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
        else:
            # ─ Linux/LXC: opciones de Chrome (igual que BotCCOT) ─────
            import shutil, tempfile
            chrome_data = Path(tempfile.gettempdir()) / "oracle-chrome-data"
            try:
                chrome_data.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            options.add_argument(f"--user-data-dir={chrome_data}")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-plugins")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-geolocation")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

        # ─ Argumentos comunes (Windows + Linux) ───────────────────
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--start-maximized")

        if modo_headless:
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")

        service = _crear_service()

        if IS_WINDOWS:
            log.info("🖥️ Iniciando Microsoft Edge (Windows)")
            _driver = webdriver.Edge(service=service, options=options)
        else:
            log.info("🖥️ Iniciando Google Chrome (Linux/LXC)")
            _driver = webdriver.Chrome(service=service, options=options)

        _driver.execute_script("""
            Object.defineProperty(
                navigator,
                'webdriver',
                { get: () => undefined }
            )
        """)

        browser_name = "Edge" if IS_WINDOWS else "Chrome"
        if modo_headless:
            log.info(f"✅ {browser_name} HEADLESS iniciado correctamente")
        else:
            log.info(f"✅ {browser_name} VISIBLE iniciado - HAZ LOGIN MANUAL CON MFA")

        log.info(f"🌐 URL Oracle: {URL_ORACLE} | Usuario: {USUARIO}")
        return _driver


def cerrar_driver():
    global _driver

    with _driver_lock:
        if _driver:
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None
            log.info("🛑 Driver cerrado")


# ─────────────────────────────────────────────────────
# KEEPALIVE — refresca Oracle cada 4 min si está libre
# ─────────────────────────────────────────────────────

def _loop_keepalive():
    """Hilo daemon: refresca Oracle cada KEEPALIVE_SEGUNDOS si no hay búsqueda activa."""
    global _buscando, _driver

    while True:
        time.sleep(KEEPALIVE_SEGUNDOS)

        if _buscando:
            log.info("⏸️ Keepalive pausado — hay búsqueda activa")
            continue

        if _driver is None:
            log.info("⏸️ Keepalive pausado — driver no iniciado aún")
            continue

        try:
            url_actual = _driver.current_url
            dominio_oracle = URL_ORACLE.split("/")[2]
            if dominio_oracle in url_actual:
                log.info("🔄 Keepalive: refrescando Oracle para mantener sesión...")
                _driver.refresh()
                # Esperar barra de búsqueda en lugar de sleep fijo
                try:
                    WebDriverWait(_driver, 10).until(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, SEL_BARRA_BUSQUEDA)
                        )
                    )
                    campo = _driver.find_element(By.CSS_SELECTOR, SEL_BARRA_BUSQUEDA)
                    _driver.execute_script("arguments[0].focus();", campo)
                except Exception:
                    pass

                log.info("✅ Keepalive: página refrescada")
            else:
                log.info(f"⏸️ Keepalive: URL inesperada ({url_actual}), no se refresca")
        except Exception as e:
            log.warning(f"⚠️ Keepalive: no se pudo refrescar — {e}")
            with _driver_lock:
                _driver = None
            log.info("🔄 Keepalive: driver marcado como cerrado, se recreará en la próxima búsqueda")
            time.sleep(KEEPALIVE_SEGUNDOS)


def iniciar_keepalive():
    """Arranca el hilo keepalive una sola vez."""
    global _keepalive_t

    if _keepalive_t is not None and _keepalive_t.is_alive():
        return

    _keepalive_t = threading.Thread(
        target=_loop_keepalive,
        daemon=True,
        name="keepalive-oracle"
    )
    _keepalive_t.start()
    log.info(f"⏱️ Keepalive iniciado (cada {KEEPALIVE_SEGUNDOS // 60} min)")


# ─────────────────────────────────────────────────────
# GESTIÓN DE COOKIES
# ─────────────────────────────────────────────────────

def guardar_cookies(driver):
    try:
        cookies = driver.get_cookies()
        with open(COOKIES_FILE, 'w') as f:
            json.dump(cookies, f, indent=2)
        log.info(f"✅ Cookies guardadas ({len(cookies)} cookies)")
        log.info(f"📁 Ubicación: {COOKIES_FILE}")
        return True
    except Exception as e:
        log.error(f"❌ Error guardando cookies: {e}")
        return False


def cargar_cookies(driver):
    """
    Carga las cookies guardadas, agrupándolas por dominio.

    IMPORTANTE: Selenium solo permite añadir una cookie si el navegador
    está posicionado exactamente en ese dominio (o un subdominio). Como
    las cookies guardadas suelen venir de varios dominios distintos
    (Oracle + el proveedor de SSO/Microsoft), hay que visitar cada
    dominio antes de inyectar sus cookies correspondientes. Si no se
    hace así, salen errores "invalid cookie domain: Cookie 'domain'
    mismatch" y la sesión nunca se restaura.
    """
    try:
        if not os.path.exists(COOKIES_FILE):
            log.info("📁 No hay archivo de cookies previo")
            return False

        with open(COOKIES_FILE, 'r') as f:
            cookies = json.load(f)

        if not cookies or len(cookies) == 0:
            log.info("📁 Archivo de cookies vacío")
            return False

        # Agrupar cookies por dominio
        por_dominio = {}
        for cookie in cookies:
            dominio = (cookie.get('domain') or '').lstrip('.')
            if not dominio:
                continue
            por_dominio.setdefault(dominio, []).append(cookie)

        total_cargadas = 0

        for dominio, lote in por_dominio.items():
            try:
                driver.get(f"https://{dominio}")
                time.sleep(1)
            except Exception as e:
                log.warning(f"⚠️ No se pudo visitar dominio {dominio} para cargar sus cookies: {e}")
                continue

            for cookie in lote:
                cookie_limpia = dict(cookie)  # copia para no mutar el original
                if 'expiry' in cookie_limpia:
                    try:
                        cookie_limpia['expiry'] = int(cookie_limpia['expiry'])
                    except Exception:
                        cookie_limpia.pop('expiry', None)

                # 'sameSite' a veces trae valores que Selenium/Edge rechaza
                cookie_limpia.pop('sameSite', None)

                try:
                    driver.add_cookie(cookie_limpia)
                    total_cargadas += 1
                except Exception as e:
                    log.warning(f"No se pudo cargar cookie {cookie.get('name')} ({dominio}): {e}")

        # Volver siempre a Oracle al final, con todas las cookies ya puestas
        driver.get(URL_ORACLE)
        time.sleep(2)

        log.info(f"✅ Cookies cargadas ({total_cargadas}/{len(cookies)} cookies)")
        return total_cargadas > 0

    except Exception as e:
        log.error(f"❌ Error cargando cookies: {e}")
        return False


# ─────────────────────────────────────────────────────
# HELPERS LOGIN
# ─────────────────────────────────────────────────────

def _dentro_de_oracle(driver):
    try:
        driver.find_element(
            By.CSS_SELECTOR,
            "input.global-search-bar-input-button"
        )
        return True
    except Exception:
        return False


def _campo_usuario(driver):
    selectores = [
        (By.ID,           "username"),
        (By.NAME,         "username"),
        (By.NAME,         "email"),
        (By.CSS_SELECTOR, "input[type='email']"),
    ]
    for tipo, sel in selectores:
        try:
            campo = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((tipo, sel))
            )
            return campo
        except Exception:
            pass
    return None


def _boton_sso(driver):
    selectores = [
        (By.ID,           "sign-in-with-sso"),
        (By.XPATH,        "//button[contains(., 'SSO')]"),
        (By.CSS_SELECTOR, "button[type='submit']"),
    ]
    for tipo, sel in selectores:
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((tipo, sel))
            )
            return btn
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────
# NOTIFICACIÓN TELEGRAM (alertas internas del bot)
# ─────────────────────────────────────────────────────

def _notificar_telegram(mensaje: str) -> None:
    """
    Envía una alerta interna al chat autorizado.
    Usa las mismas variables de entorno que telegram_bot.py.
    No lanza excepción si falla (es solo una alerta).
    """
    token   = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("ALLOWED_CHAT_ID", "")
    if not token or not chat_id:
        log.debug("[TELEGRAM] Token o chat_id no configurados, alerta omitida")
        return
    try:
        import requests as _req
        _req.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": mensaje, "parse_mode": "Markdown"},
            timeout=10,
        )
        log.info("[TELEGRAM] Alerta enviada")
    except Exception as e:
        log.warning(f"[TELEGRAM] No se pudo enviar alerta: {e}")


# ─────────────────────────────────────────────────────
# RENOVACIÓN DE COOKIES  (patrón BotCCOT)
# ─────────────────────────────────────────────────────

def renovar_cookies_manual(timeout: int = 300) -> bool:
    """
    Abre un driver Edge TEMPORAL y VISIBLE (sin afectar el singleton).
    Automatiza el ingreso del usuario y botón SSO, luego espera con
    WebDriverWait hasta detectar que Oracle cargó correctamente.
    NO usa input() — el usuario solo resuelve el MFA en la ventana
    que se abre; el script detecta el éxito automáticamente.
    Guarda las cookies y cierra el driver temporal al terminar.
    """
    log.info("🔑 Iniciando renovación de cookies (driver temporal visible)...")

    options = Options()

    if IS_WINDOWS:
        if not PERMITIR_SSO_SILENCIOSO_WINDOWS:
            options.add_argument("-inprivate")
            options.add_argument(
                "--disable-features=msEdgeProfileSigninConstraints,msSingleSignOnOSExchange"
            )
        options.add_argument(f"--user-data-dir={PERFIL_BROWSER}")
        options.add_argument("--profile-directory=Default")
    else:
        import shutil, tempfile
        chrome_data = Path(tempfile.gettempdir()) / "oracle-chrome-login"
        try:
            chrome_data.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        options.add_argument(f"--user-data-dir={chrome_data}")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = _crear_service()
    if IS_WINDOWS:
        driver_tmp = webdriver.Edge(service=service, options=options)
    else:
        driver_tmp = webdriver.Chrome(service=service, options=options)

    try:
        driver_tmp.execute_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined })
        """)

        log.info(f"🌐 Abriendo Oracle en modo visible: {URL_ORACLE}")
        driver_tmp.get(URL_ORACLE)

        # Paso 1: intentar enviar usuario + botón SSO automáticamente
        campo = _campo_usuario(driver_tmp)
        if campo:
            try:
                campo.clear()
                campo.send_keys(USUARIO)
                time.sleep(0.3)
                boton = _boton_sso(driver_tmp)
                if boton:
                    log.info(f"🚀 Enviando usuario {USUARIO} y presionando SSO...")
                    boton.click()
            except Exception as e:
                log.warning(f"⚠️ No se pudo automatizar SSO: {e} — el usuario deberá hacerlo manualmente")
        else:
            log.info("ℹ️ No se encontró campo usuario — puede que SSO se maneje vía perfil de Edge")

        # Paso 2: instrucciones en log (no en input())
        log.info("=" * 70)
        log.info("📢 ACCIÓN REQUERIDA EN LA VENTANA DE EDGE:")
        log.info("   1. Resuelve el MFA de Microsoft (contraseña, autenticador, etc.)")
        log.info("   2. Acepta 'Mantener sesión iniciada' si aparece")
        log.info(f"   El script detectará automáticamente cuando entres a Oracle (máx. {timeout}s)")
        log.info("=" * 70)

        # Paso 3: WebDriverWait detecta automáticamente el login exitoso
        wait = WebDriverWait(driver_tmp, timeout, poll_frequency=2)
        try:
            wait.until(
                lambda d: bool(d.find_elements(By.CSS_SELECTOR, SEL_BARRA_BUSQUEDA))
            )
            log.info("✅ Login detectado automáticamente — capturando cookies...")
        except TimeoutException:
            raise RuntimeError(
                f"Timeout de {timeout}s esperando login manual. "
                "Intenta de nuevo o revisa que el perfil de Edge tenga acceso a Oracle."
            )

        time.sleep(2)   # pequeña pausa para que el navegador termine de persistir cookies
        cookies = driver_tmp.get_cookies()
        with open(COOKIES_FILE, "w") as f:
            json.dump(cookies, f, indent=2)
        log.info(f"✅ Cookies renovadas y guardadas ({len(cookies)} cookies)")
        return True

    except Exception as e:
        log.error(f"❌ Error en renovación manual de cookies: {e}")
        return False
    finally:
        try:
            driver_tmp.quit()
        except Exception:
            pass
        log.info("🔒 Driver temporal cerrado")


# ─────────────────────────────────────────────────────
# VERIFICAR SESIÓN  (patrón BotCCOT ensure_session)
# ─────────────────────────────────────────────────────

def _verificar_cookies_validas() -> bool:
    """
    Verifica rápidamente si las cookies guardadas permiten acceder a Oracle
    usando un driver headless temporal (no afecta el singleton).
    """
    if not os.path.exists(COOKIES_FILE):
        return False
    try:
        with open(COOKIES_FILE) as f:
            cookies = json.load(f)
        if not cookies:
            return False
    except Exception:
        return False

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = _crear_service()
    if IS_WINDOWS:
        driver_chk = webdriver.Edge(service=service, options=options)
    else:
        driver_chk = webdriver.Chrome(service=service, options=options)
    try:
        cargar_cookies(driver_chk)
        return _dentro_de_oracle(driver_chk)
    except Exception:
        return False
    finally:
        try:
            driver_chk.quit()
        except Exception:
            pass


def ensure_session() -> bool:
    """
    Patrón BotCCOT: verifica la sesión y renueva cookies si es necesario.

    Flujo:
      1. Carga cookies guardadas → verifica que Oracle responda
      2. Si son válidas → OK
      3. Si expiraron → notifica por Telegram y lanza renovar_cookies_manual()
      4. Si la renovación falla → lanza SesionExpiradaError

    Esta función se llama ANTES de obtener el driver singleton,
    por lo que no interfiere con búsquedas en curso.
    """
    log.info("[SESSION] Verificando estado de cookies...")

    if _verificar_cookies_validas():
        log.info("[SESSION] ✅ Cookies válidas — sesión activa")
        return True

    log.warning("[SESSION] ⚠️ Cookies inválidas o expiradas")

    _notificar_telegram(
        "⚠️ *Oracle OFSC — Sesión Expirada*\n\n"
        "Las cookies de sesión han expirado.\n"
        "El bot intentará renovarlas automáticamente.\n"
        "Por favor resuelve el MFA en la ventana de Edge que se abrirá."
    )

    log.info("[SESSION] Iniciando renovación de cookies...")
    if renovar_cookies_manual():
        _notificar_telegram(
            "✅ *Oracle OFSC — Sesión Renovada*\n"
            "Las cookies se renovaron correctamente. El bot está activo."
        )
        return True

    _notificar_telegram(
        "❌ *Oracle OFSC — Error de Sesión*\n\n"
        "No se pudo renovar la sesión automáticamente.\n"
        "Ejecuta manualmente: `python oracle.py --login`"
    )
    raise SesionExpiradaError(
        "No se pudo renovar la sesión de Oracle. "
        "Ejecuta: python oracle.py --login"
    )


# ─────────────────────────────────────────────────────
# LOGIN CON COOKIES
# ─────────────────────────────────────────────────────

def hacer_login(driver, force_login=False):
    if force_login:
        log.info("🔐 Forzando renovación de cookies (force_login=True)...")
        renovar_cookies_manual()

    log.info("🌐 Abriendo Oracle con cookies guardadas...")
    driver.get(URL_ORACLE)
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass

    if cargar_cookies(driver):
        driver.refresh()
        try:
            WebDriverWait(driver, 10).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass
        if _dentro_de_oracle(driver):
            log.info("✅ Sesión restaurada con cookies (sin MFA)")
            return True
        log.warning("⚠️ Cookies cargadas pero no funcionaron — renovando...")

    # Cookies fallaron: delegar al flujo ensure_session
    try:
        if os.path.exists(COOKIES_FILE):
            os.remove(COOKIES_FILE)
            log.info("🗑️ Cookies inválidas eliminadas")
    except Exception:
        pass

    raise SesionExpiradaError(
        "No se pudo restaurar la sesión con cookies. "
        "Llama a ensure_session() antes de buscar cuentas."
    )


# ─────────────────────────────────────────────────────
# BUSCAR Y VALIDAR CUENTA
# ─────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(5),
    retry=retry_if_exception_type(WebDriverException),
    reraise=True,
)
def buscar_cuenta(cuenta, force_login=False):
    global _buscando

    # Verificar/renovar sesión antes de obtener el driver singleton
    ensure_session()

    driver = obtener_driver()
    iniciar_keepalive()
    _buscando = True

    try:
        hacer_login(driver, force_login=force_login)

        log.info(f"🔎 Buscando cuenta: {cuenta}")

        # Esperar que desaparezca overlay si existe
        try:
            WebDriverWait(driver, 5).until(
                EC.invisibility_of_element_located((By.CSS_SELECTOR, SEL_OVERLAY))
            )
            log.info("✅ Overlay desapareció")
        except TimeoutException:
            pass

        # INPUT BÚSQUEDA
        input_busqueda = WebDriverWait(driver, 30).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, SEL_BARRA_BUSQUEDA))
        )

        cuenta_str = str(cuenta)

        # Limpiar y escribir carácter a carácter
        driver.execute_script("arguments[0].value = '';", input_busqueda)

        actions = ActionChains(driver)
        actions.move_to_element(input_busqueda).click()
        for caracter in cuenta_str:
            actions.send_keys(caracter).pause(0.05)
        actions.perform()

        driver.execute_script("""
            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
            arguments[0].dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
        """, input_busqueda)

        # Esperar resultados de actividad
        WebDriverWait(driver, 20).until(
            EC.presence_of_all_elements_located(
                (By.XPATH, "//div[contains(@class,'activity-icon')]")
            )
        )

        # Pequeña pausa para renderizado de colores (reducida a 1s)
        time.sleep(1)

        # ─────────────────────────────────────────────
        # BUSCAR ÍCONO CON COLOR #A7D100 Y HACER CLICK
        # ─────────────────────────────────────────────
        log.info(f"🔍 Buscando ícono con color {COLOR_ORDEN_ACTIVA}...")

        click_realizado = driver.execute_script("""
            const iconos = document.querySelectorAll('div.activity-icon.icon');

            for (const icono of iconos) {
                const style = icono.getAttribute('style') || '';
                const bgInline = icono.style.backgroundColor || '';

                const esColorCorrecto =
                    style.toLowerCase().includes('#a7d100') ||
                    bgInline.toLowerCase().includes('#a7d100') ||
                    bgInline.replace(/\\s/g, '') === 'rgb(167,209,0)';

                if (!esColorCorrecto) continue;

                const contenedor = icono.closest('div.found-item-activity');
                if (!contenedor) continue;

                const titulo = contenedor.querySelector('div.activity-title');
                if (!titulo) continue;

                titulo.scrollIntoView({ block: 'center' });
                titulo.click();

                return {
                    ok: true,
                    texto: titulo.innerText.substring(0, 200),
                    ariaLabel: titulo.getAttribute('aria-label') || ''
                };
            }

            return { ok: false, totalIconos: iconos.length };
        """)

        log.info(f"📝 Resultado del click: {click_realizado}")

        if not click_realizado.get('ok'):
            total = click_realizado.get('totalIconos', 0)
            log.error(f"❌ No se encontró ícono {COLOR_ORDEN_ACTIVA} (se revisaron {total} íconos)")
            return f"❌ No se encontró orden de trabajo para {cuenta}"

        log.info(f"✅ Click realizado en: {click_realizado.get('ariaLabel', 'sin label')}")

        # ESPERAR CARGA DEL DETALLE — esperar elemento clave en lugar de sleep fijo
        log.info("⏳ Esperando carga del detalle...")
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, XP_CHECKBOX_HFC[0]))
            )
        except TimeoutException:
            pass

        # BUSCAR CHECKBOX HFC con scrollIntoView inteligente
        log.info("☑️ Buscando checkbox de Validación HFC...")

        checkbox = None
        for selector in XP_CHECKBOX_HFC:
            try:
                log.info(f"Probando selector: {selector}")
                el = WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.XPATH, selector))
                )

                if selector == XP_CHECKBOX_HFC[-1]:  # selector genérico: validar contexto
                    texto_cerca = driver.execute_script("""
                        var el = arguments[0], parent = el.parentElement;
                        for (var i = 0; i < 5; i++) {
                            if (parent && parent.innerText &&
                                parent.innerText.includes('Validacion Niveles HFC'))
                                return true;
                            parent = parent ? parent.parentElement : null;
                            if (!parent) break;
                        }
                        return false;
                    """, el)
                    if not texto_cerca:
                        continue

                checkbox = el
                log.info("✅ Checkbox encontrado")
                break

            except (TimeoutException, Exception) as e:
                log.warning(f"Falló selector: {e}")

        if not checkbox:
            _guardar_screenshot(driver, f"error_no_checkbox_{cuenta}")
            log.error("❌ No se encontró el checkbox de Validación HFC")
            return f"❌ No se encontró checkbox HFC para {cuenta}"

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", checkbox)
        # Esperar que el checkbox sea interactuable en lugar de sleep fijo
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, XP_CHECKBOX_HFC[0])))

        # ─────────────────────────────────────────────
        # VALIDAR SI YA ESTABA MARCADO
        # ─────────────────────────────────────────────
        if checkbox.is_selected():
            log.info("☑️ Checkbox HFC ya estaba marcado — cuenta previamente validada")
            return f"ℹ️ CUENTA {cuenta} YA ESTABA VALIDADA ANTERIORMENTE ✔️"

        # Marcar el checkbox
        try:
            checkbox.click()
            log.info("✅ Checkbox HFC marcado")
        except Exception:
            driver.execute_script("arguments[0].click();", checkbox)
            log.info("✅ Checkbox HFC marcado con JS")

        # BUSCAR BOTÓN OK con scrollIntoView inteligente
        log.info("🚀 Buscando botón OK...")

        boton_ok = None
        for xp in XP_BOTON_OK:
            try:
                log.info(f"Probando selector para OK: {xp}")
                el = WebDriverWait(driver, 8).until(
                    EC.element_to_be_clickable((By.XPATH, xp))
                )
                boton_ok = el
                log.info("✅ Botón OK encontrado")
                break
            except (TimeoutException, Exception) as e:
                log.warning(f"Falló selector para OK: {e}")

        if not boton_ok:
            _guardar_screenshot(driver, f"error_no_boton_ok_{cuenta}")
            log.error("❌ No se encontró botón OK")
            return f"❌ No se encontró botón OK para {cuenta}"

        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", boton_ok)
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, XP_BOTON_OK[0]))
        )

        try:
            boton_ok.click()
            log.info("✅ Click botón OK")
        except Exception:
            driver.execute_script("arguments[0].click();", boton_ok)
            log.info("✅ Click botón OK con JS")

        # Esperar confirmación visual (presencia de barra búsqueda = volvió al inicio)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, SEL_BARRA_BUSQUEDA))
            )
        except TimeoutException:
            pass

        mensaje = f"✅ CUENTA {cuenta} VALIDADA OK"
        log.info(mensaje)
        return mensaje

    except SesionExpiradaError as e:
        log.error(f"⚠️ Sesión expirada: {e}")
        cerrar_driver()
        _notificar_telegram(
            f"⚠️ *Oracle OFSC — Sesión Expirada*\n"
            f"No se pudo procesar la cuenta `{cuenta}`.\n"
            f"Ejecuta: `python oracle.py --login`"
        )
        return (
            f"⚠️ *La sesión de Oracle expiró y no se pudo renovar sola.*\n"
            f"Ejecuta `python oracle.py --login` para renovar las cookies.\n"
            f"Cuenta pendiente: {cuenta}"
        )

    except Exception as e:
        log.exception("❌ Error procesando cuenta")
        _guardar_screenshot(driver, f"error_cuenta_{cuenta}")
        return f"❌ ERROR EN CUENTA {cuenta}: {e}"

    finally:
        _buscando = False


# ─────────────────────────────────────────────────────
# MAIN / CLI
# ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]

    if "--login" in args:
        # Renovación de cookies manual — abre Edge visible
        print("🔑 Iniciando renovación manual de cookies...")
        ok = renovar_cookies_manual(timeout=300)
        print("✅ Cookies renovadas correctamente" if ok else "❌ No se pudo renovar las cookies")
        sys.exit(0 if ok else 1)

    if "--check" in args:
        # Verificar si las cookies actuales son válidas
        print("🔍 Verificando estado de cookies...")
        ok = _verificar_cookies_validas()
        print("✅ Cookies válidas — sesión activa" if ok else "❌ Cookies inválidas o expiradas")
        sys.exit(0 if ok else 1)

    # Test por defecto
    cuenta_test = "50826808"
    resultado = buscar_cuenta(cuenta_test)
    print(f"\n{'='*50}")
    print(f"RESULTADO FINAL: {resultado}")
    print(f"{'='*50}")
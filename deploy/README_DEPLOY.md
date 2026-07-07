# niveles-ofsc — Despliegue en LXC Proxmox

## Contenedor objetivo
El mismo LXC donde corre **Script-BotCCOT Funcional**.
- Ruta del BotCCOT: `/root/script_wf_umm/botUMM/`
- Ruta de este bot: `/root/niveles-ofsc/`

---

## Estructura en el LXC
```
/root/niveles-ofsc/
├── oracle.py
├── telegram_bot.py
├── requirements.txt
├── .env                   ← credenciales (NO subir a Git)
├── oracle_cookies.json    ← sesión persistida (se genera en --login)
├── edge_perfil/           ← perfil Edge persistido
└── deploy/
    ├── install_lxc.sh     ← instalación inicial
    ├── update_lxc.sh      ← actualización incremental
    ├── login_lxc.sh       ← renovar cookies desde el LXC
    └── README_DEPLOY.md

/var/log/niveles-ofsc/
└── bot.log                ← logs del servicio
```

---

## Instalación inicial (primera vez)

### 1. Subir el código a GitHub (desde tu PC)
```bash
cd C:\Users\juane\sigotc\niveles-ofsc-main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/juanesgit/niveles-ofsc.git
git push -u origin main
```

### 2. Ejecutar el instalador desde el LXC
Conectarse al LXC (desde Proxmox o SSH) y ejecutar:
```bash
# Descarga y ejecuta el instalador directamente desde GitHub:
bash <(curl -fsSL https://raw.githubusercontent.com/juanesgit/niveles-ofsc/main/deploy/install_lxc.sh)

# O si prefieres clonar primero manualmente:
git clone https://github.com/juanesgit/niveles-ofsc.git /root/niveles-ofsc
bash /root/niveles-ofsc/deploy/install_lxc.sh
```

El instalador hace `git clone` automáticamente si el directorio no existe,
o `git pull` si ya existe.

El instalador:
- Instala dependencias del sistema (Python 3, librerías X11, Xvfb)
- Instala **Microsoft Edge Stable** para Linux
- Crea el entorno virtual en `.venv/`
- Instala los paquetes de `requirements.txt`
- Crea el servicio **systemd** `niveles-ofsc`
- Crea el servicio **systemd** `xvfb-niveles` (display virtual para Edge headless)

### 3. Configurar `.env`
```bash
nano /root/niveles-ofsc/.env
```
Asegúrate de tener al menos:
```env
ORACLE_URL=https://amx-res-co.fs.ocs.oraclecloud.com/
ORACLE_USUARIO=38101491@claro.com.co
TELEGRAM_TOKEN=<tu_token>
ALLOWED_CHAT_ID=<tu_chat_id>
KEEPALIVE_SEGUNDOS=240
PERMITIR_SSO_SILENCIOSO_WINDOWS=false
```
> ⚠️ En Linux cambia `PERMITIR_SSO_SILENCIOSO_WINDOWS=false` porque no hay dominio Windows en el LXC.

### 4. Login inicial (primera vez / cookies expiradas)

El LXC no tiene pantalla física. Necesitas ver la ventana de Edge con alguna de estas opciones:

**Opción A — SSH con X11 forwarding (recomendado):**
```bash
# Desde tu PC Windows (con Xming o VcXsrv instalado y corriendo):
ssh -X root@<IP_LXC>
bash /root/niveles-ofsc/deploy/login_lxc.sh
```

**Opción B — noVNC desde la consola de Proxmox:**
1. Abre la consola del contenedor en Proxmox web UI
2. Ejecuta:
```bash
bash /root/niveles-ofsc/deploy/login_lxc.sh
```

En la ventana de Edge que se abrirá:
1. Resuelve el MFA de Microsoft
2. El script detecta automáticamente cuando entras a Oracle
3. Guarda las cookies y reinicia el bot

### 5. Arrancar el bot
```bash
systemctl start xvfb-niveles
systemctl start niveles-ofsc

# Verificar que corra:
systemctl status niveles-ofsc
journalctl -u niveles-ofsc -f
```

---

## Actualización (cuando cambias código)

**Desde tu PC** — haz push a GitHub:
```bash
git add .
git commit -m "descripcion del cambio"
git push
```

**Desde el LXC** — descarga y aplica:
```bash
bash /root/niveles-ofsc/deploy/update_lxc.sh
```
Esto hace `git pull`, actualiza las dependencias y reinicia el servicio.

---

## Comandos útiles

```bash
# Estado del servicio
systemctl status niveles-ofsc

# Logs en tiempo real
journalctl -u niveles-ofsc -f
tail -f /var/log/niveles-ofsc/bot.log

# Reiniciar
systemctl restart niveles-ofsc

# Verificar cookies desde CLI
cd /root/niveles-ofsc
.venv/bin/python oracle.py --check

# Renovar cookies desde CLI
bash /root/niveles-ofsc/deploy/login_lxc.sh

# Renovar cookies desde Telegram
/verificar    ← verifica si la sesión está activa
/renovar      ← inicia renovación (requiere ver la ventana de Edge)
```

---

## Coexistencia con BotCCOT

| | BotCCOT | niveles-ofsc |
|---|---|---|
| **Ruta** | `/root/script_wf_umm/botUMM/` | `/root/niveles-ofsc/` |
| **Venv** | `/root/script_wf_umm/botUMM/.venv/` | `/root/niveles-ofsc/.venv/` |
| **Tipo** | Jobs puntuales por cron | Proceso continuo (systemd) |
| **Browser** | Chrome headless | Edge headless |
| **Servicio** | *(sin systemd, solo cron)* | `niveles-ofsc.service` |
| **Logs** | `/var/log/job1_ofsc.log` | `/var/log/niveles-ofsc/bot.log` |

Ambos coexisten sin conflicto: usan browsers, perfiles y puertos distintos.

---

## Solución de problemas

**El bot no arranca:**
```bash
journalctl -u niveles-ofsc -n 50 --no-pager
```

**Edge no abre / error de display:**
```bash
systemctl status xvfb-niveles
# Si falla:
systemctl restart xvfb-niveles
systemctl restart niveles-ofsc
```

**Cookies expiradas — el bot avisa por Telegram:**
```bash
bash /root/niveles-ofsc/deploy/login_lxc.sh
```

**Verificar que Edge esté instalado:**
```bash
microsoft-edge-stable --version
```

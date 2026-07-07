#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  niveles-ofsc — Instalador LXC (via Git)
#  Clona el repositorio y configura el bot en /root/niveles-ofsc/
#  sobre el mismo contenedor LXC que Script-BotCCOT Funcional.
#
#  Uso (ejecutar como root dentro del LXC):
#    bash install_lxc.sh [GIT_REPO_URL]
#
#  Ejemplo:
#    bash install_lxc.sh https://github.com/juanesgit/niveles-ofsc.git
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Configuración ─────────────────────────────────────────────────
GIT_REPO="${1:-https://github.com/juanesgit/niveles-ofsc.git}"  # URL real del repo
INSTALL_DIR="/root/niveles-ofsc"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="niveles-ofsc"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_DIR="/var/log/niveles-ofsc"
EDGE_PROFILE_DIR="$INSTALL_DIR/edge_perfil"

# ── Colores ───────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Verificar root ────────────────────────────────────────────────
[[ "$EUID" -ne 0 ]] && error "Ejecuta este script como root."

info "════════════════════════════════════════"
info " Instalando niveles-ofsc en el LXC"
info " Destino: $INSTALL_DIR"
info "════════════════════════════════════════"

# ── 1. Dependencias del sistema ───────────────────────────────────
info "[1/7] Instalando dependencias del sistema..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    wget curl unzip gnupg ca-certificates \
    xvfb libglib2.0-0 libnss3 libx11-6 libx11-xcb1 \
    libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
    libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 \
    libxss1 libxtst6 fonts-liberation libappindicator3-1 \
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 \
    libdbus-1-3 libdrm2 libgbm1 libgtk-3-0

# ── 2. Microsoft Edge en Linux ────────────────────────────────────
info "[2/7] Verificando Microsoft Edge..."
if ! command -v microsoft-edge-stable &>/dev/null; then
    info "  Instalando Microsoft Edge Stable..."
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-edge.gpg
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-edge.gpg] \
https://packages.microsoft.com/repos/edge stable main" \
        > /etc/apt/sources.list.d/microsoft-edge.list
    apt-get update -qq
    apt-get install -y -qq microsoft-edge-stable
else
    info "  Microsoft Edge ya está instalado: $(microsoft-edge-stable --version 2>/dev/null || echo 'versión desconocida')"
fi

# ── 3. Clonar o actualizar el repositorio Git ────────────────────
info "[3/7] Clonando repositorio desde Git..."
mkdir -p "$LOG_DIR" "$EDGE_PROFILE_DIR"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "  Repositorio ya existe — ejecutando git pull..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "  Clonando: $GIT_REPO → $INSTALL_DIR"
    git clone "$GIT_REPO" "$INSTALL_DIR"
fi

mkdir -p "$EDGE_PROFILE_DIR"

# Crear .env si no existe
if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    warn "  No se encontró .env — crea $INSTALL_DIR/.env antes de iniciar el bot"
    warn "  Puedes usar como base: $INSTALL_DIR/.env.example"
else
    info "  .env ya existe"
fi

# ── 4. Entorno virtual Python ─────────────────────────────────────
info "[4/7] Creando entorno virtual Python..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
info "  Dependencias instaladas en $VENV_DIR"

# ── 5. Systemd service ────────────────────────────────────────────
info "[5/7] Creando servicio systemd: $SERVICE_NAME..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Niveles OFSC — Bot Telegram Oracle
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$VENV_DIR/bin/python -u telegram_bot.py
Restart=always
RestartSec=10
StandardOutput=append:$LOG_DIR/bot.log
StandardError=append:$LOG_DIR/bot.log
# Evitar que Edge headless se queje de display en LXC
Environment=DISPLAY=:99

[Install]
WantedBy=multi-user.target
EOF

# ── 6. Servicio Xvfb (display virtual para Edge headless en LXC) ──
info "[6/7] Creando servicio Xvfb (display virtual)..."
cat > "/etc/systemd/system/xvfb-niveles.service" <<EOF
[Unit]
Description=Xvfb display virtual para niveles-ofsc
Before=niveles-ofsc.service

[Service]
Type=simple
ExecStart=/usr/bin/Xvfb :99 -screen 0 1920x1080x24
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ── 7. Habilitar y arrancar ───────────────────────────────────────
info "[7/7] Habilitando servicios..."
systemctl daemon-reload
systemctl enable xvfb-niveles.service
systemctl enable "$SERVICE_NAME.service"

echo ""
info "════════════════════════════════════════════════════════"
info " Instalación completada."
info ""
warn " ANTES de iniciar el bot:"
warn "   1. Edita $INSTALL_DIR/.env con tus credenciales reales"
warn "   2. Si nunca has hecho login, ejecuta primero:"
warn "      cd $INSTALL_DIR && $VENV_DIR/bin/python oracle.py --login"
warn "      (abre Edge visible — necesitas acceso X11/VNC al LXC)"
info ""
info " Para arrancar el bot:"
info "   systemctl start xvfb-niveles"
info "   systemctl start $SERVICE_NAME"
info ""
info " Para ver los logs:"
info "   journalctl -u $SERVICE_NAME -f"
info "   tail -f $LOG_DIR/bot.log"
info "════════════════════════════════════════════════════════"

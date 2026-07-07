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
GIT_REPO="${1:-https://github.com/juanesgit/niveles-ofsc.git}"
INSTALL_DIR="/root/niveles-ofsc"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="niveles-ofsc"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOG_DIR="/var/log/niveles-ofsc"

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
info "[1/6] Instalando dependencias del sistema..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-pip python3-venv git \
    wget curl unzip gnupg ca-certificates

# ── 2. Google Chrome (igual que BotCCOT) ─────────────────────────
info "[2/6] Verificando Google Chrome..."
if ! command -v google-chrome-stable &>/dev/null && ! command -v google-chrome &>/dev/null; then
    info "  Instalando Google Chrome Stable..."
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list
    apt-get update -qq
    apt-get install -y -qq google-chrome-stable
else
    CHROME_VER=$(google-chrome-stable --version 2>/dev/null || google-chrome --version 2>/dev/null || echo 'versión desconocida')
    info "  Google Chrome ya instalado: $CHROME_VER"
fi

# ── 3. Clonar o actualizar el repositorio Git ─────────────────────
info "[3/6] Clonando repositorio desde Git..."
mkdir -p "$LOG_DIR"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "  Repositorio ya existe — ejecutando git pull..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "  Clonando: $GIT_REPO → $INSTALL_DIR"
    git clone "$GIT_REPO" "$INSTALL_DIR"
fi

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
    warn "  No se encontró .env — créalo antes de iniciar el bot"
    warn "  Usa como base: $INSTALL_DIR/.env.example"
else
    info "  .env ya existe"
fi

# ── 4. Entorno virtual Python ─────────────────────────────────────
info "[4/6] Creando entorno virtual Python..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q
info "  Dependencias instaladas en $VENV_DIR"

# ── 5. Systemd service ────────────────────────────────────────────
info "[5/6] Creando servicio systemd: $SERVICE_NAME..."
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

[Install]
WantedBy=multi-user.target
EOF

# ── 6. Habilitar servicio ─────────────────────────────────────────
info "[6/6] Habilitando servicio systemd..."
systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service"

echo ""
info "════════════════════════════════════════════════════════"
info " Instalación completada."
info ""
warn " PRÓXIMOS PASOS:"
warn "   1. Configura el .env:"
warn "      cp $INSTALL_DIR/.env.example $INSTALL_DIR/.env"
warn "      nano $INSTALL_DIR/.env"
warn "   2. Login inicial (renueva cookies):"
warn "      bash $INSTALL_DIR/deploy/login_lxc.sh"
warn "   3. Inicia el bot:"
warn "      systemctl start $SERVICE_NAME"
info ""
info " Logs:"
info "   journalctl -u $SERVICE_NAME -f"
info "   tail -f $LOG_DIR/bot.log"
info "════════════════════════════════════════════════════════"

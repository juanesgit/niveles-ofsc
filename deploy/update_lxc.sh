#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  niveles-ofsc — Actualizador LXC (via Git)
#  Hace git pull, actualiza dependencias y reinicia el servicio.
#  Igual que el patrón de Script-BotCCOT Funcional.
#
#  Uso (ejecutar como root dentro del LXC):
#    bash /root/niveles-ofsc/deploy/update_lxc.sh
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/root/niveles-ofsc"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="niveles-ofsc"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

info "Deteniendo servicio $SERVICE_NAME..."
systemctl stop "$SERVICE_NAME" || true

info "Actualizando código desde Git..."
git -C "$INSTALL_DIR" pull --ff-only
info "  Commit actual: $(git -C "$INSTALL_DIR" log -1 --oneline)"

info "Actualizando dependencias Python..."
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -q

info "Reiniciando servicio $SERVICE_NAME..."
systemctl start "$SERVICE_NAME"
sleep 2
systemctl status "$SERVICE_NAME" --no-pager -l

info "Actualización completada."
warn "Logs: journalctl -u $SERVICE_NAME -f"

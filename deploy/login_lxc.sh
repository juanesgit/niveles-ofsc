#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  niveles-ofsc — Login manual de cookies en el LXC
#
#  En el LXC usa Chrome con Selenium Manager (headless=false).
#  Para ver la ventana de Chrome necesitas acceso gráfico:
#    - SSH con X11 forwarding:  ssh -X root@<ip-lxc>
#    - noVNC / consola Proxmox
#
#  Uso:
#    bash /root/niveles-ofsc/deploy/login_lxc.sh
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/root/niveles-ofsc"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="niveles-ofsc"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

info "Deteniendo bot temporalmente..."
systemctl stop "$SERVICE_NAME" || true
sleep 2

info "Iniciando renovación de cookies con Chrome visible..."
warn "Necesitas ver la ventana de Chrome. Opciones:"
warn "  - SSH con X11:   ssh -X root@<ip-lxc>  y luego ejecuta este script"
warn "  - noVNC/Proxmox: abre la consola del LXC y ejecuta este script"
echo ""

cd "$INSTALL_DIR"
"$VENV_DIR/bin/python" oracle.py --login
RC=$?

if [[ $RC -eq 0 ]]; then
    info "Cookies renovadas correctamente."
    info "Reiniciando bot..."
    systemctl start "$SERVICE_NAME"
    sleep 2
    systemctl status "$SERVICE_NAME" --no-pager -l
else
    warn "La renovación falló (rc=$RC). Revisa los logs:"
    warn "  journalctl -u $SERVICE_NAME -n 30 --no-pager"
fi

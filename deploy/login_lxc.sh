#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  niveles-ofsc — Login manual de cookies en el LXC
#  Ejecuta oracle.py --login con display virtual (Xvfb).
#  Requiere que el LXC tenga acceso X11/VNC para ver la ventana
#  de Edge, o bien que uses SSH con X11 forwarding:
#    ssh -X root@<ip-lxc>
#    bash /root/niveles-ofsc/deploy/login_lxc.sh
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/root/niveles-ofsc"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="niveles-ofsc"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# Detener el bot para liberar el perfil de Edge
info "Deteniendo bot temporalmente para liberar perfil de Edge..."
systemctl stop "$SERVICE_NAME" || true
sleep 2

# Asegurar que Xvfb esté corriendo
if ! pgrep -x Xvfb &>/dev/null; then
    info "Iniciando Xvfb en :99..."
    Xvfb :99 -screen 0 1920x1080x24 &
    sleep 1
fi

export DISPLAY=:99

info "Iniciando renovación manual de cookies..."
warn "Necesitas ver la ventana de Edge. Opciones:"
warn "  - SSH con X11 forwarding: ssh -X root@<ip-lxc>"
warn "  - VNC conectado al LXC"
warn "  - noVNC desde Proxmox"
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
    warn "La renovación de cookies falló (rc=$RC)."
    warn "El bot NO se reiniciará. Revisa los logs y vuelve a intentarlo."
fi

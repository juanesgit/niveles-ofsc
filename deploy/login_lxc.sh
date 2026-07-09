#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  niveles-ofsc — Login manual de cookies en el LXC (Xvfb + VNC)
#
#  Usa el Xvfb persistente del servicio niveles-ofsc-xvfb.
#  Inicia fluxbox + x11vnc para que el usuario pueda hacer MFA.
#  Al finalizar, Chrome sigue corriendo y el bot se conecta al mismo
#  proceso via remote debugging.
#
#  Uso:
#    bash /root/niveles-ofsc/deploy/login_lxc.sh
# ══════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/root/niveles-ofsc"
VENV_DIR="$INSTALL_DIR/.venv"
SERVICE_NAME="niveles-ofsc"
DISPLAY_NUM=99
DISPLAY=":$DISPLAY_NUM"
VNC_PORT=5900

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

info "Deteniendo bot..."
systemctl stop "$SERVICE_NAME" || true
sleep 2

info "Asegurando Xvfb persistente..."
systemctl start niveles-ofsc-xvfb || true
sleep 2

info "Iniciando fluxbox (window manager)..."
DISPLAY="$DISPLAY" fluxbox &
FLUXBOX_PID=$!
sleep 2

info "Iniciando x11vnc (puerto $VNC_PORT)..."
x11vnc -display "$DISPLAY" -rfbport "$VNC_PORT" -forever -shared -nopw &
VNC_PID=$!
sleep 2

info "════════════════════════════════════════════════════════"
info "  VNC SERVER ACTIVO"
info "  Conectate desde tu PC:"
warn "    vncviewer <IP-LXC>:$VNC_PORT"
info "  IPs disponibles:"
hostname -I | tr ' ' '\n' | while read ip; do
    [[ -n "$ip" ]] && warn "    - $ip:$VNC_PORT"
done
info "════════════════════════════════════════════════════════"
warn "  Presiona Ctrl+C en este script cuando termines el login"
echo ""

cd "$INSTALL_DIR"
DISPLAY="$DISPLAY" "$VENV_DIR/bin/python" oracle.py --login
RC=$?

# Limpieza: solo fluxbox y x11vnc. Xvfb y Chrome deben seguir corriendo.
info "Deteniendo fluxbox y x11vnc (Xvfb y Chrome siguen corriendo)..."
kill $FLUXBOX_PID $VNC_PID 2>/dev/null || true
pkill -f "x11vnc.*$VNC_PORT" || true
pkill -f "fluxbox" || true

if [[ $RC -eq 0 ]]; then
    info "Cookies renovadas correctamente."
    info "Iniciando bot (se conectará al Chrome existente)..."
    systemctl start "$SERVICE_NAME"
    sleep 2
    systemctl status "$SERVICE_NAME" --no-pager -l
else
    warn "La renovación falló (rc=$RC). Revisa los logs:"
    warn "  journalctl -u $SERVICE_NAME -n 30 --no-pager"
fi

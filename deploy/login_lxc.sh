#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════
#  niveles-ofsc — Login manual de cookies en el LXC (Xvfb + VNC)
#
#  Inicia Xvfb (display virtual), fluxbox (window manager) y x11vnc.
#  Conectate desde tu PC via VNC: vncviewer <ip-lxc>:5900
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

# Verificar que no esté corriendo
if pgrep -f "Xvfb.*$DISPLAY_NUM" >/dev/null; then
    warn "Xvfb ya está corriendo en display $DISPLAY"
    warn "Deteniendo procesos existentes..."
    pkill -f "Xvfb.*$DISPLAY_NUM" || true
    pkill -f "x11vnc.*$VNC_PORT" || true
    pkill -f "fluxbox" || true
    sleep 2
fi

info "Deteniendo bot y Xvfb temporalmente..."
systemctl stop "$SERVICE_NAME" || true
systemctl stop niveles-ofsc-xvfb || true
sleep 2

info "Iniciando Xvfb (display virtual)..."
Xvfb "$DISPLAY" -screen 0 1920x1080x24 &
XVFB_PID=$!
sleep 2

info "Iniciando fluxbox (window manager)..."
DISPLAY="$DISPLAY" fluxbox &
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

# Limpieza
info "Deteniendo Xvfb, fluxbox y x11vnc..."
kill $XVFB_PID $VNC_PID 2>/dev/null || true
pkill -f "Xvfb.*$DISPLAY_NUM" || true
pkill -f "x11vnc.*$VNC_PORT" || true
pkill -f "fluxbox" || true

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

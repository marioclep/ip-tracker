#!/bin/bash
# Comandos de administracion de una instalacion de IP-Tracker.
#
# Uso (como root):
#   sudo bash /opt/ip-tracker/admin.sh reset-password   # genera una contraseña nueva
#   sudo bash /opt/ip-tracker/admin.sh init-admin       # solo si todavia no hay contraseña

set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${ENV_FILE:-/etc/ip-tracker.env}"
SERVICE_USER="iptracker"

if [[ $EUID -ne 0 ]]; then
    echo "Este script debe ejecutarse como root (sudo bash $0 $*)" >&2
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    echo "No existe $ENV_FILE: primero hay que correr install.sh" >&2
    exit 1
fi

set -a
. "$ENV_FILE"
set +a

cd "$INSTALL_DIR"
exec runuser -u "$SERVICE_USER" -- env SECRET_KEY="$SECRET_KEY" DATABASE_URL="$DATABASE_URL" \
    "$INSTALL_DIR/.venv/bin/python" app.py "$@"

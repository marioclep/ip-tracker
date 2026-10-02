#!/bin/bash
# Instala IP Tracker como servicio systemd en Ubuntu/Debian.
# Ejecutar como root: sudo bash install.sh

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Este script debe ejecutarse como root (sudo bash install.sh)"

command -v python3 &>/dev/null || error "Python3 no encontrado. Instalalo con: apt install python3 python3-venv"
PY_VER=$(python3 -c "import sys; print(sys.version_info.minor)")
[[ $PY_VER -lt 9 ]] && error "Se requiere Python 3.9+. Version encontrada: 3.$PY_VER"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/ip-tracker}"
SERVICE_USER="iptracker"
SERVICE_FILE="/etc/systemd/system/ip-tracker.service"
ENV_FILE="/etc/ip-tracker.env"
BACKUP_DIR="/var/backups/ip-tracker"

info "Directorio fuente: $SCRIPT_DIR"
info "Directorio destino: $INSTALL_DIR"

if ! id "$SERVICE_USER" &>/dev/null; then
    info "Creando usuario de sistema '$SERVICE_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    info "Usuario '$SERVICE_USER' ya existe."
fi

info "Copiando archivos a $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/instance"
rsync -a --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
         --exclude='.DS_Store' --exclude='ip_tracker.db' \
         "$SCRIPT_DIR/" "$INSTALL_DIR/"

info "Creando entorno virtual..."
python3 -m venv "$INSTALL_DIR/.venv"
info "Instalando dependencias..."
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

# La configuracion se crea una sola vez: si cambiara la SECRET_KEY, las
# contraseñas de los routers guardadas en la base quedarian ilegibles.
if [[ -f "$ENV_FILE" ]]; then
    info "Se conserva la configuracion existente en $ENV_FILE."
else
    # Instalaciones anteriores guardaban la clave en la unidad systemd: reutilizarla.
    SECRET_KEY=""
    DATABASE_URL=""
    if [[ -f "$SERVICE_FILE" ]]; then
        SECRET_KEY=$(sed -n 's/^Environment=SECRET_KEY=//p' "$SERVICE_FILE")
        DATABASE_URL=$(sed -n 's/^Environment=DATABASE_URL=//p' "$SERVICE_FILE")
        [[ "$SECRET_KEY" == "CHANGE_THIS_SECRET_KEY" ]] && SECRET_KEY=""
    fi
    if [[ -n "$SECRET_KEY" ]]; then
        info "Se reutiliza la SECRET_KEY de la instalacion anterior."
    else
        SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        info "SECRET_KEY generada."
    fi
    DATABASE_URL="${DATABASE_URL:-sqlite:///$INSTALL_DIR/ip_tracker.db}"
    (umask 077; printf 'SECRET_KEY=%s\nDATABASE_URL=%s\n' "$SECRET_KEY" "$DATABASE_URL" > "$ENV_FILE")
    info "Configuracion guardada en $ENV_FILE."
fi

info "Instalando servicio systemd y backup diario..."
for unit in ip-tracker.service ip-tracker-backup.service ip-tracker-backup.timer; do
    cp "$SCRIPT_DIR/$unit" "/etc/systemd/system/$unit"
    sed -i "s|/opt/ip-tracker|$INSTALL_DIR|g" "/etc/systemd/system/$unit"
done
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 700 "$BACKUP_DIR"

info "Preparando base de datos y usuario administrador..."
ADMIN_MSG=$(ENV_FILE="$ENV_FILE" bash "$INSTALL_DIR/admin.sh" init-admin 2>/dev/null) \
    || ADMIN_MSG="No se pudo preparar el administrador: probar 'sudo bash $INSTALL_DIR/admin.sh init-admin'"

systemctl daemon-reload
systemctl enable ip-tracker
systemctl restart ip-tracker
systemctl enable --now ip-tracker-backup.timer

info "Instalacion completada."
echo ""
info "IP Tracker esta corriendo en http://$(hostname -I | awk '{print $1}'):5001"
info "Backup diario a las 03:00 en $BACKUP_DIR (se guardan 14 dias)."
info "$ADMIN_MSG"
if [[ "$ADMIN_MSG" == Contraseña* ]]; then
    warn "Guarda esta contraseña: no se vuelve a mostrar. Podes cambiarla en Configuracion."
fi

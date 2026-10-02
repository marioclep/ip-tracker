#!/bin/bash
# Bootstrap script para IP-Tracker.
# El usuario sube los archivos sueltos a /tmp/ del servidor.
# Este script los organiza, instala dependencias del sistema y ejecuta install.sh.
#
# Uso en el servidor (como root):
#   sudo bash /tmp/bootstrap.sh

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[BOOTSTRAP]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[BOOTSTRAP]${NC} $*"; }
error() { echo -e "${RED}[BOOTSTRAP]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Este script debe ejecutarse como root (sudo bash /tmp/bootstrap.sh)"

TMP_DIR="/tmp"
INSTALL_DIR="/opt/ip-tracker"

# --- Lista explicita de archivos y directorios a copiar ------------------------
FILES=(
    "app.py"
    "models.py"
    "mikrotik.py"
    "requirements.txt"
    "install.sh"
    "admin.sh"
    "ip-tracker.service"
    "conftest.py"
    ".gitignore"
)

DIRS=(
    "templates"
    "tests"
)

# --- Verificar archivos fuente en /tmp ---------------------------------------
info "Verificando archivos fuente en $TMP_DIR..."
for f in "${FILES[@]}"; do
    if [[ ! -f "$TMP_DIR/$f" ]]; then
        error "Archivo requerido no encontrado: $TMP_DIR/$f"
    fi
done
for d in "${DIRS[@]}"; do
    if [[ ! -d "$TMP_DIR/$d" ]]; then
        error "Directorio requerido no encontrado: $TMP_DIR/$d"
    fi
done
info "Todos los archivos y directorios requeridos encontrados."

# --- Instalar dependencias del sistema ---------------------------------------
info "Actualizando paquetes del sistema..."
apt-get update -qq

info "Instalando dependencias del sistema (python3, venv, pip)..."
apt-get install -y -qq python3 python3-venv python3-pip git rsync

# Verificar Python 3.9+
PY_VER=$(python3 -c "import sys; print(sys.version_info.minor)")
[[ $PY_VER -lt 9 ]] && error "Se requiere Python 3.9+. Version encontrada: 3.$PY_VER"
info "Python 3.$PY_VER detectado."

# --- Preparar directorio de instalacion --------------------------------------
info "Preparando directorio de instalacion: $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# Copiar archivos individuales
info "Copiando archivos del proyecto..."
for f in "${FILES[@]}"; do
    cp "$TMP_DIR/$f" "$INSTALL_DIR/"
done

# Copiar directorios recursivamente
info "Copiando directorios del proyecto..."
for d in "${DIRS[@]}"; do
    cp -r "$TMP_DIR/$d" "$INSTALL_DIR/"
done

# Asegurarse de que install.sh sea ejecutable
chmod +x "$INSTALL_DIR/install.sh"

info "Archivos copiados correctamente."

# --- Ejecutar install.sh -----------------------------------------------------
info "Ejecutando install.sh..."
cd "$INSTALL_DIR"
bash "$INSTALL_DIR/install.sh"

# --- Limpiar /tmp ------------------------------------------------------------
info "Limpiando archivos temporales del proyecto en /tmp..."
for f in "${FILES[@]}"; do
    rm -f "$TMP_DIR/$f" 2>/dev/null || true
done
for d in "${DIRS[@]}"; do
    rm -rf "$TMP_DIR/$d" 2>/dev/null || true
done
rm -f "$TMP_DIR/bootstrap.sh" 2>/dev/null || true
rm -rf "$TMP_DIR/.pytest_cache" 2>/dev/null || true

info "Bootstrap completado. IP-Tracker esta corriendo."

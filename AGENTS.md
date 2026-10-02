# IP-Tracker — guía para asistentes de IA

Este archivo es para un asistente de IA (Claude Code, Codex, Cursor, Copilot,
Gemini, etc.) que ayuda a **instalar, actualizar, diagnosticar o modificar**
IP-Tracker. Lo que el sistema hace para el usuario final está en
[`README.md`](README.md) y [`MANUAL.md`](MANUAL.md).

## Qué es

Aplicación web (Flask + SQLite) que cada pocos minutos consulta routers
MikroTik por su API (puerto 8728) y guarda el historial de leases DHCPv4,
sesiones PPPoE y bindings DHCPv6. Sirve para responder "¿quién tenía esta IP
en tal fecha?".

- **Solo lee los routers.** Nunca escribe ni cambia su configuración.
- Un solo proceso: la web y el sondeo corren juntos en `app.py`.
- Escucha en el puerto **5001**, en todas las interfaces.

## Reglas para el asistente

- **Nunca cambiar ni regenerar `SECRET_KEY`** en `/etc/ip-tracker.env`.
  Con ella se cifran las contraseñas de los routers guardadas en la base: si
  cambia, quedan ilegibles y hay que volver a cargarlas todas.
- **Nunca borrar `/etc/ip-tracker.env`** ni la base (`/opt/ip-tracker/ip_tracker.db`).
- **Antes de actualizar, hacer un backup** (`sudo systemctl start ip-tracker-backup`).
- **No correr dos instancias** contra la misma base (por ejemplo, el servicio
  y un `python app.py` a mano): los dos sondean los routers.
- **No exponer el puerto 5001 a internet.** Limitarlo a la red de gestión
  (firewall del servidor o del router).
- **No modificar la configuración de los routers** más allá de lo indicado en
  "Preparar cada MikroTik", y siempre mostrándole al usuario el comando antes.
- Las contraseñas que muestran `install.sh` y `admin.sh` aparecen **una sola
  vez**: pasárselas al usuario y recomendarle cambiarlas en Configuración.
- El usuario habla en español: responder en español.

## Instalación

### 1. Verificar requisitos

| Requisito | Cómo comprobarlo |
|---|---|
| Ubuntu 22.04+ o Debian 12+ con systemd | `cat /etc/os-release` y `systemctl --version` |
| Acceso root | `sudo -v` |
| Python 3.9 o superior | `python3 --version` |
| git y rsync | `git --version` y `rsync --version` (si faltan: `sudo apt-get install -y git rsync`) |
| Puerto 5001 libre | `sudo ss -ltnp \| grep :5001` no debe mostrar nada |
| El servidor llega a cada router por el puerto 8728 | `nc -zv <IP_DEL_ROUTER> 8728` |

### 2. Instalar

```bash
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip git rsync
sudo git clone https://github.com/marioclep/ip-tracker.git /opt/ip-tracker
sudo bash /opt/ip-tracker/install.sh
```

`install.sh`:

1. crea el usuario de sistema `iptracker`;
2. crea el entorno virtual en `/opt/ip-tracker/.venv` e instala las dependencias;
3. la primera vez, crea `/etc/ip-tracker.env` (permisos 600) con `SECRET_KEY`
   y `DATABASE_URL`; si ya existe, no lo toca;
4. crea la base y el usuario `admin` con una contraseña al azar;
5. instala y arranca el servicio `ip-tracker`;
6. activa el backup diario (`ip-tracker-backup.timer`, a las 03:00, 14 días
   en `/var/backups/ip-tracker/`).

Al final muestra una línea así, que hay que pasarle al usuario:

```
Contraseña del usuario 'admin': <contraseña>
```

### 3. Verificar

```bash
systemctl is-active ip-tracker                            # active
sudo journalctl -u ip-tracker -n 30 --no-pager            # sin errores
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5001/login   # 200
```

Después, entrar a `http://<IP_DEL_SERVIDOR>:5001` con `admin` y la contraseña
mostrada.

## Preparar cada MikroTik

IP-Tracker usa la API clásica de RouterOS **sin SSL** (puerto 8728; `api-ssl`
en 8729 no está soportado) y lee `/ip/dhcp-server/lease`, `/ppp/active`,
`/ipv6/dhcp-server/binding` y `/system/identity`. Necesita un usuario con
permisos solo de lectura y de API.

Antes de cambiar nada, mirar cómo está el servicio API:

```
/ip service print where name=api
```

Si el servicio `api` ya tiene direcciones permitidas (`address`), **agregar**
la IP del servidor a la lista existente en lugar de reemplazarla: otros
sistemas pueden estar usándolo.

```
/user group add name=iptracker policy=read,api
/user add name=iptracker group=iptracker password=<CONTRASEÑA> address=<IP_DEL_SERVIDOR>/32
/ip service set api disabled=no port=8728 address=<IP_DEL_SERVIDOR>/32
```

Después, en la web: **Routers → Agregar router** (host, puerto 8728, usuario y
contraseña) y **Probar conexión**, en el mismo formulario, para confirmarla. El primer sondeo llega en
el siguiente ciclo (5 minutos por defecto, se cambia en Configuración).

## Operación

| Tarea | Comando |
|---|---|
| Estado | `systemctl status ip-tracker` |
| Logs | `sudo journalctl -u ip-tracker -f` |
| Reiniciar | `sudo systemctl restart ip-tracker` |
| Nueva contraseña de admin | `sudo bash /opt/ip-tracker/admin.sh reset-password` |
| Actualizar | `cd /opt/ip-tracker && sudo git -c safe.directory=/opt/ip-tracker pull origin main && sudo bash install.sh` |
| Backup inmediato | `sudo systemctl start ip-tracker-backup` (queda en `/var/backups/ip-tracker/`) |
| Ver los backups | `systemctl list-timers ip-tracker-backup.timer` y `sudo ls -l /var/backups/ip-tracker/` |

### Backups

Para restaurar una copia:

```bash
sudo systemctl stop ip-tracker
sudo cp /opt/ip-tracker/ip_tracker.db /opt/ip-tracker/ip_tracker.db.antes-de-restaurar
sudo install -o iptracker -g iptracker -m 644 /var/backups/ip-tracker/ip_tracker-AAAAMMDD-HHMMSS.db /opt/ip-tracker/ip_tracker.db
sudo systemctl start ip-tracker
```

Antes de actualizar, hacer un backup inmediato.

El `safe.directory` hace falta porque la carpeta es del usuario `iptracker` y
git rechaza operar como root sobre ella ("dubious ownership").

**Instalaciones sin git** (hechas con `bootstrap.sh`, sin `/opt/ip-tracker/.git`):
clonar en una carpeta temporal y correr su `install.sh`. Copia el repo
(incluido `.git`) sobre `/opt/ip-tracker` sin tocar la base, así que las
siguientes actualizaciones ya son con `git pull`:

```bash
sudo systemctl stop ip-tracker
sudo cp /opt/ip-tracker/ip_tracker.db /root/ip_tracker-antes-de-actualizar.db
git clone https://github.com/marioclep/ip-tracker.git /tmp/ip-tracker
sudo bash /tmp/ip-tracker/install.sh && rm -rf /tmp/ip-tracker
```

Actualizar con `install.sh` conserva `/etc/ip-tracker.env`, la base y la
contraseña. En instalaciones anteriores a este archivo, que guardaban la
`SECRET_KEY` dentro de la unidad systemd, la reutiliza.

## Problemas comunes

| Síntoma | Causa probable y qué hacer |
|---|---|
| El servicio no arranca y el log dice `SECRET_KEY is not set` | Falta o está vacío `/etc/ip-tracker.env`. Si existía una clave anterior, recuperarla (por ejemplo, de la unidad systemd vieja o de un backup); generar una nueva deja ilegibles las contraseñas de los routers ya cargados. |
| "Probar" falla con timeout | Firewall, servicio `api` deshabilitado o `address` del servicio que no incluye al servidor. Verificar con `nc -zv <IP> 8728`. |
| "Probar" falla con error de login | Usuario o contraseña del router, o el `address` del usuario en RouterOS no incluye la IP del servidor. |
| Un router dejó de sondearse después de cambiar la `SECRET_KEY` | Volver a cargar la contraseña del router en la web. |
| Nadie recuerda la contraseña de la web | `sudo bash /opt/ip-tracker/admin.sh reset-password` |
| `git pull` dice "dubious ownership" | Usar `git -c safe.directory=/opt/ip-tracker pull`. |

### Problemas conocidos

- Los intentos fallidos de login se cuentan en memoria: se reinician al
  reiniciar el servicio.

## Desarrollo

| Qué | Dónde |
|---|---|
| Rutas, sondeo, autenticación, backups y comandos `init-admin` / `reset-password` / `backup` | `app.py` |
| Modelos y cifrado de las contraseñas de routers | `models.py` |
| Cliente de la API de MikroTik | `mikrotik.py` |
| Plantillas (Jinja2 + Bootstrap 5) | `templates/` |
| Tests | `tests/`, con fixtures en `conftest.py` |
| Instalación y administración | `install.sh`, `admin.sh`, `bootstrap.sh`, `ip-tracker.service`, `ip-tracker-backup.service` y `.timer` |

Para correrlo a mano:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
.venv/bin/python app.py init-admin   # muestra la contraseña inicial
.venv/bin/python app.py              # http://127.0.0.1:5001
.venv/bin/python -m pytest -q        # tests (usan una base en memoria)
```

Convenciones:

- La documentación, los textos de la interfaz y los mensajes de commit van en
  español; los nombres en el código, en inglés.
- TDD: primero el test que falla y después la implementación.
- Los cambios de esquema se aplican en `_migrate_db()` de `app.py` con
  `ALTER TABLE` (no hay Alembic).
- Nada de credenciales, IPs reales ni datos de clientes en el repo: el
  proyecto es público. Para ejemplos, usar `192.0.2.0/24` y `2001:db8::/32`.

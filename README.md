# IP-Tracker

Aplicacion web Flask para registrar y consultar asignaciones IP (DHCPv4, PPPoE e IPv6) de routers MikroTik. Diseñada para operaciones de ISP.

> ¿Instalas con ayuda de un asistente de IA? Pedile que lea [`AGENTS.md`](AGENTS.md): tiene la instalacion paso a paso, como verificarla y que no hay que tocar.

## Caracteristicas

- **Dashboard** — KPIs de DHCP, PPPoE e IPv6 activos
- **Busqueda unificada** — Por IP, MAC, usuario PPPoE o rango de fechas
- **Correlacion IPv6** — Vincula bindings IPv6 con sesiones PPPoE o leases DHCPv4
- **Timeline de cliente** — Historial cronologico completo de un cliente
- **IP Lookup (RDAP)** — Consulta datos publicos de una IP (ASN, RIR, abuso)
- **CRUD de routers** — Alta, baja y edicion de routers MikroTik
- **Polling automatico** — Recolecta datos de routers por scheduler configurable
- **Metricas del sistema** — CPU, memoria y disco
- **Backups** — Diario automatico (14 dias) y descarga desde la web
- **Auth seguro** — Login con proteccion anti-brute-force y CSRF

## Requisitos

- Ubuntu Server 22.04+ / Debian 12+
- Python 3.9+
- Git y rsync

## Instalacion automatica

Subi los archivos del proyecto al directorio `/tmp/` del servidor y ejecuta:

```bash
sudo bash /tmp/bootstrap.sh
```

El script hace todo automaticamente:
- Instala Python, venv y dependencias del sistema
- Copia el proyecto a `/opt/ip-tracker/`
- Crea entorno virtual e instala dependencias Python
- Genera una `SECRET_KEY` segura y la guarda en `/etc/ip-tracker.env`
- Crea el usuario `admin` con una contraseña al azar y la muestra al final
- Instala y arranca el servicio systemd

## Instalacion manual

```bash
# 1. Clonar o copiar el proyecto
git clone https://github.com/marioclep/ip-tracker.git /opt/ip-tracker
cd /opt/ip-tracker

# 2. Ejecutar install.sh
sudo bash install.sh
```

## Acceso

- URL: `http://<IP_DEL_SERVIDOR>:5001`
- Usuario: `admin`
- Contraseña: la que muestra `install.sh` al terminar. No hay contraseña por
  defecto: cada instalacion genera la suya.

Si te olvidaste la contraseña, genera una nueva desde la consola del servidor:

```bash
sudo bash /opt/ip-tracker/admin.sh reset-password
```

Podes cambiar el usuario y la contraseña desde el panel de Configuracion.

## Configuracion del servidor

`install.sh` crea `/etc/ip-tracker.env` (permisos 600) la primera vez y no lo
vuelve a tocar:

| Variable | Descripcion |
|----------|-------------|
| `SECRET_KEY` | Clave de sesiones y de cifrado de las contraseñas de los routers. **No cambiarla**: las contraseñas guardadas quedarian ilegibles. |
| `DATABASE_URL` | Base SQLite (por defecto `/opt/ip-tracker/ip_tracker.db`) |

La aplicacion no arranca sin `SECRET_KEY`. Para correrla a mano (desarrollo):

```bash
export SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
.venv/bin/python app.py init-admin   # muestra la contraseña inicial
.venv/bin/python app.py
```

## Configuracion de routers

Agrega tus routers MikroTik desde el panel de Configuracion:

| Campo | Descripcion |
|-------|-------------|
| Nombre | Identificador del router |
| Host | IP del router |
| Puerto API | 8728 (API sin SSL; `api-ssl`/8729 no esta soportado) |
| Usuario | Usuario con permisos API |
| Contraseña | Contraseña del usuario |
| Habilitado | Activar/Desactivar polling |

## Actualizacion

```bash
cd /opt/ip-tracker
sudo git -c safe.directory=/opt/ip-tracker pull origin main
sudo bash install.sh
```

`install.sh` conserva la configuracion y la base existentes. En instalaciones
anteriores, que guardaban la `SECRET_KEY` en la unidad systemd, la reutiliza.

## Backups

Todos los dias a las 03:00 se guarda una copia de la base en
`/var/backups/ip-tracker/` y se conservan 14 dias (timer de systemd
`ip-tracker-backup.timer`). Backup inmediato:
`sudo systemctl start ip-tracker-backup`. Tambien se puede descargar desde
Configuracion. Las copias quedan en el mismo servidor: conviene llevarlas a
otro equipo. Como restaurar: ver [`MANUAL.md`](MANUAL.md#backup).

## Tests

```bash
.venv/bin/python -m pytest -q
```

## Estructura del proyecto

```
.
├── app.py              # Aplicacion Flask (rutas, polling, auth)
├── models.py           # Modelos SQLAlchemy (DB schema)
├── mikrotik.py         # Cliente API para MikroTik
├── requirements.txt    # Dependencias Python
├── install.sh          # Script de instalacion systemd
├── admin.sh            # Comandos de administracion (reset-password)
├── bootstrap.sh        # Script de deployment desde /tmp/
├── ip-tracker.service  # Unidad systemd
├── ip-tracker-backup.* # Backup diario (servicio y timer de systemd)
├── templates/          # Templates Jinja2 (Bootstrap 5)
│   ├── base.html
│   ├── login.html
│   ├── index.html
│   ├── search.html
│   ├── lookup.html
│   ├── timeline.html
│   ├── routers.html
│   ├── router_form.html
│   ├── settings.html
│   └── metrics.html
├── conftest.py         # Fixtures de pytest
└── tests/
    ├── test_app.py          # Login, dashboard y routers
    └── test_admin_setup.py  # Contraseña inicial, reset-password y SECRET_KEY
```

## Tecnologias

- Flask 3.0 + Flask-SQLAlchemy + Flask-Login + Flask-WTF
- Bootstrap 5 + Bootstrap Icons
- APScheduler (polling)
- librouteros (API MikroTik)
- ipwhois (RDAP lookup)
- SQLite

## Licencia

Desarrollado por **[MKE Solutions](mailto:info@mkesolutions.net)**.

Este proyecto es software libre bajo la licencia
**GNU Affero General Public License v3.0** (ver [`LICENSE`](LICENSE)). Podés
usarlo, modificarlo y redistribuirlo; si ofrecés una versión modificada a
otros a través de la red, tenés que publicar también su código fuente bajo
la misma licencia.

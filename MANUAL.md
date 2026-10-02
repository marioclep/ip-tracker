# Manual de Usuario — IP-Tracker

## Tabla de contenidos

1. [Primer acceso](#primer-acceso)
2. [Dashboard](#dashboard)
3. [Busqueda](#busqueda)
4. [Timeline de cliente](#timeline-de-cliente)
5. [IP Lookup](#ip-lookup)
6. [Gestion de routers](#gestion-de-routers)
7. [Configuracion](#configuracion)
8. [Backup](#backup)
9. [Metricas](#metricas)

---

## Primer acceso

1. Abri tu navegador y navega a `http://<IP_DEL_SERVIDOR>:5001`
2. Ingresa con el usuario `admin` y la contraseña que mostro `install.sh` al
   terminar la instalacion
3. Si queres, cambia el usuario y la contraseña en **Configuracion**

> **¿Perdiste la contraseña?** Genera una nueva desde la consola del servidor:
> `sudo bash /opt/ip-tracker/admin.sh reset-password`

> **Seguridad:** Si fallas el login 5 veces, se bloquea por 15 minutos.

---

## Dashboard

Panel principal que muestra el estado actual del sistema.

| Indicador | Significado |
|-----------|-------------|
| DHCP activos | Cantidad de leases IPv4 vigentes en este momento |
| PPPoE activos | Cantidad de sesiones PPPoE conectadas |
| IPv6 activos | Cantidad de bindings IPv6 vigentes |
| Total registros | Suma historica de todos los registros en la base de datos |
| Routers | Lista de routers configurados con estado del ultimo polling |

**Acciones desde el dashboard:**
- Agregar un nuevo router
- Ver el detalle de cada router (IP, estado, ultimo poll)
- Ir a la busqueda o timeline

---

## Busqueda

Pantalla principal para consultar asignaciones IP. Soporta multiples criterios combinados.

### Buscar por IP

- **IPv4:** Ingresa la IP completa (ej: `192.168.1.100`)
- **IPv6:** Ingresa la IP completa o un prefijo (ej: `2001:db8:100:1e0::/60`)
- El sistema busca en DHCPv4, PPPoE e IPv6 simultaneamente

### Buscar por MAC

Ingresa la MAC en cualquier formato:
- `AA:BB:CC:DD:EE:FF`
- `AA-BB-CC-DD-EE-FF`
- `AABBCCDDEEFF`

### Buscar por usuario PPPoE

Ingresa el nombre de usuario (ej: `usuario@isp`).

### Filtro por fecha

Limita los resultados a un rango de fechas usando los campos **Desde** y **Hasta**.

### Resultados

Cada fila indica la fuente con un badge:

| Badge | Fuente | Datos mostrados |
|-------|--------|-----------------|
| **Verde** | DHCPv4 | IP, MAC, hostname, router |
| **Rojo** | PPPoE | IP, MAC (caller-id), usuario, interfaz |
| **Violeta** | IPv6 | IP, MAC/DUID, usuario PPPoE, interfaz |

#### Correlacion IPv6

Cuando un binding IPv6 es de tipo **prefix** (DHCP-PD), el sistema intenta vincularlo con su sesion origen:

- **Badge azul PPPoE:** El binding tiene `ppp_username` y se encontro la sesion PPPoE correlacionada. Muestra MAC + IPv4 del cliente.
- **Badge amarillo DHCP:** El binding no tiene `ppp_username`. Se extrajo la MAC del DUID y se encontro un lease DHCPv4 activo en el mismo router. Muestra MAC + IPv4 + hostname.
- **Sin badge:** No hay correlacion disponible (DHCP-PD sin lease activo, o binding tipo `address`).

---

## Timeline de cliente

Muestra el historial cronologico completo de un cliente en todos los servicios.

### Como usarlo

1. Ingresa una **MAC address** o un **usuario PPPoE**
2. Opcional: define un rango de fechas
3. Presiona **Consultar**

### Resultados

Los eventos se ordenan cronologicamente (el mas reciente arriba). Cada evento muestra:

| Campo | Descripcion |
|-------|-------------|
| Tipo | DHCPv4 (azul), PPPoE (rojo), IPv6 (violeta) |
| IP | Direccion IP asignada |
| Estado | Activo / Expirado / Desconectado |
| MAC | Direccion MAC del cliente |
| Usuario | Nombre PPPoE (si aplica) |
| Router | Nombre del router donde ocurrio |
| Periodo | Fecha de inicio y fin del evento |

> **Tip:** La MAC se extrae automaticamente del DUID en bindings IPv6 tipo prefix, incluso si no esta explicitamente registrada.

---

## IP Lookup

Consulta datos publicos de una direccion IP via RDAP (whois moderno).

### Como usarlo

1. Navega a **Lookup** en el menu
2. Ingresa una IP publica (ej: `8.8.8.8`, `1.1.1.1`)
3. Presiona **Consultar**

### Datos mostrados

| Campo | Descripcion |
|-------|-------------|
| Organizacion | Entidad propietaria del rango |
| Rango asignado | CIDR delegado (ej: `8.8.8.0/24`) |
| Pais | Codigo ISO del pais |
| ASN | Numero de sistema autonomo |
| RIR | Registro regional (ARIN, LACNIC, RIPE, APNIC, AFRINIC) |
| Email de abuso | Contacto para reportar abuso |

> **Nota:** Las IPs privadas, loopback o multicast no tienen registro RDAP y mostraran un mensaje de advertencia.

---

## Gestion de routers

### Agregar un router

1. Ve a **Routers** en el menu
2. Presiona **Agregar router**
3. Completa los campos:

| Campo | Descripcion |
|-------|-------------|
| Nombre | Identificador descriptivo |
| Host | IP o hostname del router MikroTik |
| Puerto API | `8728` (API sin SSL; `api-ssl`/8729 no esta soportado) |
| Usuario | Usuario con permisos de API read |
| Contraseña | Contraseña del usuario |
| Habilitado | Activa o desactiva el polling |

4. Opcional: presiona **Probar conexion** para verificar que la API responde
5. Presiona **Guardar**
6. El sistema comenzara a hacer polling automatico en el proximo ciclo

### Editar / Eliminar

- Presiona **Editar** en la fila del router para modificar sus datos
- Presiona **Eliminar** para quitarlo de la base de datos (esto tambien elimina todos sus registros historicos asociados)

---

## Configuracion

### General

| Parametro | Descripcion | Recomendacion |
|-----------|-------------|---------------|
| Intervalo de polling | Minutos entre cada recoleccion de datos | 5 minutos para redes medianas |
| Retencion de datos | Dias que se mantienen los registros historicos | 365 dias |
| Zona horaria | Zona horaria para mostrar fechas en la UI | Usa la zona del ISP |

### Cambiar credenciales

1. Ve a **Configuracion** y presiona **Cambiar credenciales**
2. Ingresa el nuevo usuario
3. Ingresa la nueva contraseña (minimo 6 caracteres)
4. Confirma la contraseña
5. Presiona **Guardar**

> **Importante:** Al cambiar la contraseña, se cierra la sesion actual y debes volver a iniciar sesion con las nuevas credenciales.

---

## Backup

### Automatico

Todos los dias a las 03:00 (hora del servidor) se guarda una copia completa
de la base en `/var/backups/ip-tracker/`, con nombre
`ip_tracker-AAAAMMDD-HHMMSS.db`. Se conservan 14 dias. Si el servidor estaba
apagado a esa hora, la copia se hace al arrancar.

- Ver cuando corrio: `systemctl list-timers ip-tracker-backup.timer`
- Ver que hizo: `journalctl -u ip-tracker-backup`
- Backup inmediato: `sudo systemctl start ip-tracker-backup`

**Las copias quedan en el mismo servidor.** Copialas a otro equipo, por
ejemplo con `rsync` desde un NAS.

### Desde la web

1. Ve a **Configuracion** → seccion **Base de datos**
2. Presiona **Descargar respaldo (.db)**
3. El archivo se guarda con nombre `ip_tracker_YYYY-MM-DD.db`

La copia se hace sin detener el servicio.

### Restaurar

Para restaurar una copia:

```bash
sudo systemctl stop ip-tracker
sudo cp /opt/ip-tracker/ip_tracker.db /opt/ip-tracker/ip_tracker.db.antes-de-restaurar
sudo install -o iptracker -g iptracker -m 644 /var/backups/ip-tracker/ip_tracker-AAAAMMDD-HHMMSS.db /opt/ip-tracker/ip_tracker.db
sudo systemctl start ip-tracker
```

---

## Metricas

Muestra el uso de recursos del servidor donde corre IP-Tracker.

| Metrica | Descripcion |
|---------|-------------|
| CPU | Porcentaje de uso del procesador |
| Memoria | Porcentaje de RAM utilizada |
| Disco | Porcentaje de espacio en disco usado |

> **Nota:** Estas metricas son del servidor host, no de los routers MikroTik.

---

## FAQ

**Q: ¿Por que no veo datos de un router recien agregado?**
A: El polling ocurre cada X minutos segun la configuracion. Podes esperar al siguiente ciclo o reiniciar el servicio con `sudo systemctl restart ip-tracker`.

**Q: ¿Los datos historicos se borran?**
A: Si. El parametro **Retencion de dias** define cuanto tiempo se mantienen los registros inactivos. Los activos nunca se borran.

**Q: ¿Puedo buscar un cliente que cambio de IP?**
A: Si. Busca por MAC o por usuario PPPoE en la pantalla **Timeline** para ver todo su historial de IPs.

**Q: ¿Que significa "DHCP-PD" en un binding IPv6?**
A: Es Delegacion de Prefijo DHCPv6. El router delega un prefijo /64 o mayor al cliente para que este asigne IPs dentro de su red local.

**Q: ¿El polling modifica algo en el router?**
A: No. Solo lee datos (leases, sesiones, bindings). Nunca escribe ni modifica configuracion.

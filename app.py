import io
import os
import re
import sys
import sqlite3
import logging
import ipaddress
import psutil
import tempfile
import csv
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone as dt_utc
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify, send_file, session)
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from flask_wtf import CSRFProtect
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from models import (db, Router, DhcpLease, PppSession, Ipv6Binding,
                    AppSettings, SystemMetric, ensure_admin_password,
                    reset_admin_password)
from mikrotik import (get_dhcp_leases, get_ppp_active,
                       get_ipv6_bindings, test_connection)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
app.config['WTF_CSRF_TIME_LIMIT'] = None  # Tokens CSRF no expiran (sesion activa)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///ip_tracker.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
csrf = CSRFProtect(app)
login_failures = {}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Iniciá sesión para continuar.'
login_manager.login_message_category = 'warning'


class AdminUser(UserMixin):
    id = 'admin'


@login_manager.user_loader
def load_user(user_id):
    return AdminUser() if user_id == 'admin' else None


@app.before_request
def require_login():
    public = {'login', 'static'}
    if request.endpoint not in public and not current_user.is_authenticated:
        return redirect(url_for('login', next=request.url))


# ---------------------------------------------------------------------------
# Timezone context processor
# ---------------------------------------------------------------------------

@app.context_processor
def inject_tz():
    tz_name = AppSettings.get().timezone or 'UTC'
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo('UTC')
        tz_name = 'UTC'

    def to_local(dt, fmt='%d/%m/%Y %H:%M:%S'):
        if dt is None:
            return '—'
        return dt.replace(tzinfo=dt_utc.utc).astimezone(tz).strftime(fmt)

    return {'to_local': to_local, 'tz_name': tz_name}


def _duid_to_mac(duid: str):
    """Extrae MAC address (formato XX:XX:XX:XX:XX:XX) de un DUID MikroTik."""
    if not duid:
        return None
    hex_duid = duid.lstrip('0x').lstrip('0X')
    if len(hex_duid) >= 12:
        hex_duid = hex_duid[-12:]
        return ':'.join(hex_duid[i:i+2] for i in range(0, 12, 2)).upper()
    return None


scheduler = BackgroundScheduler(timezone='UTC')


# ---------------------------------------------------------------------------
# DB migration
# ---------------------------------------------------------------------------

def _migrate_db():
    with db.engine.connect() as conn:
        def _cols(table):
            return {r[1] for r in conn.execute(db.text(f"PRAGMA table_info({table})"))}

        s_cols = _cols('app_settings')
        migrations = [
            ('timezone',            "ALTER TABLE app_settings ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'"),
            ('admin_username',      "ALTER TABLE app_settings ADD COLUMN admin_username TEXT NOT NULL DEFAULT 'admin'"),
            ('admin_password_hash', "ALTER TABLE app_settings ADD COLUMN admin_password_hash TEXT NOT NULL DEFAULT ''"),
        ]
        for col, sql in migrations:
            if col not in s_cols:
                conn.execute(db.text(sql))
                conn.commit()
                logger.info(f"Migration: added '{col}' to app_settings")


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

def poll_router(router_id):
    with app.app_context():
        router = db.session.get(Router, router_id)
        if not router or not router.enabled:
            return

        logger.info(f"Polling {router.name} ({router.host})")
        poll_time = datetime.utcnow()

        try:
            router_password = router.get_password()
            raw_dhcp = get_dhcp_leases(
                router.host, router.username, router_password, router.api_port)
            raw_ppp = get_ppp_active(
                router.host, router.username, router_password, router.api_port)
            raw_v6 = get_ipv6_bindings(
                router.host, router.username, router_password, router.api_port)

            settings = AppSettings.get()
            stale_minutes = settings.poll_interval_minutes * 2.5

            # --- PPPoE: build interface->username map for IPv6 correlation ---
            ppp_iface_map = {}
            for s in raw_ppp:
                ppp_iface_map[s['interface']] = s['username']

            # --- DHCP IPv4 leases ---
            for ld in raw_dhcp:
                q = DhcpLease.query.filter_by(
                    router_id=router.id, ip_address=ld['ip_address'],
                    mac_address=ld['mac_address'], active=True)
                existing = q.first()
                if existing:
                    existing.last_seen = poll_time
                    if ld.get('hostname'):
                        existing.hostname = ld['hostname']
                else:
                    db.session.add(DhcpLease(
                        router_id=router.id,
                        ip_address=ld['ip_address'],
                        mac_address=ld['mac_address'],
                        hostname=ld.get('hostname', ''),
                        first_seen=poll_time, last_seen=poll_time, active=True))

            # --- PPPoE IPv4 sessions ---
            for ps in raw_ppp:
                q = PppSession.query.filter_by(
                    router_id=router.id,
                    username=ps['username'],
                    active=True)
                existing = q.first()
                if existing:
                    existing.last_seen = poll_time
                    existing.ip_address = ps['ip_address']
                    existing.interface = ps['interface']
                else:
                    db.session.add(PppSession(
                        router_id=router.id,
                        username=ps['username'],
                        ip_address=ps['ip_address'],
                        interface=ps['interface'],
                        caller_id=ps.get('caller_id', ''),
                        first_seen=poll_time, last_seen=poll_time, active=True))

            # --- IPv6 bindings ---
            for bd in raw_v6:
                q = Ipv6Binding.query.filter_by(
                    router_id=router.id,
                    ipv6_address=bd['ipv6_address'],
                    binding_type=bd['binding_type'],
                    active=True)
                if bd['duid']:
                    q = q.filter_by(duid=bd['duid'])
                elif bd['mac_address']:
                    q = q.filter_by(mac_address=bd['mac_address'])
                existing = q.first()
                ppp_user = ppp_iface_map.get(bd.get('interface', ''))

                if existing:
                    existing.last_seen = poll_time
                    existing.interface = bd.get('interface')
                    if ppp_user:
                        existing.ppp_username = ppp_user
                else:
                    db.session.add(Ipv6Binding(
                        router_id=router.id,
                        ipv6_address=bd['ipv6_address'],
                        binding_type=bd['binding_type'],
                        duid=bd.get('duid'),
                        mac_address=bd.get('mac_address'),
                        interface=bd.get('interface'),
                        ppp_username=ppp_user,
                        first_seen=poll_time, last_seen=poll_time, active=True))

            # --- Stale detection for all sources ---
            stale_threshold = poll_time - timedelta(minutes=stale_minutes)

            stale_dhcp = DhcpLease.query.filter(
                DhcpLease.router_id == router.id,
                DhcpLease.active == True,
                DhcpLease.last_seen < stale_threshold).all()
            for lease in stale_dhcp:
                lease.active = False

            stale_ppp = PppSession.query.filter(
                PppSession.router_id == router.id,
                PppSession.active == True,
                PppSession.last_seen < stale_threshold).all()
            for sess in stale_ppp:
                sess.active = False

            stale_v6 = Ipv6Binding.query.filter(
                Ipv6Binding.router_id == router.id,
                Ipv6Binding.active == True,
                Ipv6Binding.last_seen < stale_threshold).all()
            for bd in stale_v6:
                bd.active = False

            router.last_poll = poll_time
            router.last_poll_status = 'ok'
            router.last_poll_error = None
            router.last_poll_dhcp_count = len(raw_dhcp)
            router.last_poll_ppp_count = len(raw_ppp)
            router.last_poll_ipv6_count = len(raw_v6)

            db.session.commit()
            logger.info(
                f"{router.name}: DHCP {len(raw_dhcp)}, PPPoE {len(raw_ppp)}, "
                f"IPv6 {len(raw_v6)}")

            # --- Retention pruning ---
            cutoff = poll_time - timedelta(days=settings.retention_days)
            for model in (DhcpLease, PppSession, Ipv6Binding):
                removed = model.query.filter(
                    model.router_id == router.id,
                    model.last_seen < cutoff).delete()
                if removed:
                    db.session.commit()

        except Exception as exc:
            logger.error(f"Error polling {router.name}: {exc}")
            router.last_poll = poll_time
            router.last_poll_status = 'error'
            router.last_poll_error = str(exc)
            db.session.commit()


def poll_all_routers():
    with app.app_context():
        for router in Router.query.filter_by(enabled=True).all():
            poll_router(router.id)


def reschedule_polling():
    settings = AppSettings.get()
    interval = settings.poll_interval_minutes
    scheduler.add_job(
        poll_all_routers,
        trigger=IntervalTrigger(minutes=interval),
        id='poll_all', name='Poll all routers', replace_existing=True,
    )
    logger.info(f"Polling rescheduled every {interval} minutes")


# ---------------------------------------------------------------------------
# System metrics
# ---------------------------------------------------------------------------

def collect_system_metrics():
    with app.app_context():
        try:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            disk = psutil.disk_usage('/').percent
            db.session.add(SystemMetric(
                timestamp=datetime.utcnow(),
                cpu_percent=cpu, memory_percent=mem, disk_percent=disk))
            cutoff = datetime.utcnow() - timedelta(days=400)
            SystemMetric.query.filter(SystemMetric.timestamp < cutoff).delete()
            db.session.commit()
        except Exception as exc:
            logger.error(f"Error collecting system metrics: {exc}")


# ---------------------------------------------------------------------------
# Routes - Auth
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        ip_address = request.remote_addr or 'unknown'
        if _login_blocked(ip_address):
            flash('Demasiados intentos. Esperá 15 minutos e intentá de nuevo.', 'danger')
            return render_template('login.html'), 429

        settings = AppSettings.get()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == settings.admin_username and settings.check_password(password):
            _clear_login_failures(ip_address)
            login_user(AdminUser(), remember=request.form.get('remember') == 'on')
            next_url = request.args.get('next')
            return redirect(_safe_redirect_url(next_url))
        _record_login_failure(ip_address)
        flash('Usuario o contraseña incorrectos.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    logout_user()
    flash('Sesión cerrada.', 'info')
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Routes - Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    routers = Router.query.all()
    settings = AppSettings.get()
    active_dhcp = DhcpLease.query.filter_by(active=True).count()
    active_ppp = PppSession.query.filter_by(active=True).count()
    active_v6 = Ipv6Binding.query.filter_by(active=True).count()
    total_records = (DhcpLease.query.count()
                     + PppSession.query.count()
                     + Ipv6Binding.query.count())
    return render_template('index.html', routers=routers, settings=settings,
                           active_dhcp=active_dhcp, active_ppp=active_ppp,
                           active_v6=active_v6, total_records=total_records)


# ---------------------------------------------------------------------------
# Routes - Routers CRUD
# ---------------------------------------------------------------------------

@app.route('/routers')
def routers_list():
    return render_template('routers.html', routers=Router.query.all())


@app.route('/routers/add', methods=['GET', 'POST'])
def router_add():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        host = request.form.get('host', '').strip()
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        api_port = _safe_int(request.form.get('api_port'), 8728)
        enabled = request.form.get('enabled') == 'on'
        if not name or not host or not username:
            flash('Nombre, Host y Usuario son obligatorios.', 'danger')
            return render_template('router_form.html', router=None, action='Agregar')
        new_router = Router(name=name, host=host, username=username,
                            api_port=api_port, enabled=enabled)
        new_router.set_password(password)
        db.session.add(new_router)
        db.session.commit()
        flash(f'Router "{name}" agregado.', 'success')
        return redirect(url_for('routers_list'))
    return render_template('router_form.html', router=None, action='Agregar')


@app.route('/routers/<int:router_id>/edit', methods=['GET', 'POST'])
def router_edit(router_id):
    router = db.get_or_404(Router, router_id)
    if request.method == 'POST':
        router.name = request.form.get('name', '').strip()
        router.host = request.form.get('host', '').strip()
        router.username = request.form.get('username', '').strip()
        new_pw = request.form.get('password', '')
        if new_pw:
            router.set_password(new_pw)
        router.api_port = _safe_int(request.form.get('api_port'), 8728)
        router.enabled = request.form.get('enabled') == 'on'
        if not router.name or not router.host or not router.username:
            flash('Nombre, Host y Usuario son obligatorios.', 'danger')
            return render_template('router_form.html', router=router, action='Editar')
        db.session.commit()
        flash(f'Router "{router.name}" actualizado.', 'success')
        return redirect(url_for('routers_list'))
    return render_template('router_form.html', router=router, action='Editar')


@app.route('/routers/<int:router_id>/delete', methods=['POST'])
def router_delete(router_id):
    router = db.get_or_404(Router, router_id)
    name = router.name
    db.session.delete(router)
    db.session.commit()
    flash(f'Router "{name}" eliminado.', 'success')
    return redirect(url_for('routers_list'))


@app.route('/routers/<int:router_id>/toggle', methods=['POST'])
def router_toggle(router_id):
    router = db.get_or_404(Router, router_id)
    router.enabled = not router.enabled
    db.session.commit()
    estado = 'habilitado' if router.enabled else 'deshabilitado'
    flash(f'Router "{router.name}" {estado}.', 'info')
    return redirect(url_for('routers_list'))


@app.route('/routers/<int:router_id>/poll', methods=['POST'])
def router_poll_now(router_id):
    router = db.get_or_404(Router, router_id)
    poll_router(router_id)
    flash(f'Sondeo manual de "{router.name}" completado.', 'info')
    return redirect(url_for('index'))


@app.route('/poll-all', methods=['POST'])
def poll_all_now():
    poll_all_routers()
    flash('Sondeo manual de todos los routers completado.', 'info')
    return redirect(url_for('index'))


# ---------------------------------------------------------------------------
# Routes - Metrics
# ---------------------------------------------------------------------------

@app.route('/metrics')
def metrics():
    return render_template('metrics.html')


@app.route('/api/metrics')
def api_metrics():
    range_name = request.args.get('range', 'day')
    range_config = {
        'day':   {'delta': timedelta(days=1),   'bucket_min': 5},
        'week':  {'delta': timedelta(days=7),   'bucket_min': 60},
        'month': {'delta': timedelta(days=30),  'bucket_min': 240},
        'year':  {'delta': timedelta(days=365), 'bucket_min': 1440},
    }
    cfg = range_config.get(range_name, range_config['day'])
    since = datetime.utcnow() - cfg['delta']
    records = SystemMetric.query.filter(
        SystemMetric.timestamp >= since).order_by(SystemMetric.timestamp).all()
    aggregated = _aggregate_metrics(records, cfg['bucket_min'])
    tz_name = AppSettings.get().timezone or 'UTC'
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo('UTC')
    fmt = '%d/%m %H:%M' if range_name in ('day', 'week') else '%d/%m/%Y'
    labels = [r['ts'].replace(tzinfo=dt_utc.utc).astimezone(tz).strftime(fmt)
              for r in aggregated]
    return jsonify({
        'labels': labels,
        'cpu':    [round(r['cpu'], 1) for r in aggregated],
        'memory': [round(r['mem'], 1) for r in aggregated],
        'disk':   [round(r['disk'], 1) for r in aggregated],
    })


# ---------------------------------------------------------------------------
# Routes - Settings
# ---------------------------------------------------------------------------

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    s = AppSettings.get()
    if request.method == 'POST':
        action = request.form.get('action', 'general')
        if action == 'password':
            new_user = request.form.get('new_username', '').strip()
            new_pass = request.form.get('new_password', '')
            confirm = request.form.get('confirm_password', '')
            if not new_user:
                flash('El nombre de usuario no puede estar vacío.', 'danger')
            elif new_pass != confirm:
                flash('Las contraseñas no coinciden.', 'danger')
            elif len(new_pass) < 6:
                flash('La contraseña debe tener al menos 6 caracteres.', 'danger')
            else:
                s.admin_username = new_user
                s.set_password(new_pass)
                db.session.commit()
                flash('Credenciales actualizadas. Iniciá sesión nuevamente.', 'success')
                logout_user()
                return redirect(url_for('login'))
        else:
            interval = _safe_int(request.form.get('poll_interval_minutes'), 5)
            retention = _safe_int(request.form.get('retention_days'), 365)
            tz = request.form.get('timezone', 'UTC').strip()
            try:
                ZoneInfo(tz)
            except (ZoneInfoNotFoundError, KeyError):
                tz = 'UTC'
            s.poll_interval_minutes = max(1, interval)
            s.retention_days = max(1, retention)
            s.timezone = tz
            db.session.commit()
            reschedule_polling()
            flash('Configuración guardada y scheduler actualizado.', 'success')
        return redirect(url_for('settings'))
    return render_template('settings.html', settings=s)


# ---------------------------------------------------------------------------
# Routes - Backup
# ---------------------------------------------------------------------------

@app.route('/backup/db')
def backup_db():
    db_path = os.path.join(app.instance_path, 'ip_tracker.db')
    filename = f"ip_tracker_{datetime.utcnow().strftime('%Y-%m-%d')}.db"
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(tmp_path)
        src.backup(dst)
        dst.close()
        src.close()
        buf = io.BytesIO()
        with open(tmp_path, 'rb') as f:
            buf.write(f.read())
        buf.seek(0)
        os.unlink(tmp_path)
        return send_file(buf, as_attachment=True, download_name=filename,
                         mimetype='application/x-sqlite3')
    except Exception as exc:
        logger.error(f"DB backup failed: {exc}")
        flash('Error al generar el respaldo.', 'danger')
        return redirect(url_for('settings'))


# ---------------------------------------------------------------------------
# Routes - Search
# ---------------------------------------------------------------------------

@app.route('/search')
def search():
    results = []
    searched = False
    query_ip = request.args.get('ip', '').strip()
    query_mac = request.args.get('mac', '').strip().upper()
    query_user = request.args.get('user', '').strip()
    query_prefix = request.args.get('prefix', '').strip()
    query_from = request.args.get('from', '')
    query_to = request.args.get('to', '')

    if query_ip or query_mac or query_user or query_prefix:
        searched = True
        tz_name = AppSettings.get().timezone or 'UTC'
        from_dt = _local_to_utc(query_from, tz_name)
        to_dt = _local_to_utc(query_to, tz_name)

        # --- DHCP results ---
        if query_ip or query_mac:
            q = DhcpLease.query.join(Router)
            if query_ip:
                q = q.filter(DhcpLease.ip_address == query_ip)
            if query_mac:
                q = q.filter(DhcpLease.mac_address == query_mac)
            if from_dt:
                q = q.filter(DhcpLease.last_seen >= from_dt)
            if to_dt:
                q = q.filter(DhcpLease.first_seen <= to_dt)
            for r in q.order_by(DhcpLease.first_seen.desc()).all():
                results.append({'source': 'dhcp', 'record': r})

        # --- PPPoE results ---
        if query_ip or query_user:
            q = PppSession.query.join(Router)
            if query_ip:
                q = q.filter(PppSession.ip_address == query_ip)
            if query_user:
                q = q.filter(PppSession.username == query_user)
            if from_dt:
                q = q.filter(PppSession.last_seen >= from_dt)
            if to_dt:
                q = q.filter(PppSession.first_seen <= to_dt)
            for r in q.order_by(PppSession.first_seen.desc()).all():
                results.append({'source': 'pppoe', 'record': r})

        def _enrich_ipv6(r):
            result = {'source': 'ipv6', 'record': r}
            # PPPoE: correlar por username desde ppp_iface_map
            if r.binding_type == 'prefix' and r.ppp_username:
                ppp = PppSession.query.filter(
                    PppSession.router_id == r.router_id,
                    PppSession.username == r.ppp_username,
                    PppSession.first_seen <= r.last_seen,
                    PppSession.last_seen >= r.first_seen,
                ).order_by(PppSession.last_seen.desc()).first()
                if ppp:
                    result['ppp_mac'] = ppp.caller_id
                    result['ppp_ip'] = ppp.ip_address
            # DHCP-PD: extraer MAC del DUID y buscar lease DHCPv4 activo en el mismo router
            elif r.binding_type == 'prefix' and not r.ppp_username and r.duid:
                mac = _duid_to_mac(r.duid)
                if mac:
                    lease = DhcpLease.query.filter(
                        DhcpLease.router_id == r.router_id,
                        DhcpLease.mac_address == mac,
                        DhcpLease.active == True,
                    ).order_by(DhcpLease.last_seen.desc()).first()
                    if lease:
                        result['dhcp_mac'] = mac
                        result['dhcp_ip'] = lease.ip_address
                        result['dhcp_hostname'] = lease.hostname
                    else:
                        result['dhcp_mac'] = mac
                        result['dhcp_hint'] = ('Posible MAC del DUID; sin lease DHCPv4 '
                                               'activo en este momento.')
            return result

        # --- IPv6 results ---
        if query_ip or query_prefix or query_mac or query_user:
            q = Ipv6Binding.query.join(Router)
            if query_prefix:
                target_prefix = _normalize_prefix(query_prefix)
                q = q.filter(Ipv6Binding.ipv6_address == target_prefix)
            elif query_ip:
                ip_obj = _parse_ipv6(query_ip)
                if ip_obj:
                    exact_matches = q.filter(
                        Ipv6Binding.ipv6_address == str(ip_obj)).all()
                    for r in exact_matches:
                        results.append(_enrich_ipv6(r))
                    pq = Ipv6Binding.query.join(Router).filter(
                        Ipv6Binding.binding_type == 'prefix')
                    if from_dt:
                        pq = pq.filter(Ipv6Binding.last_seen >= from_dt)
                    if to_dt:
                        pq = pq.filter(Ipv6Binding.first_seen <= to_dt)
                    for r in pq.order_by(Ipv6Binding.first_seen.desc()).all():
                        if _ip_in_prefix(ip_obj, r.ipv6_address):
                            results.append(_enrich_ipv6(r))
                    q = None
                else:
                    # query_ip is IPv4 (not v6); skip IPv6 section
                    q = None
            if query_mac and q:
                q = q.filter(Ipv6Binding.mac_address == query_mac)
            if query_user and q:
                q = q.filter(Ipv6Binding.ppp_username == query_user)
            if from_dt and q:
                q = q.filter(Ipv6Binding.last_seen >= from_dt)
            if to_dt and q:
                q = q.filter(Ipv6Binding.first_seen <= to_dt)
            if q:
                for r in q.order_by(Ipv6Binding.first_seen.desc()).all():
                    results.append(_enrich_ipv6(r))

    return render_template('search.html',
                           results=results, searched=searched,
                           query_ip=query_ip, query_mac=query_mac,
                           query_user=query_user, query_prefix=query_prefix,
                           query_from=query_from, query_to=query_to)


# ---------------------------------------------------------------------------
# Routes - Timeline de un cliente (unifica DHCP + PPPoE + IPv6)
# ---------------------------------------------------------------------------

@app.route('/timeline')
def timeline():
    results = []
    searched = False
    query_mac = request.args.get('mac', '').strip().upper()
    query_user = request.args.get('user', '').strip()
    query_from = request.args.get('from', '')
    query_to = request.args.get('to', '')

    if query_mac or query_user:
        searched = True
        tz_name = AppSettings.get().timezone or 'UTC'
        from_dt = _local_to_utc(query_from, tz_name)
        to_dt = _local_to_utc(query_to, tz_name)

        def _overlap(model_q, ts_field='first_seen', end_field='last_seen'):
            """Aplica filtros de rango temporal sobre campos first_seen / last_seen."""
            if from_dt:
                model_q = model_q.filter(getattr(model, end_field) >= from_dt)
            if to_dt:
                model_q = model_q.filter(getattr(model, ts_field) <= to_dt)
            return model_q

        # --- DHCP leases ---
        if query_mac:
            _q = DhcpLease.query.join(Router).filter(
                DhcpLease.mac_address == query_mac)
            if from_dt:
                _q = _q.filter(DhcpLease.last_seen >= from_dt)
            if to_dt:
                _q = _q.filter(DhcpLease.first_seen <= to_dt)
            for r in _q.order_by(DhcpLease.first_seen.desc()).all():
                results.append({
                    'type': 'dhcp',
                    'ip': r.ip_address,
                    'mac': r.mac_address,
                    'user': r.hostname,
                    'router_name': r.router.name,
                    'start': r.first_seen,
                    'end': r.last_seen,
                    'active': r.active,
                })

        # --- PPPoE sessions ---
        _q = PppSession.query.join(Router)
        if query_mac:
            _q = _q.filter(PppSession.caller_id == query_mac)
        if query_user:
            _q = _q.filter(PppSession.username == query_user)
        if from_dt:
            _q = _q.filter(PppSession.last_seen >= from_dt)
        if to_dt:
            _q = _q.filter(PppSession.first_seen <= to_dt)
        for r in _q.order_by(PppSession.first_seen.desc()).all():
            results.append({
                'type': 'pppoe',
                'ip': r.ip_address,
                'mac': r.caller_id,
                'user': r.username,
                'router_name': r.router.name,
                'start': r.first_seen,
                'end': r.last_seen,
                'active': r.active,
            })

        # --- IPv6 bindings ---
        if query_mac or query_user:
            _q = Ipv6Binding.query.join(Router)
            if query_mac:
                _q = _q.filter(
                    (Ipv6Binding.mac_address == query_mac) |
                    (Ipv6Binding.duid.ilike('%' + query_mac.replace(':', '').replace('-', '').replace('.', '') + '%')
                     if len(query_mac.replace(':', '').replace('-', '').replace('.', '')) >= 12 else False)
                )
            if query_user:
                _q = _q.filter(Ipv6Binding.ppp_username == query_user)
            if from_dt:
                _q = _q.filter(Ipv6Binding.last_seen >= from_dt)
            if to_dt:
                _q = _q.filter(Ipv6Binding.first_seen <= to_dt)
            for r in _q.order_by(Ipv6Binding.first_seen.desc()).all():
                results.append({
                    'type': 'ipv6',
                    'ip': r.ipv6_address,
                    'mac': r.mac_address or _duid_to_mac(r.duid) or r.duid,
                    'user': r.ppp_username,
                    'router_name': r.router.name,
                    'start': r.first_seen,
                    'end': r.last_seen,
                    'active': r.active,
                })

        results.sort(key=lambda x: x['start'] or datetime.min, reverse=True)

    return render_template('timeline.html',
                           results=results, searched=searched,
                           query_mac=query_mac, query_user=query_user,
                           query_from=query_from, query_to=query_to)



# ---------------------------------------------------------------------------
# Routes - IP Lookup (RDAP)
# ---------------------------------------------------------------------------

@app.route('/lookup')
def lookup():
    query_ip = request.args.get('ip', '').strip()
    result = None
    error = None

    if query_ip:
        try:
            ip_obj = ipaddress.ip_address(query_ip)
            if not ip_obj.is_global or ip_obj.is_multicast:
                error = 'Esta IP es privada/reservada, no tiene registro publico en RDAP.'
            else:
                from ipwhois import IPWhois
                rdap = IPWhois(str(ip_obj), timeout=10).lookup_rdap(
                    depth=1, retry_count=1, rate_limit_timeout=15
                )

                abuse_email = None
                for _, obj in (rdap.get('objects') or {}).items():
                    roles = obj.get('roles') or []
                    if 'abuse' in roles:
                        for contact_type, contacts in (obj.get('contact') or {}).items():
                            if contact_type == 'email' and contacts:
                                abuse_email = contacts[0].get('value')
                                break
                    if abuse_email:
                        break

                result = {
                    'org':         rdap.get('network', {}).get('name') or None,
                    'cidr':        rdap.get('network', {}).get('cidr') or None,
                    'country':     rdap.get('network', {}).get('country') or None,
                    'asn':         rdap.get('asn') or None,
                    'asn_desc':    rdap.get('asn_description') or None,
                    'abuse_email': abuse_email,
                    'rir':         (rdap.get('asn_registry') or '').upper() or None,
                }
        except ValueError:
            error = f'"{query_ip}" no es una direccion IP valida.'
        except ImportError:
            logger.error("ipwhois package is not installed; /lookup route is non-functional")
            error = 'El paquete ipwhois no esta instalado. Ejecuta: pip install ipwhois'
        except Exception as exc:
            logger.warning(f"IP lookup failed for {query_ip}: {exc}")
            error = 'No se pudo consultar RDAP. La IP puede estar en una zona sin registro o el servicio no responde.'
    return render_template('lookup.html', query_ip=query_ip, result=result, error=error)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route('/api/test-connection', methods=['POST'])
@csrf.exempt
def api_test_connection():
    data = request.get_json() or {}
    success, message = test_connection(
        data.get('host', ''), data.get('username', ''),
        data.get('password', ''), data.get('port', 8728))
    safe_message = message.replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
    return jsonify({'success': success, 'message': safe_message})


@app.route('/api/stats')
def api_stats():
    return jsonify({
        'active_dhcp': DhcpLease.query.filter_by(active=True).count(),
        'active_ppp':  PppSession.query.filter_by(active=True).count(),
        'active_v6':   Ipv6Binding.query.filter_by(active=True).count(),
        'total_records': (DhcpLease.query.count()
                          + PppSession.query.count()
                          + Ipv6Binding.query.count()),
        'routers_total': Router.query.count(),
        'routers_ok':    Router.query.filter_by(last_poll_status='ok',
                                                enabled=True).count(),
        'routers_error': Router.query.filter_by(last_poll_status='error',
                                                 enabled=True).count(),
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_redirect_url(target):
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return url_for('index')


def _login_blocked(ip_address):
    attempts = login_failures.get(ip_address, [])
    cutoff = datetime.utcnow() - timedelta(minutes=15)
    attempts = [ts for ts in attempts if ts > cutoff]
    login_failures[ip_address] = attempts
    return len(attempts) >= 5


def _record_login_failure(ip_address):
    attempts = login_failures.setdefault(ip_address, [])
    attempts.append(datetime.utcnow())


def _clear_login_failures(ip_address):
    login_failures.pop(ip_address, None)


def _parse_dt(value):
    if not value:
        return None
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _local_to_utc(value, tz_name):
    naive = _parse_dt(value)
    if naive is None:
        return None
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo('UTC')
    return naive.replace(tzinfo=tz).astimezone(dt_utc.utc).replace(tzinfo=None)


def _normalize_prefix(prefix_str):
    try:
        return str(ipaddress.ip_network(prefix_str, strict=False))
    except ValueError:
        return prefix_str.strip().lower()


def _ip_in_prefix(ip_obj, prefix_str):
    try:
        return ip_obj in ipaddress.ip_network(prefix_str, strict=False)
    except ValueError:
        return False


def _parse_ipv6(value):
    if not value:
        return None
    try:
        addr = ipaddress.ip_address(value)
        return addr if addr.version == 6 else None
    except ValueError:
        return None


def _aggregate_metrics(records, bucket_minutes):
    if not records:
        return []
    buckets = {}
    for r in records:
        ts = r.timestamp
        floored = ts - timedelta(
            minutes=ts.minute % bucket_minutes,
            seconds=ts.second, microseconds=ts.microsecond)
        b = buckets.setdefault(floored, {'cpu': [], 'mem': [], 'disk': []})
        b['cpu'].append(r.cpu_percent)
        b['mem'].append(r.memory_percent)
        b['disk'].append(r.disk_percent)
    return [
        {'ts': ts,
         'cpu': sum(v['cpu']) / len(v['cpu']),
         'mem': sum(v['mem']) / len(v['mem']),
         'disk': sum(v['disk']) / len(v['disk'])}
        for ts in sorted(buckets) for v in [buckets[ts]]
    ]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

PLACEHOLDER_SECRET_KEY = 'CHANGE_THIS_SECRET_KEY'


def require_secret_key():
    key = app.config.get('SECRET_KEY')
    if not key or key == PLACEHOLDER_SECRET_KEY:
        raise RuntimeError(
            'SECRET_KEY is not set. Define it in the environment '
            '(install.sh writes it to /etc/ip-tracker.env).')


def _init_db():
    db.create_all()
    _migrate_db()


def cli(argv):
    """Admin commands: init-admin (set a password if none) and reset-password."""
    command = argv[0] if argv else ''
    if command not in ('init-admin', 'reset-password'):
        print('Uso: python app.py [init-admin | reset-password]', file=sys.stderr)
        return 2
    with app.app_context():
        _init_db()
        if command == 'init-admin':
            password = ensure_admin_password()
            if password is None:
                print('El administrador ya tiene contraseña; no se cambió. '
                      'Para generar una nueva: python app.py reset-password')
                return 0
        else:
            password = reset_admin_password()
        username = AppSettings.get().admin_username
        print(f"Contraseña del usuario '{username}': {password}")
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))

    require_secret_key()
    with app.app_context():
        _init_db()
        password = ensure_admin_password()
        if password:
            logger.warning(f"Initial password for '{AppSettings.get().admin_username}': {password}")
        reschedule_polling()

    scheduler.add_job(
        collect_system_metrics,
        trigger=IntervalTrigger(minutes=5),
        id='metrics', name='Collect system metrics', replace_existing=True)
    scheduler.start()
    try:
        app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False)
    finally:
        scheduler.shutdown()

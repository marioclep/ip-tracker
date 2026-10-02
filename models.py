import base64
import hashlib
import os
import secrets
from datetime import datetime
from cryptography.fernet import Fernet, InvalidToken
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def _credential_cipher():
    key_source = os.environ.get('CREDENTIAL_ENCRYPTION_KEY') or os.environ.get('SECRET_KEY')
    if not key_source:
        raise RuntimeError('SECRET_KEY is not set: router credentials cannot be encrypted.')
    digest = hashlib.sha256(key_source.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class Router(db.Model):
    __tablename__ = 'routers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    host = db.Column(db.String(200), nullable=False)
    username = db.Column(db.String(100), nullable=False, default='admin')
    password = db.Column(db.String(200), nullable=False, default='')
    api_port = db.Column(db.Integer, nullable=False, default=8728)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_poll = db.Column(db.DateTime, nullable=True)
    last_poll_status = db.Column(db.String(20), nullable=True)
    last_poll_error = db.Column(db.Text, nullable=True)
    last_poll_dhcp_count = db.Column(db.Integer, nullable=True)
    last_poll_ppp_count = db.Column(db.Integer, nullable=True)
    last_poll_ipv6_count = db.Column(db.Integer, nullable=True)

    dhcp_leases = db.relationship('DhcpLease', backref='router', lazy='dynamic',
                                  cascade='all, delete-orphan')
    ppp_sessions = db.relationship('PppSession', backref='router', lazy='dynamic',
                                   cascade='all, delete-orphan')
    ipv6_bindings = db.relationship('Ipv6Binding', backref='router', lazy='dynamic',
                                    cascade='all, delete-orphan')

    def set_password(self, password):
        self.password = _credential_cipher().encrypt(password.encode()).decode()

    def get_password(self):
        if not self.password:
            return ''
        try:
            return _credential_cipher().decrypt(self.password.encode()).decode()
        except (InvalidToken, ValueError):
            return self.password


class DhcpLease(db.Model):
    __tablename__ = 'dhcp_leases'

    id = db.Column(db.Integer, primary_key=True)
    router_id = db.Column(db.Integer, db.ForeignKey('routers.id'), nullable=False, index=True)
    ip_address = db.Column(db.String(45), nullable=False, default='')
    mac_address = db.Column(db.String(50), nullable=True)
    hostname = db.Column(db.String(200), nullable=True)
    first_seen = db.Column(db.DateTime, nullable=False, index=True)
    last_seen = db.Column(db.DateTime, nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, index=True)

    __table_args__ = (
        db.Index('idx_dhcp_ip_time', 'ip_address', 'first_seen', 'last_seen'),
        db.Index('idx_dhcp_mac_time', 'mac_address', 'first_seen', 'last_seen'),
    )


class PppSession(db.Model):
    __tablename__ = 'ppp_sessions'

    id = db.Column(db.Integer, primary_key=True)
    router_id = db.Column(db.Integer, db.ForeignKey('routers.id'), nullable=False, index=True)
    username = db.Column(db.String(200), nullable=False)
    ip_address = db.Column(db.String(45), nullable=False, default='')
    interface = db.Column(db.String(100), nullable=True)
    caller_id = db.Column(db.String(200), nullable=True)
    first_seen = db.Column(db.DateTime, nullable=False, index=True)
    last_seen = db.Column(db.DateTime, nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, index=True)

    __table_args__ = (
        db.Index('idx_ppp_ip_time', 'ip_address', 'first_seen', 'last_seen'),
        db.Index('idx_ppp_user_time', 'username', 'first_seen', 'last_seen'),
    )


class Ipv6Binding(db.Model):
    __tablename__ = 'ipv6_bindings'

    id = db.Column(db.Integer, primary_key=True)
    router_id = db.Column(db.Integer, db.ForeignKey('routers.id'), nullable=False, index=True)
    ipv6_address = db.Column(db.String(50), nullable=False, default='')
    binding_type = db.Column(db.String(10), nullable=False, default='address')
    # 'address' for IA-NA, 'prefix' for IA-PD
    duid = db.Column(db.String(200), nullable=True)
    mac_address = db.Column(db.String(50), nullable=True)
    interface = db.Column(db.String(100), nullable=True)
    ppp_username = db.Column(db.String(200), nullable=True)
    first_seen = db.Column(db.DateTime, nullable=False, index=True)
    last_seen = db.Column(db.DateTime, nullable=False, index=True)
    active = db.Column(db.Boolean, default=True, index=True)

    __table_args__ = (
        db.Index('idx_ipv6_addr_time', 'ipv6_address', 'first_seen', 'last_seen'),
        db.Index('idx_ipv6_mac_time', 'mac_address', 'first_seen', 'last_seen'),
        db.Index('idx_ipv6_user_time', 'ppp_username', 'first_seen', 'last_seen'),
    )


class SystemMetric(db.Model):
    __tablename__ = 'system_metrics'

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    cpu_percent = db.Column(db.Float, nullable=False)
    memory_percent = db.Column(db.Float, nullable=False)
    disk_percent = db.Column(db.Float, nullable=False)


class AppSettings(db.Model):
    __tablename__ = 'app_settings'

    id = db.Column(db.Integer, primary_key=True)
    poll_interval_minutes = db.Column(db.Integer, nullable=False, default=5)
    retention_days = db.Column(db.Integer, nullable=False, default=365)
    timezone = db.Column(db.String(50), nullable=False, default='UTC')
    admin_username = db.Column(db.String(50), nullable=False, default='admin')
    admin_password_hash = db.Column(db.String(256), nullable=False, default='')

    def set_password(self, password):
        self.admin_password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def check_password(self, password):
        if not self.admin_password_hash:
            return False
        return check_password_hash(self.admin_password_hash, password)

    @classmethod
    def get(cls):
        settings = cls.query.first()
        if not settings:
            settings = cls(poll_interval_minutes=5, retention_days=365,
                           timezone='UTC', admin_username='admin')
            db.session.add(settings)
            db.session.commit()
        return settings


def _generate_password():
    return secrets.token_urlsafe(16)


def ensure_admin_password():
    """Set a random admin password if none exists. Returns it, or None if one was already set."""
    settings = AppSettings.get()
    if settings.admin_password_hash:
        return None
    return reset_admin_password()


def reset_admin_password():
    """Replace the admin password with a random one and return it."""
    settings = AppSettings.get()
    password = _generate_password()
    settings.set_password(password)
    db.session.commit()
    return password

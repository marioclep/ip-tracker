import pytest

import app as app_module
from models import AppSettings, Router, db as _db, ensure_admin_password, reset_admin_password


def _clear_password():
    s = AppSettings.get()
    s.admin_password_hash = ''
    _db.session.commit()
    return s


class TestNoDefaultPassword:
    def test_new_settings_have_no_password(self, app):
        _db.session.query(AppSettings).delete()
        _db.session.commit()
        assert AppSettings.get().admin_password_hash == ''

    @pytest.mark.parametrize('password', ['', 'admin'])
    def test_login_fails_while_no_password_is_set(self, client, password):
        _clear_password()
        resp = client.post('/login', data={'username': 'admin', 'password': password})
        assert b'incorrectos' in resp.data


class TestEnsureAdminPassword:
    def test_generates_random_password_when_missing(self, app):
        _clear_password()
        password = ensure_admin_password()
        assert password and len(password) >= 16
        assert AppSettings.get().check_password(password)

    def test_generated_passwords_differ(self, app):
        _clear_password()
        first = ensure_admin_password()
        _clear_password()
        assert ensure_admin_password() != first

    def test_keeps_existing_password(self, app):
        s = AppSettings.get()
        s.set_password('existing-pass')
        _db.session.commit()
        assert ensure_admin_password() is None
        assert AppSettings.get().check_password('existing-pass')


class TestResetAdminPassword:
    def test_replaces_password(self, app):
        s = AppSettings.get()
        s.set_password('old-pass')
        _db.session.commit()
        new = reset_admin_password()
        assert new and len(new) >= 16
        assert AppSettings.get().check_password(new)
        assert not AppSettings.get().check_password('old-pass')


class TestCli:
    def test_reset_password_prints_new_password(self, app, capsys):
        assert app_module.cli(['reset-password']) == 0
        out = capsys.readouterr().out
        password = out.strip().splitlines()[-1].split()[-1]
        assert AppSettings.get().check_password(password)

    def test_init_admin_prints_password_only_once(self, app, capsys):
        _clear_password()
        assert app_module.cli(['init-admin']) == 0
        password = capsys.readouterr().out.strip().splitlines()[-1].split()[-1]
        assert AppSettings.get().check_password(password)

        assert app_module.cli(['init-admin']) == 0
        assert password not in capsys.readouterr().out
        assert AppSettings.get().check_password(password)

    def test_unknown_command_fails(self, app, capsys):
        assert app_module.cli(['nope']) == 2


class TestSecretKeyRequired:
    @pytest.mark.parametrize('value', [None, '', 'CHANGE_THIS_SECRET_KEY'])
    def test_server_refuses_to_start_without_real_key(self, app, value):
        app.config['SECRET_KEY'] = value
        with pytest.raises(RuntimeError):
            app_module.require_secret_key()

    def test_server_accepts_real_key(self, app):
        app.config['SECRET_KEY'] = 'a' * 64
        app_module.require_secret_key()

    def test_router_password_needs_a_key(self, app, monkeypatch):
        monkeypatch.delenv('SECRET_KEY', raising=False)
        monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
        with pytest.raises(RuntimeError):
            Router(name='R', host='192.0.2.1').set_password('x')

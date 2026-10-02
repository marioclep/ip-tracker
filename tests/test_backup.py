import os
import sqlite3
import time

import app as app_module
from conftest import do_login
from models import Router


def _routers_in(path):
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute('SELECT name FROM routers')]
    finally:
        conn.close()


def _add_router(db, name='R1'):
    db.session.add(Router(name=name, host='192.0.2.1', username='admin', password='', enabled=True))
    db.session.commit()


class TestWebBackup:
    def test_downloads_the_database_in_use(self, client, db, tmp_path):
        _add_router(db, 'Backup Router')
        do_login(client)
        resp = client.get('/backup/db')
        assert resp.status_code == 200
        assert resp.headers['Content-Disposition'].startswith('attachment')
        out = tmp_path / 'downloaded.db'
        out.write_bytes(resp.data)
        assert _routers_in(out) == ['Backup Router']

    def test_requires_login(self, client):
        resp = client.get('/backup/db')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


class TestBackupCommand:
    def test_writes_a_copy_to_the_directory(self, app, db, tmp_path, capsys):
        _add_router(db, 'CLI Router')
        assert app_module.cli(['backup', str(tmp_path)]) == 0
        files = [f for f in os.listdir(tmp_path) if f.endswith('.db')]
        assert len(files) == 1
        assert files[0].startswith('ip_tracker-')
        assert _routers_in(tmp_path / files[0]) == ['CLI Router']
        assert files[0] in capsys.readouterr().out

    def test_leaves_no_partial_files(self, app, tmp_path):
        app_module.cli(['backup', str(tmp_path)])
        assert not [f for f in os.listdir(tmp_path) if not f.endswith('.db')]

    def test_removes_backups_older_than_keep_days(self, app, tmp_path):
        old = tmp_path / 'ip_tracker-20200101-030000.db'
        recent = tmp_path / 'ip_tracker-20990101-030000.db'
        other = tmp_path / 'notes.txt'
        for f in (old, recent, other):
            f.write_text('x')
        ten_days_ago = time.time() - 10 * 86400
        os.utime(old, (ten_days_ago, ten_days_ago))
        os.utime(other, (ten_days_ago, ten_days_ago))

        assert app_module.cli(['backup', str(tmp_path), '--keep-days', '7']) == 0
        assert not old.exists()
        assert recent.exists()
        assert other.exists()

    def test_creates_the_directory(self, app, tmp_path):
        target = tmp_path / 'nested' / 'backups'
        assert app_module.cli(['backup', str(target)]) == 0
        assert any(f.endswith('.db') for f in os.listdir(target))

    def test_requires_a_directory(self, app, capsys):
        assert app_module.cli(['backup']) == 2

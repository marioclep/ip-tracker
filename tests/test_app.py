import pytest
from conftest import do_login
from models import Router, DhcpLease, PppSession, Ipv6Binding, AppSettings


class TestAuth:
    def test_login_page_renders(self, client):
        resp = client.get('/login')
        assert resp.status_code == 200
        assert b'IP Tracker' in resp.data

    def test_login_redirects_when_authenticated(self, client):
        do_login(client)
        resp = client.get('/login')
        assert resp.status_code == 302

    def test_protected_route_redirects_to_login(self, client):
        resp = client.get('/')
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']

    def test_login_success(self, client):
        resp = client.post('/login', data={
            'username': 'admin', 'password': 'admin'
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_login_wrong_password(self, client):
        resp = client.post('/login', data={
            'username': 'admin', 'password': 'wrong'
        })
        assert b'incorrectos' in resp.data

    def test_logout(self, client):
        do_login(client)
        resp = client.get('/logout', follow_redirects=False)
        assert resp.status_code == 302


class TestDashboard:
    def test_dashboard_renders(self, client):
        do_login(client)
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'Dashboard' in resp.data


class TestRouters:
    def test_router_crud(self, client, db):
        do_login(client)
        resp = client.post('/routers/add', data={
            'name': 'Test Router', 'host': '10.0.0.1',
            'username': 'admin', 'password': 'secret',
            'api_port': '8728', 'enabled': 'on',
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b'Test Router' in resp.data

        router = Router.query.first()
        assert router is not None
        assert router.name == 'Test Router'

    def test_router_toggle(self, client, db):
        do_login(client)
        db.session.add(Router(name='R1', host='10.0.0.1',
                              username='admin', password='x', enabled=True))
        db.session.commit()
        router = Router.query.first()
        client.post(f'/routers/{router.id}/toggle')
        db.session.expire_all()
        assert not Router.query.first().enabled

    def test_router_delete(self, client, db):
        do_login(client)
        client.post('/routers/add', data={
            'name': 'R1', 'host': '10.0.0.1',
            'username': 'admin', 'password': 'x',
        })
        router = Router.query.first()
        client.post(f'/routers/{router.id}/delete')
        assert Router.query.count() == 0

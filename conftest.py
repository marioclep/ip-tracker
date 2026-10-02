import os
import pytest

os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
os.environ.setdefault('SECRET_KEY', 'test-key')

TEST_PASSWORD = 'admin'

from app import app as flask_app
from models import db as _db, AppSettings


@pytest.fixture
def app():
    flask_app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SECRET_KEY='test-key',
        WTF_CSRF_ENABLED=False,
    )
    with flask_app.app_context():
        _db.create_all()
        AppSettings.get().set_password(TEST_PASSWORD)
        _db.session.commit()
        yield flask_app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def db(app):
    return _db


def do_login(client):
    client.post('/login', data={'username': 'admin', 'password': TEST_PASSWORD})

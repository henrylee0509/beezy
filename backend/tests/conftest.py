import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="bp_test_")
os.environ.update(BP_DATA_DIR=_tmp, BP_AI_MODE="mock", BP_PBKDF2_ITERATIONS="1000", BP_AUTOMATION_INTERVAL_SEC="0",
                  BP_SKIP_DEMO="1", MODEL_API_KEY="", META_API_KEY="")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest  # noqa: E402

from backend.app import config, db, web  # noqa: E402
from backend.app.seed import seed_demo  # noqa: E402

_counter = [0]
_state = {}


@pytest.fixture
def conn():
    """A fresh database seeded with the demo business for every test."""
    _counter[0] += 1
    config.DATABASE_PATH = os.path.join(_tmp, f"t{_counter[0]}.db")
    db.init_db()
    web.reset_rate_limits()
    with db.connect() as c:
        _state["biz"] = seed_demo(c)
        yield c


@pytest.fixture
def biz(conn):
    return _state["biz"]


@pytest.fixture
def client(conn):
    from starlette.testclient import TestClient
    from backend.app.main import app
    with TestClient(app) as tc:
        yield tc


def login(client, who="owner"):
    r = client.post("/api/auth/login", json={"email": f"{who}@brewbean.demo", "password": "demo1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture
def owner(client):
    return login(client, "owner")


class FakeCtx:
    def __init__(self, biz, role="OWNER"):
        from backend.app import security
        self.business_id, self.role, self.name, self.user_id = biz, role, "Test User", 1
        self._sec = security

    @property
    def actor(self):
        return f"{self.name} ({self.role})"

    def can(self, perm):
        return self._sec.has_perm(self.role, perm)

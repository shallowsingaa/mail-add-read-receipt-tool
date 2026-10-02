from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from io import BytesIO
import json
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from receipt.config import Settings, load_settings
from receipt.server import create_app
from receipt.store import RateLimited, Store


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(database=str(tmp_path / "test.db")), start_worker=False)


def new_link(client):
    response = client.post("/api/links", json={"notification_email": "reader@example.com", "note": "报价\n[测试]"})
    assert response.status_code == 201
    return response.json()


def test_real_transparent_png_and_every_get_head_is_durable(app):
    with TestClient(app) as client:
        link = new_link(client)
        path = "/pixel/" + link["id"] + ".png?a=1&a=2"
        response = client.get(path, headers=[("User-Agent", "scanner"), ("X-Duplicate", "one"), ("X-Duplicate", "two"), ("X-Forwarded-For", "8.8.8.8")])
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert "no-store" in response.headers["cache-control"]
        image = Image.open(BytesIO(response.content))
        image.load()
        assert image.size == (1, 1)
        assert image.convert("RGBA").getpixel((0, 0))[3] == 0
        head = client.head(path)
        assert head.status_code == 200 and head.content == b""
        assert int(head.headers["content-length"]) == len(response.content)
        assert client.get(path).status_code == 200
        assert client.post(path).status_code == 405
        store = app.state.store
        status = store.events(link["id"])
        assert status["counts"] == {"pending": 3}
        with store.connect() as db:
            rows = db.execute("SELECT details FROM events ORDER BY created").fetchall()
        details = json.loads(rows[0][0])
        assert details["raw_query_string"] == "a=1&a=2"
        assert details["effective_source_ip"] == "testclient"
        assert not details["peer_is_trusted_proxy"]
        assert [v for k, v in details["headers"] if k == "x-duplicate"] == ["one", "two"]
        assert details["received_at"].endswith("+08:00")
        assert json.loads(rows[1][0])["method"] == "HEAD"


def test_separate_management_secret_stop_and_delete(app):
    with TestClient(app) as client:
        link = new_link(client)
        other = new_link(client)
        route = f"/api/links/{link['id']}"
        pixel = f"/pixel/{link['id']}.png"
        assert link["id"] != other["id"]
        assert client.get(route).status_code == 404
        assert client.post(route + "/stop", headers={"X-Management-Token": other["management_token"]}).status_code == 404
        auth = {"X-Management-Token": link["management_token"]}
        assert client.get(pixel).status_code == 200
        assert client.post(route + "/stop", headers=auth).status_code == 200
        stopped = client.get(route, headers=auth).json()
        assert stopped["active"] is False
        assert stopped["counts"] == {"cancelled": 1}
        assert client.get(pixel).status_code == 404
        assert client.post(route + "/retry", headers=auth).status_code == 409
        assert client.delete(route, headers=auth).status_code == 200
        with app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM events WHERE link_id=?", (link["id"],)).fetchone()[0] == 0
        assert client.get(route, headers=auth).status_code == 404


def test_rate_limit_atomic_with_concurrent_creations(tmp_path):
    store = Store(str(tmp_path / "limit.db"))

    def create(_):
        try:
            return store.create("a@example.com", "", "same-ip", 3, 3600)
        except RateLimited:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create, range(12)))
    assert sum(result is not None for result in results) == 3
    assert store.create("a@example.com", "", "other-ip", 3, 3600)


def test_trusted_proxy_and_creation_http_limit(tmp_path):
    settings = Settings(database=str(tmp_path / "proxy.db"), trusted_proxies=("127.0.0.1/32",), create_limit=1)
    app = create_app(settings, start_worker=False)
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        payload = {"notification_email": "a@example.com"}
        a = client.post("/api/links", json=payload, headers={"X-Forwarded-For": "1.2.3.4"})
        assert a.status_code == 201
        assert client.post("/api/links", json=payload, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 429
        assert client.post("/api/links", json=payload, headers={"X-Forwarded-For": "1.2.3.5"}).status_code == 201
        client.get(f"/pixel/{a.json()['id']}.png", headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4"})
        with app.state.store.connect() as db:
            details = json.loads(db.execute("SELECT details FROM events").fetchone()[0])
        assert details["connection_ip"] == "127.0.0.1"
        assert details["effective_source_ip"] == "1.2.3.4"


def test_invalid_input_does_not_create_links(app):
    with TestClient(app) as client:
        for email in ("not email", "a@example.com\r\nBcc: b@example.com"):
            assert client.post("/api/links", json={"notification_email": email}).status_code == 422
        assert client.post("/api/links", json={"notification_email": "a@example.com", "note": "x" * 501}).status_code == 422


def test_expiration_lease_recovery_retention_and_manual_retry(app):
    store = app.state.store
    link, _ = store.create("a@example.com", "", "ip", 10, 3600)
    event_id = store.record(link, {"method": "GET"}, 72)
    event = store.claim(300)
    assert event["id"] == event_id and store.claim(300) is None
    with store.connect() as db:
        db.execute("UPDATE events SET lease_until=? WHERE id=?", (time.time() - 1, event_id))
    store.maintain(90)
    recovered = store.claim(300)
    assert recovered["attempts"] == 2
    store.finish(recovered, "ConnectionRefusedError")
    assert store.events(link)["counts"] == {"pending": 1}
    assert store.claim(300) is None
    with store.connect() as db:
        db.execute("UPDATE events SET deadline=? WHERE id=?", (time.time() - 1, event_id))
    store.maintain(90)
    assert store.events(link)["counts"] == {"failed": 1}
    assert store.retry(link, 72) == 1
    store.finish(store.claim(300))
    old_failed = store.record(link, {}, 72)
    with store.connect() as db:
        db.execute("UPDATE events SET created=?", (time.time() - 91 * 86400,))
        db.execute("UPDATE events SET status='failed' WHERE id=?", (old_failed,))
    store.maintain(90)
    assert store.events(link)["counts"] == {"failed": 1}
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == 1


def test_config_example_and_secret_override(monkeypatch):
    monkeypatch.setenv("RECEIPT_SMTP_PASSWORD", "test-secret")
    settings = load_settings("server.example.toml")
    assert settings.smtp_password == "test-secret"
    assert "test-secret" not in repr(settings)
    assert settings.smtp_min_interval == 2
    with pytest.raises(ValueError):
        replace(settings, public_base_url="https://example.com/?injected=1")


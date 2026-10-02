"""Exercise the isolated HTTPS -> API -> SQLite -> SMTP deployment."""
import argparse
from email import policy
from email.parser import BytesParser
from io import BytesIO
import json
from pathlib import Path
import ssl
import time

import httpx
from PIL import Image

stage = Path(__file__).resolve().parent.parent / ".docker-test"
parser = argparse.ArgumentParser()
parser.add_argument("--after-restart", action="store_true")
args = parser.parse_args()
context = ssl.create_default_context(cafile=str(stage / "certs/fullchain.pem"))

with httpx.Client(base_url="https://localhost:18443", verify=context, trust_env=False, timeout=10) as client:
    assert client.get("/health").json() == {"status": "ok", "smtp_configured": True}
    if args.after_restart:
        link = json.loads((stage / "link.json").read_text(encoding="utf-8"))
        path = f"/api/links/{link['id']}"
        auth = {"X-Management-Token": link["management_token"]}
        status = client.get(path, headers=auth).json()
        assert status["active"] and status["counts"] == {"sent": 3}, status
        assert client.post(path + "/stop", headers=auth).status_code == 200
        assert client.get(f"/pixel/{link['id']}.png").status_code == 404
        assert client.get(path, headers=auth).json()["counts"] == {"sent": 3}
        assert client.delete(path, headers=auth).status_code == 200
        assert client.get(path, headers=auth).status_code == 404
        print("PASS: link/outbox persisted across container restart; stop and deletion work through HTTPS")
    else:
        response = client.post("/api/links", json={"notification_email": "receiver@example.com", "note": "Docker Linux 集成测试"})
        assert response.status_code == 201, response.text
        link = response.json()
        (stage / "link.json").write_text(json.dumps(link), encoding="utf-8")
        path = f"/pixel/{link['id']}.png?a=1&a=2"
        headers = {"User-Agent": "docker-debug-test", "X-Forwarded-For": "203.0.113.123"}
        for method in ("GET", "HEAD", "GET"):
            image = client.request(method, path, headers=headers)
            assert image.status_code == 200, image.text
            assert "no-store" in image.headers["cache-control"]
            if method == "GET":
                pixel = Image.open(BytesIO(image.content)).convert("RGBA")
                assert pixel.size == (1, 1) and pixel.getpixel((0, 0))[3] == 0
            else:
                assert image.content == b""
        assert client.post(path).status_code == 405
        management = f"/api/links/{link['id']}"
        assert client.get(management).status_code == 404
        auth = {"X-Management-Token": link["management_token"]}
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            status = client.get(management, headers=auth).json()
            if status["counts"] == {"sent": 3}:
                break
            time.sleep(0.1)
        assert status["counts"] == {"sent": 3}, status
        messages = []
        for file in (stage / "mail").glob("*.eml"):
            message = BytesParser(policy=policy.default).parsebytes(file.read_bytes())
            if link["id"] in message.get_content():
                messages.append(message)
        assert len(messages) == 3, len(messages)
        details = []
        for message in messages:
            assert message["To"] == "receiver@example.com"
            assert message["From"] == "sender@example.com"
            body = message.get_content()
            info = json.loads(body[body.index("{\n"):])
            assert info["connection_ip"] == "172.30.98.2"
            assert info["peer_is_trusted_proxy"] is True
            assert info["effective_source_ip"] != "203.0.113.123"
            assert info["raw_query_string"] == "a=1&a=2"
            assert ["user-agent", "docker-debug-test"] in info["headers"]
            details.append(info)
        assert sorted(info["method"] for info in details) == ["GET", "GET", "HEAD"]
        redirect = httpx.get("http://localhost:18080/health", trust_env=False)
        assert redirect.status_code == 308
        print("PASS: trusted HTTPS certificate, transparent PNG, 3 requests -> 3 SMTP emails, request details, proxy spoof protection, HTTP redirect")

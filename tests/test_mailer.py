import asyncio
from email import policy
from email.parser import BytesParser
import json
import socketserver
import threading
import time

from fastapi.testclient import TestClient

from receipt.config import Settings
from receipt.mailer import run_worker
from receipt.store import Store
from receipt.server import create_app


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 localhost test SMTP\r\n")
        data = []
        receiving = False
        while line := self.rfile.readline():
            if receiving:
                if line == b".\r\n":
                    self.server.messages.append(b"".join(data))
                    self.wfile.write(b"250 queued\r\n")
                    receiving = False
                else:
                    data.append(line[1:] if line.startswith(b"..") else line)
                continue
            command = line.upper()
            if command.startswith((b"EHLO", b"HELO")):
                self.wfile.write(b"250-localhost\r\n250 SIZE 1000000\r\n")
            elif command.startswith(b"DATA"):
                self.wfile.write(b"354 send message\r\n")
                receiving = True
                data = []
            elif command.startswith(b"QUIT"):
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


def test_api_get_and_head_deliver_two_independent_real_smtp_messages(tmp_path):
    smtp = socketserver.ThreadingTCPServer(("127.0.0.1", 0), SMTPHandler)
    smtp.messages = []
    thread = threading.Thread(target=smtp.serve_forever, daemon=True)
    thread.start()
    settings = Settings(database=str(tmp_path / "smtp.db"), smtp_host="127.0.0.1",
                        smtp_port=smtp.server_address[1], smtp_security="plain",
                        smtp_from="sender@example.com", worker_interval=0.01, smtp_min_interval=0.01)
    try:
        app = create_app(settings)
        with TestClient(app) as client:
            response = client.post("/api/links", json={"notification_email": "receiver@example.com", "note": "报价\nBcc: ignored@example.com"})
            assert response.status_code == 201
            link = response.json()["id"]
            for method in ("GET", "HEAD"):
                assert client.request(method, f"/pixel/{link}.png", headers={"User-Agent": "test-agent"}).status_code == 200
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and app.state.store.events(link)["counts"] != {"sent": 2}:
                time.sleep(0.02)
            assert app.state.store.events(link)["counts"] == {"sent": 2}
        assert len(smtp.messages) == 2
        for raw in smtp.messages:
            message = BytesParser(policy=policy.default).parsebytes(raw)
            assert message["To"] == "receiver@example.com"
            assert message["From"] == "sender@example.com"
            assert message["Bcc"] is None
            assert message["Auto-Submitted"] == "auto-generated"
            assert "test-agent" in message.get_content()
            assert "报价" in message.get_content()
    finally:
        smtp.shutdown()
        smtp.server_close()
        thread.join()


def test_worker_persists_delivery_failure_without_losing_event(tmp_path):
    settings = Settings(database=str(tmp_path / "failure.db"), smtp_host="unused",
                        smtp_from="sender@example.com", worker_interval=0.01, smtp_min_interval=0.01)
    store = Store(settings.database)
    link, _ = store.create("a@example.com", "", "ip", 20, 3600)
    store.record(link, {}, 72)
    calls = []

    def fail(event, settings):
        calls.append(event["id"])
        raise ConnectionRefusedError("do not persist a password")

    async def exercise():
        stop = asyncio.Event()
        task = asyncio.create_task(run_worker(store, settings, stop, sender=fail))
        try:
            for _ in range(100):
                events = store.events(link)["events"]
                if events[0]["last_error"]:
                    break
                await asyncio.sleep(0.01)
            assert events[0]["status"] == "pending"
            assert events[0]["last_error"] == "ConnectionRefusedError"
            assert len(calls) == 1
        finally:
            stop.set()
            await task
    asyncio.run(exercise())

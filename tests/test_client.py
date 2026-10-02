import asyncio
import json

import httpx
from textual.widgets import Input, TextArea

from receipt.client import ReceiptApp
from receipt.config import Settings
from receipt.server import create_app


def test_tui_create_persist_reload_stop_retry_and_delete(tmp_path):
    config = tmp_path / "client.toml"
    config.write_text('[client]\napi_base_url="http://test"\n', encoding="utf-8")
    server = create_app(Settings(database=str(tmp_path / "app.db")), start_worker=False)
    transport = httpx.ASGITransport(app=server)

    async def exercise():
        app = ReceiptApp(config, transport=transport)
        async with app.run_test(size=(120, 45)) as pilot:
            app.query_one("#email", Input).value = "owner@example.com"
            app.query_one("#note", Input).value = "测试邮件"
            await pilot.click("#create")
            await app.workers.wait_for_complete()
            assert len(app.history.entries) == 1
            assert '<img src="http://127.0.0.1:8000/pixel/' in app.query_one("#html", TextArea).text
            entry = app.history.entries[0]
            server.state.store.record(entry["id"], {}, 72)
            with server.state.store.connect() as db:
                db.execute("UPDATE events SET status='failed'")
            app.manage("retry", dict(entry))
            await app.workers.wait_for_complete()
            assert server.state.store.events(entry["id"])["counts"] == {"pending": 1}
            app.manage("refresh", dict(entry))
            await app.workers.wait_for_complete()
            app.manage("stop", dict(entry))
            await app.workers.wait_for_complete()
            assert app.history.entries[0]["active"] is False
        reloaded = ReceiptApp(config, transport=transport)
        async with reloaded.run_test(size=(120, 45)) as pilot:
            assert reloaded.history.entries[0]["active"] is False
            reloaded.current = reloaded.history.entries[0]
            reloaded.query_one("#html", TextArea).load_text(reloaded.current["html"])
            await pilot.click("#delete")
            assert len(reloaded.history.entries) == 1
            await pilot.pause(0.4)
            await pilot.click("#delete")
            await reloaded.workers.wait_for_complete()
            assert reloaded.history.entries == []
            assert json.loads((tmp_path / "history.json").read_text(encoding="utf-8")) == []
    asyncio.run(exercise())


def test_tui_network_failure_is_visible_and_leaves_no_history(tmp_path):
    config = tmp_path / "client.toml"

    async def unavailable(request):
        raise httpx.ConnectError("connection unavailable")

    async def exercise():
        app = ReceiptApp(config, transport=httpx.MockTransport(unavailable))
        async with app.run_test(size=(120, 45)):
            app.query_one("#email", Input).value = "owner@example.com"
            app.create_link()
            await app.workers.wait_for_complete()
            assert app.history.entries == []
            assert "ConnectError" in str(app.query_one("#status").render())
    asyncio.run(exercise())

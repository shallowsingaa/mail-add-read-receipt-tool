"""Windows-friendly TUI; never sends mail directly."""
import argparse
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tomllib
from urllib.parse import urlsplit

import httpx
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, TextArea


def application_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()


def load_client_config(path):
    if not path.exists():
        path.write_text('[client]\napi_base_url = "http://127.0.0.1:8000"\ntimeout_seconds = 15\n', encoding="utf-8")
    with path.open("rb") as handle:
        config = tomllib.load(handle)["client"]
    base = config["api_base_url"].rstrip("/")
    url = urlsplit(base)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment):
        raise ValueError("api_base_url 必须是 HTTP(S) 地址，不能含路径、用户名或查询参数")
    timeout = float(config.get("timeout_seconds", 15))
    if timeout <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    return {"api_base_url": base, "timeout_seconds": timeout}


class History:
    def __init__(self, path):
        self.path = path
        self.entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(self.entries, list):
            raise ValueError("history.json 格式错误；请备份后修复，程序不会覆盖它")

    def save(self):
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, self.path)


def windows_clipboard(text):
    """Copy actual Unicode text to the Windows clipboard, independent of terminal OSC52."""
    from ctypes import wintypes
    user32, kernel32 = ctypes.WinDLL("user32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    data = (text + "\0").encode("utf-16-le")
    memory = kernel32.GlobalAlloc(0x0002, len(data))
    if not memory:
        raise ctypes.WinError(ctypes.get_last_error())
    transferred = False
    try:
        pointer = kernel32.GlobalLock(memory)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(pointer, data, len(data))
        kernel32.GlobalUnlock(memory)
        if not user32.OpenClipboard(None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not user32.EmptyClipboard() or not user32.SetClipboardData(13, memory):
                raise ctypes.WinError(ctypes.get_last_error())
            transferred = True
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(memory)


class ReceiptApp(App):
    TITLE = "邮件图片请求通知工具"
    BINDINGS = [("ctrl+q", "quit", "退出"), ("ctrl+y", "copy_html", "复制 HTML")]
    CSS = """
    Screen { background: $surface; }
    #body { padding: 1 2; }
    Label { margin-top: 1; }
    Input, Select { margin-bottom: 1; }
    #html { height: 6; margin-top: 1; }
    .buttons { height: auto; margin-top: 1; }
    Button { margin-right: 1; }
    #status { margin-top: 1; min-height: 3; }
    #counts { margin-top: 1; }
    """

    def __init__(self, config_path, transport=None):
        super().__init__()
        self.config_path = Path(config_path)
        self.config = load_client_config(self.config_path)
        self.history = History(self.config_path.parent / "history.json")
        # Verify local persistence before creating remote links.
        self.history.save()
        self.current = None
        self.transport = transport
        self.delete_armed = None
        self.api_busy = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="body"):
            yield Static(f"API：{self.config['api_base_url']}\n配置：{self.config_path}", markup=False)
            yield Label("通知邮箱")
            yield Input(placeholder="you@example.com", id="email")
            yield Label("备注（可选）")
            yield Input(placeholder="用于辨认这封邮件，最多 500 字", id="note", max_length=500)
            yield Button("创建追踪链接", variant="primary", id="create")
            yield Label("本地历史（包含管理凭据，请妥善保管）")
            yield Select([], prompt="选择历史记录", id="history")
            yield Label("复制下方 HTML 到邮件的源码模式")
            yield TextArea("", read_only=True, id="html")
            with Horizontal(classes="buttons"):
                yield Button("复制 HTML", id="copy")
                yield Button("刷新状态", id="refresh")
                yield Button("重新投递失败通知", id="retry")
            with Horizontal(classes="buttons"):
                yield Button("停用链接", variant="warning", id="stop")
                yield Button("删除记录（点两次）", variant="error", id="delete")
            yield Static("", id="counts", markup=False)
            yield Static("图片请求不等于确定已读；邮箱代理和预加载可能触发访问。", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        self.update_history()

    def update_history(self):
        options = []
        for item in self.history.entries:
            state = "已停用" if not item.get("active", True) else "启用"
            label = f"[{state}] {item.get('created', '')} {item.get('note') or item.get('notification_email', '')} ({item['id'][:8]})"
            options.append((Text(label), item["id"]))
        select = self.query_one("#history", Select)
        select.set_options(options)
        if self.current and any(e["id"] == self.current["id"] for e in self.history.entries):
            select.value = self.current["id"]

    def show_status(self, text):
        self.query_one("#status", Static).update(text)

    def set_busy(self, busy):
        self.api_busy = busy
        for widget_id in ("create", "refresh", "retry", "stop", "delete"):
            self.query_one("#" + widget_id, Button).disabled = busy
        self.query_one("#history", Select).disabled = busy

    @on(Select.Changed, "#history")
    def selected(self, event):
        if event.value is Select.BLANK:
            return
        self.current = next((e for e in self.history.entries if e["id"] == event.value), None)
        self.delete_armed = None
        self.query_one("#html", TextArea).load_text(self.current["html"] if self.current else "")
        self.query_one("#counts", Static).update("")

    @on(Button.Pressed)
    def button_pressed(self, event):
        action = event.button.id
        if action == "copy":
            self.action_copy_html()
        elif action == "create":
            self.create_link()
        elif action in {"refresh", "stop", "retry", "delete"}:
            if not self.current:
                self.show_status("请先创建或选择一条历史记录。")
                return
            if action == "delete" and self.delete_armed != self.current["id"]:
                self.delete_armed = self.current["id"]
                self.show_status("删除将使图片失效，并删除服务端请求记录及本地管理凭据。再次点击删除以确认。")
                return
            self.manage(action, dict(self.current))

    def action_copy_html(self):
        text = self.query_one("#html", TextArea).text
        if not text:
            self.show_status("还没有可复制的 HTML。")
            return
        try:
            if sys.platform == "win32":
                windows_clipboard(text)
            else:
                self.copy_to_clipboard(text)
        except OSError as exc:
            self.show_status(f"剪贴板不可用：{exc}。可以直接选择并复制源码。")
        else:
            self.show_status("HTML 已复制。请粘贴到邮件编辑器的源码模式。")

    async def api(self, method, base, path, **kwargs):
        async with httpx.AsyncClient(timeout=self.config["timeout_seconds"], transport=self.transport,
                                     follow_redirects=False) as client:
            response = await client.request(method, base + path, **kwargs)
            response.raise_for_status()
            return response.json()

    def failure(self, exc):
        if isinstance(exc, httpx.HTTPStatusError):
            self.show_status(f"服务端返回 HTTP {exc.response.status_code}：{exc.response.text[:500]}")
        else:
            self.show_status(f"操作失败（{type(exc).__name__}）：{exc}")

    @work(group="api")
    async def create_link(self):
        if self.api_busy:
            return
        email = self.query_one("#email", Input).value.strip()
        note = self.query_one("#note", Input).value.strip()
        if not email:
            self.show_status("请输入通知邮箱。")
            return
        self.set_busy(True)
        self.show_status("正在创建……")
        try:
            result = await self.api("POST", self.config["api_base_url"], "/api/links",
                                    json={"notification_email": email, "note": note})
            entry = {**result, "notification_email": email, "note": note, "active": True,
                     "api_base_url": self.config["api_base_url"], "created": datetime.now().astimezone().isoformat(timespec="seconds")}
            self.history.entries.insert(0, entry)
            self.current = entry
            self.query_one("#html", TextArea).load_text(result["html"])
            self.update_history()
            self.history.save()
            self.show_status("已创建并保存。复制 HTML 到邮件源码模式。")
        except Exception as exc:
            self.failure(exc)
        finally:
            self.set_busy(False)

    @work(group="api")
    async def manage(self, action, entry):
        if self.api_busy:
            return
        self.set_busy(True)
        self.show_status("正在请求服务端……")
        path = f"/api/links/{entry['id']}"
        method = "GET" if action == "refresh" else "DELETE" if action == "delete" else "POST"
        if action in {"stop", "retry"}:
            path += "/" + action
        try:
            result = await self.api(method, entry["api_base_url"], path,
                                    headers={"X-Management-Token": entry["management_token"]})
            if action == "delete":
                self.history.entries = [e for e in self.history.entries if e["id"] != entry["id"]]
                self.current = None
                self.query_one("#html", TextArea).load_text("")
                self.history.save()
                self.update_history()
                self.show_status("服务端和本地记录已删除。")
            elif action in {"refresh", "stop"}:
                for item in self.history.entries:
                    if item["id"] == entry["id"]:
                        item["active"] = result["active"]
                self.history.save()
                self.update_history()
                counts = result.get("counts", {})
                self.query_one("#counts", Static).update("状态：" + ("启用" if result["active"] else "已停用") + "\n通知队列：" + json.dumps(counts, ensure_ascii=False))
                failures = [e for e in result.get("events", []) if e["status"] == "failed"]
                self.show_status("链接已停用。" if action == "stop" else
                                 f"状态已刷新。最近 100 次请求中失败 {len(failures)} 次。" +
                                 (f" 最近错误：{failures[0]['last_error']}" if failures else ""))
            else:
                self.show_status(f"已重新排队 {result['requeued']} 封失败通知。")
        except Exception as exc:
            self.failure(exc)
        finally:
            self.set_busy(False)


def main():
    parser = argparse.ArgumentParser(description="邮件图片请求通知 TUI")
    parser.add_argument("--config", type=Path, default=application_directory() / "client.toml")
    parser.add_argument("--smoke-test", action="store_true", help="检查打包运行时，不启动 TUI、不联网")
    args = parser.parse_args()
    if args.smoke_test:
        print("receipt-client runtime OK")
        return
    try:
        app = ReceiptApp(args.config.resolve())
        app.run()
    except Exception as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        if getattr(sys, "frozen", False) and sys.stdin.isatty():
            input("按回车退出……")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

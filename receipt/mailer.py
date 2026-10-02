import asyncio
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import json
import logging
import smtplib
import ssl
import time
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

log = logging.getLogger(__name__)


def build_message(event, settings):
    message = EmailMessage()
    # Never put untrusted note/headers in mail headers.
    message["Subject"] = f"图片请求通知 / Pixel request [{event['id']}]"
    message["From"] = settings.smtp_from
    message["To"] = event["email"]
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=urlsplit(settings.public_base_url).hostname.encode("idna").decode("ascii"))
    message["Auto-Submitted"] = "auto-generated"
    message["X-Auto-Response-Suppress"] = "All"
    timestamp = datetime.fromtimestamp(event["created"], ZoneInfo(settings.timezone)).isoformat()
    details = json.loads(event["details"])
    message.set_content(
        "服务器收到一次追踪图片请求。这不证明收件人已阅读正文。\n"
        "邮箱代理、预加载或自动扫描也可能触发请求。\n\n"
        f"事件编号：{event['id']}\n链接编号：{event['link_id']}\n"
        f"请求时间：{timestamp}\n备注：{event['note']}\n\n"
        "本次服务端收到的完整请求信息（JSON）：\n"
        + json.dumps(details, ensure_ascii=False, indent=2)
    )
    return message


def send_notification(event, settings):
    context = ssl.create_default_context()
    hostname = urlsplit(settings.public_base_url).hostname.encode("idna").decode("ascii")
    if settings.smtp_security == "ssl":
        connection = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                     timeout=settings.smtp_timeout, context=context, local_hostname=hostname)
    else:
        connection = smtplib.SMTP(settings.smtp_host, settings.smtp_port,
                                  timeout=settings.smtp_timeout, local_hostname=hostname)
    with connection as smtp:
        smtp.ehlo()
        if settings.smtp_security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(build_message(event, settings), from_addr=settings.smtp_from, to_addrs=[event["email"]])


async def run_worker(store, settings, stop, sender=send_notification):
    last_send = 0.0
    next_maintenance = 0.0
    if not settings.smtp_enabled:
        log.warning("SMTP is not configured; request events will queue until their retry deadline")
    while not stop.is_set():
        try:
            if time.monotonic() >= next_maintenance:
                await asyncio.to_thread(store.maintain, settings.retention_days)
                next_maintenance = time.monotonic() + 60
            if settings.smtp_enabled and time.monotonic() - last_send >= settings.smtp_min_interval:
                event = await asyncio.to_thread(store.claim, max(300, settings.smtp_timeout * 20))
                if event:
                    try:
                        await asyncio.to_thread(sender, event, settings)
                    except Exception as exc:
                        # Persist useful delivery diagnostics without echoing credentials or request data.
                        code = getattr(exc, "smtp_code", None)
                        error = type(exc).__name__ + (f" (SMTP {code})" if code else "")
                        await asyncio.to_thread(store.finish, event, error)
                        log.warning("Notification %s failed: %s", event["id"], error)
                    else:
                        await asyncio.to_thread(store.finish, event)
                    last_send = time.monotonic()
        except Exception:
            log.exception("Outbox iteration failed; retrying")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_interval)
        except TimeoutError:
            pass

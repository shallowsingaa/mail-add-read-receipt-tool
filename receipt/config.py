from dataclasses import dataclass, field
from pathlib import Path
import ipaddress
import os
import tomllib
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    public_base_url: str = "http://127.0.0.1:8000"
    database: str = "data/receipt.sqlite3"
    timezone: str = "Asia/Shanghai"
    trusted_proxies: tuple[str, ...] = ()
    create_limit: int = 20
    create_window_seconds: int = 3600
    retention_days: int = 90
    retry_hours: int = 72
    worker_interval: float = 1.0
    smtp_min_interval: float = 2.0
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_username: str = ""
    smtp_password: str = field(default="", repr=False)
    smtp_from: str = ""
    smtp_timeout: float = 15.0

    def __post_init__(self):
        from zoneinfo import ZoneInfo
        url = urlsplit(self.public_base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or url.path not in {"", "/"}):
            raise ValueError("public_base_url must be an HTTP(S) origin, without a path")
        ZoneInfo(self.timezone)
        for network in self.trusted_proxies:
            ipaddress.ip_network(network)
        for name in ("create_limit", "create_window_seconds", "retention_days", "retry_hours"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.smtp_security not in {"starttls", "ssl", "plain"}:
            raise ValueError("smtp.security must be starttls, ssl or plain")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("Invalid SMTP port")
        if min(self.worker_interval, self.smtp_min_interval, self.smtp_timeout) <= 0:
            raise ValueError("Worker and SMTP intervals must be positive")
        if bool(self.smtp_host) != bool(self.smtp_from):
            raise ValueError("Set smtp.host and smtp.from_address together")
        if self.smtp_from:
            from email_validator import validate_email
            address = validate_email(self.smtp_from, check_deliverability=False)
            if not address.ascii_email:
                raise ValueError("SMTP sender must have an ASCII mailbox")
            object.__setattr__(self, "smtp_from", address.ascii_email)

    @property
    def smtp_enabled(self):
        return bool(self.smtp_host and self.smtp_from)


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path or os.getenv("RECEIPT_CONFIG", "server.toml"))
    with path.open("rb") as handle:
        config = tomllib.load(handle)
    service = dict(config.get("service", {}))
    smtp = dict(config.get("smtp", {}))
    if "trusted_proxies" in service:
        service["trusted_proxies"] = tuple(service["trusted_proxies"])
    if "from_address" in smtp:
        smtp["from"] = smtp.pop("from_address")
    # Optional secret override for container secret/environment integrations.
    if "RECEIPT_SMTP_PASSWORD" in os.environ:
        smtp["password"] = os.environ["RECEIPT_SMTP_PASSWORD"]
    return Settings(**service, **{f"smtp_{key}": value for key, value in smtp.items()})

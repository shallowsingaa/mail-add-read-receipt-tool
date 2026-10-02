"""Catch the reserved-port conflict seen on hosts with an existing proxy."""
from pathlib import Path

import yaml


def test_default_compose_can_coexist_with_proxy_on_80_and_443():
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    conflicts = []
    for name, service in compose["services"].items():
        if service.get("profiles"):
            continue
        for port in service.get("ports", []):
            published = str(port["published"]) if isinstance(port, dict) else str(port).split(":")[-2]
            if published in {"80", "443"}:
                conflicts.append(f"{name} binds host port {published}")
    assert not conflicts, "Existing OpenResty owns these ports: " + ", ".join(conflicts)

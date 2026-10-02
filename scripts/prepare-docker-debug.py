"""Prepare an isolated local Compose deployment with a test SMTP sink."""
from pathlib import Path
import yaml

root = Path(__file__).resolve().parent.parent
stage = root / ".docker-test"
stage.mkdir(exist_ok=True)
(stage / "certs").mkdir(exist_ok=True)
(stage / "mail").mkdir(exist_ok=True)
config = (root / "server.example.toml").read_text(encoding="utf-8")
config = config.replace("https://receipt.example.com", "https://localhost:18443")
config = config.replace("172.30.97.1", "172.30.98.2")
config = config.replace('host = ""', 'host = "smtp-sink"')
config = config.replace('from_address = ""', 'from_address = "sender@example.com"')
config = config.replace('security = "starttls"', 'security = "plain"')
config = config.replace("port = 587", "port = 2525")
(stage / "server.toml").write_text(config, encoding="utf-8")
(stage / "nginx.conf").write_text(
    (root / "deploy/nginx.conf").read_text(encoding="utf-8").replace("receipt.example.com", "localhost"), encoding="utf-8")
(stage / "smtp.py").write_text('''import socketserver
from pathlib import Path
import uuid

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 localhost test SMTP\\r\\n")
        data = []
        receiving = False
        while line := self.rfile.readline():
            if receiving:
                if line == b".\\r\\n":
                    Path("/mail", str(uuid.uuid4()) + ".eml").write_bytes(b"".join(data))
                    self.wfile.write(b"250 queued\\r\\n")
                    receiving = False
                else:
                    data.append(line[1:] if line.startswith(b"..") else line)
                continue
            command = line.upper()
            if command.startswith((b"EHLO", b"HELO")):
                self.wfile.write(b"250-localhost\\r\\n250 SIZE 1000000\\r\\n")
            elif command.startswith(b"DATA"):
                data = []
                receiving = True
                self.wfile.write(b"354 send data\\r\\n")
            elif command.startswith(b"QUIT"):
                self.wfile.write(b"221 bye\\r\\n")
                return
            else:
                self.wfile.write(b"250 ok\\r\\n")

with socketserver.ThreadingTCPServer(("0.0.0.0", 2525), Handler) as server:
    server.serve_forever()
''', encoding="utf-8")

compose = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))

def mount(source, target, read_only=True):
    return {"type": "bind", "source": str(source.resolve()), "target": target, "read_only": read_only}

app = compose["services"]["app"]
app["build"] = str(root)
app["volumes"][0] = mount(stage / "server.toml", "/config/server.toml")
app["networks"]["receipt"]["ipv4_address"] = "172.30.98.3"
app.pop("ports", None)
nginx = compose["services"]["nginx"]
nginx.pop("profiles", None)
nginx["ports"] = ["127.0.0.1:18080:80", "127.0.0.1:18443:443"]
nginx["volumes"] = [mount(stage / "nginx.conf", "/etc/nginx/conf.d/default.conf"), mount(stage / "certs", "/etc/nginx/tls")]
nginx["networks"]["receipt"]["ipv4_address"] = "172.30.98.2"
compose["networks"]["receipt"]["ipam"]["config"] = [{"subnet": "172.30.98.0/24"}]
compose["services"]["smtp-sink"] = {
    "image": "python:3.12-slim", "command": ["python", "/debug/smtp.py"],
    "volumes": [mount(stage / "smtp.py", "/debug/smtp.py"), mount(stage / "mail", "/mail", False)],
    "networks": {"receipt": {"ipv4_address": "172.30.98.4"}},
}
(stage / "compose.yaml").write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
app_only = yaml.safe_load((root / "compose.yaml").read_text(encoding="utf-8"))
app_only_config = (root / "server.example.toml").read_text(encoding="utf-8").replace(
    "https://receipt.example.com", "http://127.0.0.1:18000")
(stage / "app-only.toml").write_text(app_only_config, encoding="utf-8")
app_only["services"]["app"]["build"] = str(root)
app_only["services"]["app"]["image"] = "receipt-debug-app:latest"
app_only["services"]["app"]["volumes"][0] = mount(stage / "app-only.toml", "/config/server.toml")
app_only["services"]["nginx"]["volumes"] = [
    mount(stage / "nginx.conf", "/etc/nginx/conf.d/default.conf"), mount(stage / "certs", "/etc/nginx/tls")]
(stage / "app-only.yaml").write_text(yaml.safe_dump(app_only, sort_keys=False), encoding="utf-8")
print(f"Prepared {stage}; generate a localhost TLS certificate before starting Compose")

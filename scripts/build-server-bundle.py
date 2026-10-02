"""Bundle only distribution sources, never runtime credentials or databases."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parent.parent
output = root / "dist" / "mail-read-receipt-server.zip"
output.parent.mkdir(exist_ok=True)
files = [root / name for name in (
    "Dockerfile", "compose.yaml", "compose.proxy-network.yaml", ".dockerignore", "pyproject.toml",
    "requirements-server.lock", "requirements-client.lock", "requirements-dev.lock",
    "server.example.toml", "client.example.toml", "client_entry.py",
    "README.md", "VALIDATION.md", "LICENSE",
)]
files.extend((root / "receipt").glob("*.py"))
files.extend((root / "deploy").glob("*"))
files.extend((root / "scripts").glob("*.py"))
files.extend((root / "scripts").glob("*.ps1"))
files.extend((root / "tests").glob("*.py"))
files.extend((root / "docs").glob("*.md"))
with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
    for path in files:
        if path.is_file():
            archive.write(path, path.relative_to(root).as_posix())
print(output)

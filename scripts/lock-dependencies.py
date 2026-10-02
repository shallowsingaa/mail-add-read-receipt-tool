"""Record tested dependency closures from an installed Python 3.12 environment."""
from importlib.metadata import distribution, version
from pathlib import Path
from packaging.requirements import Requirement

roots = {
    "server": ["fastapi", "uvicorn", "email-validator", "tzdata"],
    "client": ["textual", "httpx"],
    "dev": ["pytest", "pillow", "pyinstaller", "pyyaml"],
}
for group, names in roots.items():
    seen = set()
    visited = set()
    todo = [(name, frozenset()) for name in names]
    while todo:
        name, extras = todo.pop()
        name = name.lower().replace("_", "-")
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        seen.add(name)
        for raw in distribution(name).requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or any(
                requirement.marker.evaluate({"extra": extra}) for extra in {"", *extras}
            ):
                todo.append((requirement.name, frozenset(requirement.extras)))
    lines = []
    for name in sorted(seen):
        marker = "; sys_platform == 'win32'" if name in {"colorama", "pefile", "pywin32-ctypes"} else ""
        lines.append(f"{name}=={version(name)}{marker}")
    Path(f"requirements-{group}.lock").write_text(
        "# Python 3.12; tested dependency versions\n" + "\n".join(lines) + "\n", encoding="utf-8")

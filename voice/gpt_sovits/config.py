"""GPT-SoVITS installation discovery and per-install configuration.

YUMENO never bundles a GPT-SoVITS distribution. It detects an existing
installation (official release or integrated package), validates that it can
serve the HTTP API (``api_v2.py`` / ``api.py``), and remembers the path in
``data/gpt_sovits/config.json``.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_API_PORT = 17005
COMMON_INSTALL_HINTS = (
    "GPT_SOVITS_HOME",
    "GPT_SOVITS_DIR",
)


@dataclass
class InstallationProbe:
    install_dir: Path | None
    python_path: Path | None
    api_script: Path | None
    api_version: str | None
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.install_dir and self.python_path and self.api_script)


def probe_installation(install_dir: Path | None) -> InstallationProbe:
    """Validate a GPT-SoVITS install directory and locate its python + API
    entrypoint. Returns a probe that is ``ok`` when everything needed to
    launch the API service is present."""

    if install_dir is None:
        return InstallationProbe(None, None, None, None, "未配置 GPT-SoVITS 安装目录")
    install_dir = install_dir.resolve()
    if not install_dir.is_dir():
        return InstallationProbe(install_dir, None, None, None, f"目录不存在：{install_dir}")

    python_path = install_dir / "runtime" / "python.exe"
    if not python_path.is_file():
        python_path = install_dir / "python.exe"
    if not python_path.is_file():
        python_path = None

    api_v2 = install_dir / "api_v2.py"
    api_v1 = install_dir / "api.py"
    if api_v2.is_file():
        api_script, api_version = api_v2, "v2"
    elif api_v1.is_file():
        api_script, api_version = api_v1, "v1"
    else:
        api_script, api_version = None, None

    if not python_path:
        return InstallationProbe(
            install_dir, None, api_script, api_version,
            "未找到 Python 运行环境（runtime\\python.exe 或 python.exe）",
        )
    if not api_script:
        return InstallationProbe(
            install_dir, python_path, None, None,
            "未找到 API 入口（api_v2.py 或 api.py）",
        )
    return InstallationProbe(install_dir, python_path, api_script, api_version)


def detect_install_dir(roots: tuple[Path, ...] | None = None) -> Path | None:
    """Scan environment variables and common drive locations for a
    GPT-SoVITS installation."""

    for env_name in COMMON_INSTALL_HINTS:
        value = os.getenv(env_name)
        if value:
            candidate = Path(value)
            if probe_installation(candidate).ok:
                return candidate
    roots = roots or tuple(Path(d) for d in ("D:/", "C:/"))
    for root in roots:
        if not root.is_dir():
            continue
        try:
            candidates = sorted(root.glob("GPT-SoVITS*"))
        except OSError:
            continue
        for candidate in candidates:
            if candidate.is_dir() and probe_installation(candidate).ok:
                return candidate
    return None


class GPTSoVITSConfig:
    """Persisted GPT-SoVITS configuration (install dir + API port)."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.config_path = self.project_root / "data" / "gpt_sovits" / "config.json"

    def values(self) -> dict:
        defaults = {"install_dir": None, "api_port": DEFAULT_API_PORT, "download_url": None}
        if not self.config_path.is_file():
            return defaults
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return defaults
        install_dir = data.get("install_dir")
        download_url = data.get("download_url")
        return {
            "install_dir": str(install_dir) if install_dir else None,
            "api_port": int(data.get("api_port") or DEFAULT_API_PORT),
            "download_url": str(download_url) if download_url else None,
        }

    def save(
        self,
        install_dir: str | None = None,
        api_port: int | None = None,
        download_url: str | None = None,
    ) -> dict:
        values = self.values()
        if install_dir is not None:
            values["install_dir"] = install_dir.strip() or None
        if api_port is not None:
            values["api_port"] = int(api_port)
        if download_url is not None:
            values["download_url"] = download_url.strip() or None
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)
        return values

    def probe(self) -> InstallationProbe:
        return probe_installation(
            Path(self.values()["install_dir"]) if self.values()["install_dir"] else None
        )

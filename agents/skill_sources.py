"""技能源拉取层：GitHub zipball / URL 直连下载与安全解压定位。"""

import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_EXTRACT_BYTES = 25 * 1024 * 1024
MAX_FILES = 500
DOWNLOAD_TIMEOUT = 60
USER_AGENT = "YUMENO"

_GITHUB_URL_PATTERNS = (
    re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"),
    re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/tree/([^/]+)(?:/(.*))?$"),
    re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/blob/([^/]+)(?:/(.*))?$"),
    re.compile(r"^https?://raw\.githubusercontent\.com/([^/]+)/([^/]+?)/([^/]+)(?:/(.*))?$"),
)


def parse_github_url(url: str) -> tuple[str, str, str]:
    """解析 GitHub 链接 → (repo, ref, path)。非法/非 GitHub 链接抛 RuntimeError。"""
    text = url.strip()
    for pattern in _GITHUB_URL_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        groups = match.groups()
        owner, name = groups[0], groups[1]
        if len(groups) == 2:
            return f"{owner}/{name}", "main", ""
        return f"{owner}/{name}", groups[2], (groups[3] or "").strip("/")
    raise RuntimeError(f"无法识别的 GitHub 链接: {url}")


def _open_url(url: str, timeout: int = DOWNLOAD_TIMEOUT):
    """直连下载（绕开假代理），返回可读的文件类对象。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return opener.open(request, timeout=timeout)


def _download(url: str, destination: Path) -> None:
    try:
        with _open_url(url, DOWNLOAD_TIMEOUT) as response, destination.open("wb") as target:
            downloaded = 0
            while chunk := response.read(64 * 1024):
                downloaded += len(chunk)
                if downloaded > MAX_ZIP_BYTES:
                    raise RuntimeError("下载压缩包超过 25MB 上限")
                target.write(chunk)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"下载失败（{url}）：{exc}") from exc


def _is_unsafe_zip_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    return (
        name.startswith("/")
        or ":" in name
        or any(part in ("", ".", "..") for part in parts)
    )


def extract_zip_safely(archive: Path, workdir: Path) -> None:
    """安全解压 zip（路径/符号链接/大小/文件数校验），不做目录定位。"""
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            infos = bundle.infolist()
            if len(infos) > MAX_FILES:
                raise RuntimeError(f"文件数超过 {MAX_FILES} 上限")
            total = 0
            for info in infos:
                if info.is_dir():
                    continue
                if _is_unsafe_zip_path(info.filename):
                    raise RuntimeError(f"非法路径: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError(f"不支持符号链接: {info.filename}")
                total += info.file_size
            if total > MAX_EXTRACT_BYTES:
                raise RuntimeError("解压后超过 25MB 上限")
            bundle.extractall(workdir)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("不是有效的 zip 文件") from exc


def extract_skill_archive(archive: Path, workdir: Path) -> Path:
    """安全解压 zip 并定位唯一含 SKILL.md 的技能目录（适用于 URL/上传包）。"""
    extract_zip_safely(archive, workdir)
    candidates = []
    if (workdir / "SKILL.md").is_file():
        candidates.append(workdir)
    candidates.extend(
        child
        for child in sorted(workdir.iterdir())
        if child.is_dir() and (child / "SKILL.md").is_file()
    )
    if not candidates:
        raise RuntimeError("未找到含 SKILL.md 的技能目录")
    if len(candidates) > 1:
        raise RuntimeError("存在多个技能目录，无法确定安装目标")
    return candidates[0]


def fetch_github_skill(repo: str, path: str, ref: str, workdir: Path) -> Path:
    """下载 GitHub zipball 并定位 <repo>-<ref>/<path> 技能目录。"""
    if "/" not in repo:
        raise RuntimeError("repo 必须为 owner/repo 格式")
    if not path.strip():
        raise RuntimeError("path 不能为空")
    url = f"https://github.com/{repo}/archive/refs/heads/{ref}.zip"
    archive = workdir / "pkg.zip"
    _download(url, archive)
    extracted = workdir / "extracted"
    extract_zip_safely(archive, extracted)
    top = next((child for child in extracted.iterdir() if child.is_dir()), None)
    if top is None:
        raise RuntimeError("压缩包内没有目录")
    located = top / path
    if not (located / "SKILL.md").is_file():
        raise RuntimeError(f"未在 {repo}/{path} 找到 SKILL.md")
    return located


def fetch_url_skill(url: str, workdir: Path) -> Path:
    """下载 URL（zip 或单文件 SKILL.md）并返回技能目录。"""
    archive = workdir / "download"
    _download(url, archive)
    if archive.suffix.lower() == ".zip" or archive.read_bytes()[:2] == b"PK":
        return extract_skill_archive(archive, workdir / "extracted")
    skill_dir = workdir / "skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, skill_dir / "SKILL.md")
    return skill_dir

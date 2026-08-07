"""GPT-SoVITS distribution installer.

YUMENO does not bundle the 14 GB+ distribution. A user-provided archive URL
(zip of an integrated package, e.g. the nvidia50 build) is downloaded with
progress/resume, extracted into ``runtime/gpt_sovits``, and registered in the
GPT-SoVITS config. Optional project-bundled patches under
``runtime/gpt_sovits_patches`` are copied over the extracted tree afterwards
(used for files that have no public download source).
"""

import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from voice.gpt_sovits.config import GPTSoVITSConfig, probe_installation


# Content inside the integrated package that the project never uses. The
# previous hand-trimmed build (10.1 GB) worked without all of these, so they
# are removed after extraction to keep a fresh install at ~10 GB:
#   - tools/asr + tools/uvr5: the pack's own ASR/vocal-separation, replaced by
#     the app's Qwen3-ASR and HT-Demucs separator;
#   - v1/v2/v3/v4 pretrained variants: only used when switching the engine to
#     those versions, which YUMENO never does (it uses v2Pro + trained assets).
_SLIM_PATHS = (
    "tools/asr",
    "tools/uvr5",
    "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained",
    "GPT_SoVITS/pretrained_models/gsv-v4-pretrained",
    "GPT_SoVITS/pretrained_models/s1bert25hz-2kh-longer-epoch=68e-step=50232.ckpt",
    "GPT_SoVITS/pretrained_models/s2D488k.pth",
    "GPT_SoVITS/pretrained_models/s2G488k.pth",
    "GPT_SoVITS/pretrained_models/s2Gv3.pth",
)


class GPTSoVITSInstallCancelled(RuntimeError):
    pass


class _InstallState:
    def __init__(self) -> None:
        self.installing = False
        self.cancel_requested = threading.Event()
        self.phase = "idle"
        self.current_file = ""
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.detail = ""
        self.error = ""
        self.started_at: float | None = None
        self.source = ""

    def set_progress(
        self,
        phase: str,
        current_file: str = "",
        downloaded: int = 0,
        total: int = 0,
        detail: str = "",
    ) -> None:
        self.phase = phase
        self.current_file = current_file
        self.downloaded_bytes = downloaded
        self.total_bytes = total
        self.detail = detail

    def snapshot(self) -> dict:
        elapsed = time.monotonic() - self.started_at if self.started_at else 0
        transferring = self.phase in ("download", "extracting")
        speed = (
            self.downloaded_bytes / elapsed
            if transferring and elapsed > 0 and self.downloaded_bytes
            else 0
        )
        remaining = self.total_bytes - self.downloaded_bytes
        return {
            "installing": self.installing,
            "cancelling": self.installing and self.cancel_requested.is_set(),
            "phase": self.phase,
            "current_file": self.current_file,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "detail": self.detail,
            "progress_percent": round(self.downloaded_bytes * 100 / self.total_bytes) if self.total_bytes else None,
            "download_speed_bytes": round(speed),
            "eta_seconds": round(remaining / speed) if speed > 0 and remaining > 0 else 0 if self.total_bytes and remaining <= 0 else None,
            "elapsed_seconds": round(elapsed),
            "error": self.error,
            "source": self.source,
        }


class GPTSoVITSInstallManager:
    def __init__(self, project_root: Path, config: GPTSoVITSConfig) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config
        self.install_dir = self.project_root / "runtime" / "gpt_sovits"
        self.patches_dir = self.project_root / "runtime" / "gpt_sovits_patches"
        self.download_dir = self.project_root / "data" / "gpt_sovits" / "downloads"
        self.state = _InstallState()
        self._lock = threading.Lock()

    def status(self) -> dict:
        probe = probe_installation(self.install_dir if self.install_dir.is_dir() else None)
        snapshot = self.state.snapshot()
        return {
            **snapshot,
            "installed": probe.ok,
            "install_dir": str(self.install_dir),
            "patches_dir": str(self.patches_dir) if self.patches_dir.is_dir() else "",
            "external_configured": bool(self.config.values()["install_dir"]),
            "download_url": self.config.values().get("download_url"),
        }

    def remove_install(self) -> dict:
        """Delete the project-bundled GPT-SoVITS engine and clear the config."""

        if self.state.installing:
            raise RuntimeError("请先取消正在进行的下载")
        if self.install_dir.is_dir():
            shutil.rmtree(self.install_dir)
        if self.config.values()["install_dir"] == str(self.install_dir):
            self.config.save(install_dir=None)
        return self.status()

    def start_install(self, url: str | None) -> bool:
        with self._lock:
            if self.state.installing:
                return False
            url = (url or "").strip()
            if not url:
                raise ValueError("请提供整合包下载地址（zip）")
            self.state.installing = True
            self.state.cancel_requested.clear()
            self.state.error = ""
            self.state.started_at = time.monotonic()
            self.state.set_progress("preparing", "", 0, 0)
        threading.Thread(
            target=self._install,
            args=(url,),
            name="gpt-sovits-install",
            daemon=True,
        ).start()
        return True

    def cancel_install(self) -> bool:
        if not self.state.installing:
            return False
        self.state.cancel_requested.set()
        self.state.phase = "cancelling"
        return True

    def _install(self, url: str) -> None:
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            filename = Path(urlsplit(url).path).name or "gpt-sovits.zip"
            archive = self.download_dir / filename
            self.state.set_progress("download", filename, 0, 0, detail="准备下载…")
            self._download(url, archive)
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            self.state.set_progress("extracting", filename, 0, 0, detail="准备解压…")
            self._extract(archive)
            self.state.set_progress("patching", "", 0, 0, detail="准备应用项目补丁…")
            self._apply_patches()
            self.state.set_progress("cleaning", "", 0, 0, detail="清理冗余文件…")
            self._slim_install()
            self.state.set_progress("verifying", "", 0, 0, detail="校验安装完整性…")
            probe = probe_installation(self.install_dir)
            if not probe.ok:
                raise RuntimeError(probe.error or "解压后未找到可用的 GPT-SoVITS 安装")
            self.config.save(install_dir=str(self.install_dir))
            # 安装成功后删除下载缓存；失败时保留以便断点续传/重试。
            archive.unlink(missing_ok=True)
            archive.with_suffix(archive.suffix + ".part").unlink(missing_ok=True)
            self.state.set_progress("complete", "", 0, 0)
        except GPTSoVITSInstallCancelled:
            self.state.error = ""
            self.state.set_progress("idle", "", 0, 0)
        except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
            if self.state.cancel_requested.is_set():
                self.state.error = ""
                self.state.set_progress("idle", "", 0, 0)
            else:
                self.state.error = str(exc)
                self.state.phase = "error"
        finally:
            self.state.installing = False
            self.state.cancel_requested.clear()
            self.state.started_at = None

    def _download(self, url: str, destination: Path) -> None:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        attempt = 0
        while True:
            attempt += 1
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            resume = destination.with_suffix(destination.suffix + ".part").stat().st_size if destination.with_suffix(destination.suffix + ".part").is_file() else 0
            headers = {"User-Agent": "YUMENO"}
            if resume > 0:
                headers["Range"] = f"bytes={resume}-"
            request = urllib.request.Request(url, headers=headers)
            partial = destination.with_suffix(destination.suffix + ".part")
            self.state.source = urlsplit(url).hostname or "下载源"
            try:
                with opener.open(request, timeout=60) as response, partial.open("ab" if resume else "wb") as target:
                    total = resume + int(response.headers.get("Content-Length") or 0)
                    downloaded = resume
                    self.state.set_progress(
                        "download",
                        destination.name,
                        downloaded,
                        total,
                        detail=f"来源：{self.state.source}",
                    )
                    while chunk := response.read(1024 * 1024):
                        if self.state.cancel_requested.is_set():
                            raise GPTSoVITSInstallCancelled()
                        target.write(chunk)
                        downloaded += len(chunk)
                        self.state.set_progress(
                            "download",
                            destination.name,
                            downloaded,
                            total,
                            detail=f"来源：{self.state.source}",
                        )
                os.replace(partial, destination)
                return
            except GPTSoVITSInstallCancelled:
                raise
            except (OSError, urllib.error.URLError) as exc:
                if attempt > 2:
                    raise RuntimeError(f"下载 {destination.name} 失败（{self.state.source}）：{exc}") from exc
                time.sleep(2)

    def _extract(self, archive: Path) -> None:
        if archive.suffix.lower() == ".7z":
            self._extract_7z(archive)
            return
        self._extract_zip(archive)

    @staticmethod
    def _safe_member_path(root: Path, name: str) -> Path:
        """Resolve an archive member under ``root``, rejecting traversal."""

        parts = [part for part in name.replace("\\", "/").split("/") if part not in ("", ".")]
        if any(part == ".." or ":" in part for part in parts):
            raise RuntimeError(f"整合包包含不安全的文件路径：{name}")
        return root.joinpath(*parts)

    def _extract_zip(self, archive: Path) -> None:
        temporary = self.install_dir.with_name(self.install_dir.name + ".tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                base = None
                for member in members:
                    if member.is_dir():
                        continue
                    first = member.filename.split("/", 1)[0]
                    if first:
                        base = first
                        break
                total = sum(member.file_size for member in members if not member.is_dir())
                done = 0
                for member in members:
                    if self.state.cancel_requested.is_set():
                        raise GPTSoVITSInstallCancelled()
                    target = self._safe_member_path(temporary, member.filename)
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, target.open("wb") as destination:
                        while chunk := source.read(1024 * 1024):
                            if self.state.cancel_requested.is_set():
                                raise GPTSoVITSInstallCancelled()
                            destination.write(chunk)
                            done += len(chunk)
                            self.state.set_progress(
                                "extracting",
                                member.filename,
                                done,
                                total,
                                detail=f"解压 {member.filename}",
                            )
                if base and all(
                    member.filename == base or member.filename.startswith(f"{base}/")
                    for member in members
                ):
                    extracted = temporary / base
                    if extracted.is_dir():
                        shutil.move(str(extracted), str(self.install_dir))
                        return
                self.install_dir.mkdir(parents=True, exist_ok=True)
                for child in temporary.iterdir():
                    shutil.move(str(child), str(self.install_dir))
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _extract_7z(self, archive: Path) -> None:
        """Extract a .7z integrated package with live progress via 7-Zip."""

        # Project-bundled extractor first so a fresh install works even after
        # the previous engine directory (which also contained 7zr.exe) was
        # moved away or deleted; fall back to the engine's own copy, then to
        # a system 7-Zip.
        bundled_7zr = (
            self.project_root / "runtime" / "tools" / "7zr.exe",
            self.install_dir / "tools" / "7zr.exe",
        )
        seven_zip = next(
            (str(candidate) for candidate in bundled_7zr if candidate.is_file()),
            shutil.which("7z") or shutil.which("7za"),
        )
        if not seven_zip:
            raise RuntimeError(
                "检测到 7z 整合包，但未找到 7-Zip；请安装 7-Zip 后重试，"
                "或手动解压到 runtime/gpt_sovits 后重新检测。"
            )
        temporary = self.install_dir.with_name(self.install_dir.name + ".tmp")
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir(parents=True, exist_ok=True)
        total = archive.stat().st_size if archive.is_file() else 0
        try:
            process = subprocess.Popen(
                [seven_zip, "x", str(archive), f"-o{temporary}", "-y", "-bb1", "-bsp1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            pending_name = ""
            for line in process.stdout:
                if self.state.cancel_requested.is_set():
                    process.kill()
                    raise GPTSoVITSInstallCancelled()
                match = re.match(r"\s*(\d{1,3})\s*%", line)
                if match:
                    percent = int(match.group(1))
                    done = int(total * percent / 100) if total else 0
                    self.state.set_progress(
                        "extracting",
                        pending_name or Path(archive).name,
                        done,
                        total,
                        detail=f"解压 {percent}%",
                    )
                    pending_name = ""
                    continue
                stripped = line.strip()
                if stripped.startswith("- "):
                    pending_name = stripped[2:].strip()
            returncode = process.wait()
            if returncode != 0:
                raise RuntimeError(f"7-Zip 解压失败（退出码 {returncode}）")
            children = list(temporary.iterdir())
            if len(children) == 1 and children[0].is_dir():
                shutil.move(str(children[0]), str(self.install_dir))
            else:
                self.install_dir.mkdir(parents=True, exist_ok=True)
                for child in children:
                    shutil.move(str(child), str(self.install_dir))
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _apply_patches(self) -> None:
        if not self.patches_dir.is_dir():
            self.state.set_progress("patching", "", 1, 1, detail="无需应用补丁")
            return
        files = [source for source in self.patches_dir.rglob("*") if source.is_file()]
        for index, source in enumerate(files, start=1):
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            relative = source.relative_to(self.patches_dir)
            target = self.install_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            self.state.set_progress(
                "patching",
                str(relative),
                index,
                len(files),
                detail=f"补丁 {index}/{len(files)}",
            )
        # The integrated runtime ships a users.pth pointing at the packager's
        # absolute path; rewrite it to this installation so a downloaded copy
        # is self-contained.
        users_pth = self.install_dir / "runtime" / "Lib" / "site-packages" / "users.pth"
        if users_pth.parent.is_dir():
            base = str(self.install_dir)
            users_pth.write_text(
                "\n".join(
                    [
                        base,
                        f"{base}/GPT_SoVITS/BigVGAN",
                        f"{base}/tools",
                        f"{base}/tools/asr",
                        f"{base}/GPT_SoVITS",
                        f"{base}/tools/uvr5",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

    def _slim_install(self) -> None:
        """Remove package content the project never uses (see _SLIM_PATHS).

        Runs after patches are applied. Missing targets are ignored so the
        list stays compatible with future pack versions.
        """

        targets = [self.install_dir / rel for rel in _SLIM_PATHS]
        for index, target in enumerate(targets, start=1):
            if self.state.cancel_requested.is_set():
                raise GPTSoVITSInstallCancelled()
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.is_file():
                target.unlink(missing_ok=True)
            self.state.set_progress(
                "cleaning",
                str(target.relative_to(self.install_dir)),
                index,
                len(targets),
                detail=f"清理冗余文件 {index}/{len(targets)}",
            )

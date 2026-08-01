import ctypes
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

from pymilvus import MilvusClient
from sqlalchemy import create_engine, text

from app.database import database_url
from ingestion.local_embedding.resources import LocalEmbeddingResourceManager
from settings import Settings
from voice.asr.install import ASRResourceManager
from voice.tts.install import TTSResourceManager

_STARTED_AT = time.monotonic()
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ASR_PORT = 17004
_ATTU_PORT = 17003


def _app_version() -> str:
    try:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except Exception:
        return "dev"


APP_VERSION = _app_version()


def _system_label() -> str:
    system = platform.system()
    if system != "Windows":
        release = platform.release()
        return f"{system} {release}" if release else system
    try:
        build = int(platform.version().split(".")[2])
    except (ValueError, IndexError):
        return "Windows"
    if build >= 22000:
        return "Windows 11"
    if build >= 10240:
        return "Windows 10"
    return "Windows"


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _memory_status() -> dict:
    if os.name != "nt":
        return {}
    try:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return {}
        gb = 1024**3
        return {
            "total_gb": round(status.ullTotalPhys / gb, 1),
            "available_gb": round(status.ullAvailPhys / gb, 1),
            "commit_charge_gb": round((status.ullTotalPageFile - status.ullAvailPageFile) / gb, 1),
            "commit_limit_gb": round(status.ullTotalPageFile / gb, 1),
        }
    except Exception:
        return {}


def _disk_status() -> dict:
    result = {}
    try:
        usage = shutil.disk_usage(_PROJECT_ROOT)
        result["project"] = {
            "drive": _PROJECT_ROOT.anchor,
            "total_gb": round(usage.total / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
        }
    except Exception:
        result["project"] = {}
    try:
        system_root = os.getenv("SystemDrive") or f"{Path(sys.executable).drive}\\"
        usage = shutil.disk_usage(system_root)
        result["system"] = {
            "drive": system_root[:2],
            "total_gb": round(usage.total / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
        }
    except Exception:
        result["system"] = {}
    return result


def _port_open(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.settimeout(0.4)
            return sock.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def _gpu_status() -> dict | None:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,driver_version,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0:
            return None
        parts = [part.strip() for part in result.stdout.strip().split(",")]
        if len(parts) < 4:
            return None
        return {
            "name": parts[0],
            "driver": parts[1],
            "vram_total_gb": round(float(parts[2]) / 1024, 1),
            "vram_used_gb": round(float(parts[3]) / 1024, 1),
        }
    except Exception:
        return None


def _config_summary(settings: Settings) -> dict:
    base_url = settings.openai_base_url.lower()
    if "openai.com" in base_url:
        provider = "openai"
    elif "deepseek" in base_url:
        provider = "deepseek"
    elif "dashscope.aliyuncs.com" in base_url:
        provider = "qwen"
    else:
        provider = "custom" if base_url else "未配置"
    return {
        "llm_provider": provider,
        "openai_base_url": settings.openai_base_url,
        "openai_model": settings.openai_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "embedding_device": settings.embedding_device,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "web_search_provider": settings.web_search_provider,
        "web_search_enabled": settings.enable_web_fallback,
    }


def _resource_status(builder) -> dict:
    try:
        return builder()
    except Exception as exc:
        return {"ready": False, "error": str(exc)}


def get_system_status() -> dict:
    settings = Settings.load()
    result = {"mysql": "unavailable", "milvus": "unavailable"}

    engine = create_engine(
        database_url(settings),
        pool_pre_ping=True,
        pool_timeout=2,
        connect_args={"connect_timeout": 2},
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1")).fetchone()
            try:
                version_row = connection.execute(text("SELECT VERSION()")).fetchone()
                result["mysql_version"] = str(version_row[0] or "") if version_row else ""
            except Exception:
                result["mysql_version"] = ""
        result["mysql"] = "ok"
    except Exception:
        result["mysql"] = "unavailable"
        result["mysql_version"] = ""
    finally:
        engine.dispose()

    connection_args = {"uri": settings.milvus_uri, "timeout": 2}
    if settings.milvus_user and settings.milvus_password:
        connection_args.update(
            {"user": settings.milvus_user, "password": settings.milvus_password}
        )
    client = None
    try:
        client = MilvusClient(**connection_args)
        collections = client.list_collections(timeout=2)
        result["milvus"] = (
            "ok" if settings.collection_name in collections else "collection_missing"
        )
    except Exception:
        result["milvus"] = "unavailable"
    finally:
        if client is not None:
            client.close()

    try:
        milvus_port = urlsplit(settings.milvus_uri).port or 19530
    except ValueError:
        milvus_port = 19530
    ports = {
        "app": settings.app_port,
        "mysql": settings.mysql_port,
        "milvus": milvus_port,
        "attu": _ATTU_PORT,
        "asr": _ASR_PORT,
    }

    result.update(
        {
            "app": {
                "version": APP_VERSION,
                "python": sys.version.split()[0],
                "system": _system_label(),
                "system_build": platform.version(),
                "workspace_id": settings.workspace_id,
                "uptime_seconds": int(time.monotonic() - _STARTED_AT),
            },
            "memory": _memory_status(),
            "disk": _disk_status(),
            "ports": ports,
            "ports_listening": {name: _port_open(port) for name, port in ports.items()},
            "gpu": _gpu_status(),
            "config": _config_summary(settings),
            "collection": settings.collection_name,
            "resources": {
                "embedding": _resource_status(
                    lambda: LocalEmbeddingResourceManager(settings.project_root).status()
                ),
                "asr": _resource_status(
                    lambda: ASRResourceManager(settings.project_root).status()
                ),
                "tts": _resource_status(
                    lambda: TTSResourceManager(settings.project_root).status()
                ),
            },
        }
    )
    return result

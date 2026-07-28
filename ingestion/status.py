from pymilvus import MilvusClient
from sqlalchemy import create_engine, text

from app.database import database_url
from settings import Settings


def get_system_status() -> dict[str, str]:
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
            connection.execute(text("SELECT 1"))
        result["mysql"] = "ok"
    except Exception:
        result["mysql"] = "unavailable"
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
    return result

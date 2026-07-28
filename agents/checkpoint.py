from dataclasses import dataclass

import pymysql
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

from settings import Settings


@dataclass
class CheckpointResource:
    saver: PyMySQLSaver

    def close(self) -> None:
        pass


def create_mysql_checkpointer(settings: Settings) -> CheckpointResource:
    def connection_factory():
        return pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            autocommit=True,
        )

    saver = PyMySQLSaver(connection_factory)
    saver.setup()
    return CheckpointResource(saver=saver)


def delete_persona_checkpoints(settings: Settings, persona_id: str) -> None:
    """Delete every conversation thread owned by one persona."""

    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        autocommit=False,
    )
    try:
        prefix = f"{persona_id}:%"
        with connection.cursor() as cursor:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cursor.execute(f"DELETE FROM {table} WHERE thread_id LIKE %s", (prefix,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

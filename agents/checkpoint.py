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

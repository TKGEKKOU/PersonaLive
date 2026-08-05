from dataclasses import dataclass
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

from settings import Settings


@dataclass
class CheckpointResource:
    saver: SqliteSaver
    conn: sqlite3.Connection

    def close(self) -> None:
        self.conn.close()


def create_sqlite_checkpointer(settings: Settings) -> CheckpointResource:
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.sqlite_path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return CheckpointResource(saver=saver, conn=conn)


def delete_persona_checkpoints(settings: Settings, persona_id: str) -> None:
    """Delete every conversation thread owned by one persona."""

    connection = sqlite3.connect(str(settings.sqlite_path))
    try:
        prefix = f"{persona_id}:%"
        with connection:
            for table in ("writes", "checkpoints"):
                connection.execute(
                    f"DELETE FROM {table} WHERE thread_id LIKE ?", (prefix,)
                )
    finally:
        connection.close()

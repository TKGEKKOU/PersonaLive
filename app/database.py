from collections.abc import Generator

from fastapi import Request
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from settings import Settings


class Base(DeclarativeBase):
    pass


def database_url(settings: Settings) -> str:
    return f"sqlite:///{settings.sqlite_path.as_posix()}"


def build_engine(settings: Settings) -> Engine:
    settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        database_url(settings),
        connect_args={"check_same_thread": False},
    )
    # WAL 模式:读写并发更稳,桌面端单用户场景足够
    with engine.begin() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def upgrade_persona_schema(engine: Engine) -> None:
    """Add Task 2 columns for deployments created before persona types existed."""
    if engine.dialect.name != "mysql":
        return
    inspector = inspect(engine)
    additions = {
        "personas": {"persona_type": "VARCHAR(32) NOT NULL DEFAULT 'knowledge_expert'"},
        "persona_drafts": {
            "persona_type": "VARCHAR(32) NOT NULL DEFAULT 'knowledge_expert'",
            "candidates_json": "JSON NULL",
            "selected_candidate_id": "VARCHAR(64) NULL",
        },
    }
    with engine.begin() as connection:
        for table, columns in additions.items():
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        connection.execute(text("UPDATE persona_drafts SET candidates_json = JSON_ARRAY() WHERE candidates_json IS NULL"))


def get_session(request: Request) -> Generator[Session, None, None]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session

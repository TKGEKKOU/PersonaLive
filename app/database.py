from collections.abc import Generator

from fastapi import Request
from sqlalchemy import URL, Engine, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from settings import Settings


class Base(DeclarativeBase):
    pass


def database_url(settings: Settings) -> URL:
    return URL.create(
        "mysql+pymysql",
        username=settings.mysql_user,
        password=settings.mysql_password or None,
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
    )


def build_engine(settings: Settings) -> Engine:
    return create_engine(database_url(settings), pool_pre_ping=True)


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

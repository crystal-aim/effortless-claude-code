import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

DB_URL = os.environ.get("CCM_DATABASE_URL", "sqlite:///./data.db")

_connect_args: dict = {}
if DB_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    DB_URL,
    connect_args=_connect_args,
    future=True,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def read_setting(key: str) -> Optional[str]:
    from app.models import Setting

    with session_scope() as db:
        row = db.get(Setting, key)
        return row.value if row else None


def write_setting(key: str, value: Optional[str]) -> None:
    from app.models import Setting

    with session_scope() as db:
        row = db.get(Setting, key)
        if value is None:
            if row is not None:
                db.delete(row)
            return
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value

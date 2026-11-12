from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


def get_session():
    with Session(engine) as session:
        yield session

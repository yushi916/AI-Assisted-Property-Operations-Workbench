from pathlib import Path

from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def create_db_engine(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(db_path)),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(db_engine, "connect")
    def configure_connection(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA foreign_keys=ON").close()

    @event.listens_for(db_engine, "begin")
    def begin_transaction(connection):
        connection.exec_driver_sql("BEGIN")

    return db_engine


DATABASE_PATH = Path(__file__).resolve().parent / "data" / "property.db"
engine = create_db_engine(DATABASE_PATH)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

def get_db():
    with SessionLocal() as db:
        yield db
from alembic import context

import models  # Register the model tables in Base.metadata.
from database import Base, engine


target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=engine.url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_with_connection(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()

def run_sqlite_migrations(connection):
    # SQLite 必须在事务开始前调整外键设置。
    # 已由调用者开启的事务，仍由调用者管理。
    if connection.dialect.name != "sqlite" or connection.in_transaction():
        run_with_connection(connection)
        return

    raw = connection.connection.driver_connection
    previous = raw.execute("PRAGMA foreign_keys").fetchone()[0]
    raw.execute("PRAGMA foreign_keys=OFF")

    try:
        with connection.begin():
            run_with_connection(connection)
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise ValueError("迁移后外键核对失败，已回滚")
    finally:
        raw.execute(f"PRAGMA foreign_keys={previous}")
        if raw.execute("PRAGMA foreign_keys").fetchone()[0] != previous:
            connection.invalidate()
            raise RuntimeError("迁移连接的外键设置未能恢复")

def run_migrations_online():
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        run_sqlite_migrations(supplied_connection)
    else:
        with engine.connect() as connection:
            run_sqlite_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
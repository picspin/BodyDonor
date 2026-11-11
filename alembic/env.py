"""Alembic 环境：从 bodydonor.db 读取 URL（默认 sqlite:///./bodydonor.db），
target_metadata 取 bodydonor.models.Base.metadata（7 个核心实体 + License 实体）。

迁移连接经 _migration_sqlite_pragma 设 WAL + busy_timeout（FK=OFF，M5-e）；
应用连接仍用 _set_sqlite_pragma（FK=ON）。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from bodydonor.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只发 SQL，不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite ALTER 限制：批模式重建表
    )

    with context.begin_transaction():
        context.run_migrations()


def _migration_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """迁移连接 pragma：WAL + busy_timeout 同应用纪律，FK=OFF（见上）。"""
    from bodydonor.db import BUSY_TIMEOUT_MS

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA foreign_keys=OFF")
    cursor.close()


def run_migrations_online() -> None:
    """在线模式。URL 优先级：-x db_url / 环境变量 BODYDONOR_DB_URL > alembic.ini。"""
    url = context.get_x_argument(as_dictionary=True).get("db_url")
    if not url:
        url = config.get_main_option("sqlalchemy.url")

    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = url
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    # M5-e：迁移连接用 FK=OFF（应用连接仍用 _set_sqlite_pragma 的 FK=ON）。
    # 批模式重建被外键引用的表（DROP TABLE mask_versions，qc_reports 引用
    # 它）时 FK=ON 会报 IntegrityError；pragma 必须在 connect 时设（事务内
    # 改不了），且不能在 Connection 上 exec（会开隐式事务，SQLite
    # transactional_ddl=False 下 alembic 不提交，改动会被静默 ROLLBACK）。
    event.listen(engine, "connect", _migration_sqlite_pragma)

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

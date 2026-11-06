"""Alembic 环境：从 bodydonor.db 读取 URL（默认 sqlite:///./bodydonor.db），
target_metadata 取 bodydonor.models.Base.metadata（7 个核心实体 + License 实体）。

连接经 bodydonor.db._set_sqlite_pragma 设置 WAL + busy_timeout，与 connect() 同一纪律。
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from bodydonor.db import _set_sqlite_pragma
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


def run_migrations_online() -> None:
    """在线模式。URL 优先级：-x db_url / 环境变量 BODYDONOR_DB_URL > alembic.ini。"""
    url = context.get_x_argument(as_dictionary=True).get("db_url")
    if not url:
        url = config.get_main_option("sqlalchemy.url")

    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = url
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    event.listen(engine, "connect", _set_sqlite_pragma)

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

"""m7s3: decision_traces 增加 6 列（modality/teacher_id/distill_profile/
skip_reason/config_hash/env）

Revision ID: f8a3b9c2d1e4
Revises: a4f9e2c81d03
Create Date: 2026-11-15

S3 做法 6 号项：DecisionTrace 增列记录「该决策用到什么模态/哪个教师/什么
蒸馏档案、为什么跳过、什么配置哈希、什么环境」。6 列全部可空（nullable）——
已有 DecisionTrace 行必须能迁移（NOT NULL 无默认会卡存量行）；当前只有
qc_series 写入处能直接取到 modality 与 skip_reason，其余留 NULL，不为此
新造 plumbing（任务书 3）。

SQLite 不支持裸 ADD COLUMN 之外的 ALTER：用 batch_alter_table（影子表重建）。
downgrade 对称删列。
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8a3b9c2d1e4"
down_revision: str | Sequence[str] | None = "a4f9e2c81d03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 新列定义（upgrade 加 / downgrade 删；与 models.DecisionTrace 一致）
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("modality", sa.String(length=16)),
    ("teacher_id", sa.String(length=200)),
    ("distill_profile", sa.String(length=32)),
    ("skip_reason", sa.String(length=255)),
    ("config_hash", sa.String(length=64)),
    ("env", sa.String(length=32)),
)


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        sa.text("SELECT COUNT(*) FROM pragma_table_info(:table)"),
        {"table": table},
    ).scalar()
    return bool(row)


def _existing_columns(conn: Any, table: str) -> set[str]:
    rows = conn.execute(
        sa.text("SELECT name FROM pragma_table_info(:table)"),
        {"table": table},
    ).fetchall()
    return {r[0] for r in rows}


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    if not _table_exists(conn, "decision_traces"):
        # decision_traces 不存在（前置迁移未跑）——upgrade head 下不可能到这，
        # 防御性直接返回（不硬造表）
        return
    existing = _existing_columns(conn, "decision_traces")
    for name, col_type in _NEW_COLUMNS:
        if name in existing:
            continue
        with op.batch_alter_table("decision_traces") as batch:
            batch.add_column(sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    conn = op.get_bind()
    if not _table_exists(conn, "decision_traces"):
        return
    existing = _existing_columns(conn, "decision_traces")
    for name, _col_type in reversed(_NEW_COLUMNS):
        if name not in existing:
            continue
        with op.batch_alter_table("decision_traces") as batch:
            batch.drop_column(name)

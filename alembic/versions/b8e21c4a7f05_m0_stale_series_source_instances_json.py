"""m0 stale: series 表补 source_instances_json 列（JSON, nullable）

Revision ID: b8e21c4a7f05
Revises: e7f3a1c9d204
Create Date: 2026-11-11

背景（M5-REPORT §3.6 新发现，本迁移关闭）：
M0 迁移（3c17abaf19f7）建 series 表时缺 source_instances_json 列——该列是
M2-5(a) 加进 models.py 的（ingest.extract_source_instances 产、commit 落库、
export_seg 消费），但 M0 迁移从未同步，空库 create_all 与 upgrade head 的
schema 漂移。本迁移给 series 表补该列（JSON, nullable=True，与 models.py
Series.source_instances_json 一致），不改动已应用的 M0 迁移 hash。

开发库（bodydonor.db）已由 create_all 建列 + stamp/upgrade 到 e7f3a1c9d204，
本迁移对其为 no-op 语义的补列（列已存在时跳过——batch 模式下 add_column 对
已存在列会报错，故先查 PRAGMA）。
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e21c4a7f05"
down_revision: str | Sequence[str] | None = "e7f3a1c9d204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(conn: Any, table: str, column: str) -> bool:
    row = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM pragma_table_info(:table) WHERE name = :column"
        ),
        {"table": table, "column": column},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    if _column_exists(conn, "series", "source_instances_json"):
        # 开发库已由 create_all 建列：跳过（幂等，不重复补列）
        return
    with op.batch_alter_table("series", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source_instances_json", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("series", schema=None) as batch_op:
        batch_op.drop_column("source_instances_json")

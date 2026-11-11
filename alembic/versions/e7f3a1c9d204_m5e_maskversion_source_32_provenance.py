"""m5-e: MaskVersion.source String(16)->String(32) + provenance JSON 列

Revision ID: e7f3a1c9d204
Revises: 3c17abaf19f7
Create Date: 2026-11-11

背景（M5-PLAN §4/§5 M5-e，M4-REPORT §5.1 遗留）：
MaskVersion.source 是 String(16)，装不下 20 字符的 nninteractive_guided。
本迁移把列宽改为 String(32)，并新增 provenance JSON 列（nullable），
存放 {sessionId, baselineTeacherVersion, prompts[], acceptedClasses[]}
（+ submit-nni 附加的 targetLabel / correctionSidecar），prompts 可重放、
两类修正（human / nninteractive_guided）可分别统计。

SQLite 批模式（env.py render_as_batch=True）：ALTER 经批模式重建表。
legacy 别名 manual-correction-nninteractive / nninteractive 在门禁里保持
拒绝（ontology.mask_allowed_in_distill_training / distill_data
REJECTED_SOURCES，不动本迁移）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f3a1c9d204"
down_revision: str | Sequence[str] | None = "3c17abaf19f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("mask_versions", schema=None) as batch_op:
        batch_op.alter_column(
            "source",
            existing_type=sa.String(length=16),
            type_=sa.String(length=32),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column("provenance", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("mask_versions", schema=None) as batch_op:
        batch_op.drop_column("provenance")
        batch_op.alter_column(
            "source",
            existing_type=sa.String(length=32),
            type_=sa.String(length=16),
            existing_nullable=False,
        )

"""m6f: decision_traces 实体表（DecisionTrace，决策审计痕迹）

Revision ID: a4f9e2c81d03
Revises: b8e21c4a7f05
Create Date: 2026-11-12

M6-f：决策审计痕迹——决策在哪发生就在哪记一条（qc_scoring / gate_decision
两个阶段各记一条），append-only + human_outcome 事后补填（决策回流端点）。
字段见 models.DecisionTrace docstring。开发库（bodydonor.db）由 create_all
建表，本迁移对其为 no-op 语义（表已存在时跳过——幂等）。
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f9e2c81d03"
down_revision: str | Sequence[str] | None = "b8e21c4a7f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        sa.text("SELECT COUNT(*) FROM pragma_table_info(:table)"),
        {"table": table},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    if _table_exists(conn, "decision_traces"):
        # 开发库已由 create_all 建表：跳过（幂等）
        return
    op.create_table(
        "decision_traces",
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=64), nullable=True),
        sa.Column("series_id", sa.String(length=64), nullable=False),
        sa.Column("mask_version_id", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("pipeline_stage", sa.String(length=32), nullable=False),
        sa.Column("input_refs", sa.JSON(), nullable=True),
        sa.Column("process_signals", sa.JSON(), nullable=True),
        sa.Column("rules_triggered", sa.JSON(), nullable=True),
        sa.Column("gate_decision", sa.JSON(), nullable=True),
        sa.Column("human_outcome", sa.JSON(), nullable=True),
        sa.Column("calibration", sa.JSON(), nullable=True),
        sa.Column("review_task_id", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_decision_traces_case_id", "decision_traces", ["case_id"])
    op.create_index("ix_decision_traces_mask_version_id", "decision_traces", ["mask_version_id"])
    op.create_index("ix_decision_traces_stage", "decision_traces", ["pipeline_stage"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_decision_traces_stage", table_name="decision_traces")
    op.drop_index("ix_decision_traces_mask_version_id", table_name="decision_traces")
    op.drop_index("ix_decision_traces_case_id", table_name="decision_traces")
    op.drop_table("decision_traces")

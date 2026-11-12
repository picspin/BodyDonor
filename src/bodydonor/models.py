"""7 个核心实体 + LicenseState/UsageLedger（SQLAlchemy 2.0 ORM，M0-fix）。

实体体系（proposal §11 M0 数据模型）：

- Project ─< Case ─< Series
- Series ─< MaskVersion（teacher/student 来源 + 版本号）
- MaskVersion ─< QCReport
- QCReport / Series ─< ReviewTask
- ModelVersion：学生模型版本 + 晋级门禁结果（hold-out Dice 下限）
- LicenseState / UsageLedger：无 License 试用限制的计数基础
  （每年 200 例 / 每月 20 例，先到者为准；强制执行 M1 起，本文件只做数据模型）

沿用 M0 已有的 cases / series / jobs 三张表（表名不变），纳入实体体系：
cases -> Case、series -> Series；jobs 保留为通用流水线任务表（与 8 阶段对应），
不与 ReviewTask 混用。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """可读主键：{prefix}-{uuid4 前 8 位}。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Project / Case / Series
# ---------------------------------------------------------------------------


class Project(Base):
    """项目：病例的组织单位（proposal §4.1 项目与任务元数据）。"""

    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    cases: Mapped[list[Case]] = relationship(back_populates="project")


class Case(Base):
    """病例（表名沿用 M0 的 cases）。一个病例可含多个 Series。

    project_id 为空 = 未归组（M0 CLI 扫描产物可直接入库，不强制建 Project）。
    """

    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.project_id"), nullable=True, index=True
    )
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    project: Mapped[Project | None] = relationship(back_populates="cases")
    series: Mapped[list[Series]] = relationship(back_populates="case")


class Series(Base):
    """影像序列（表名沿用 M0 的 series）。NIfTI 单文件 = 单元素 Series。"""

    __tablename__ = "series"

    series_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    num_slices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slice_thickness_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    orientation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hu_range: Mapped[str | None] = mapped_column(String(64), nullable=True)  # "-1024..3071"
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # M2-5(a)：源 DICOM 实例信息持久化（逐层 SOP UID + 几何三要素，JSON）。
    # DICOM 来源序列 ingest 时从头提取，commit 时落库；export_seg 用它写真实
    # 源实例引用（ReferencedSOPInstanceUID），合成源实例降级为无 DICOM 来源
    # （如 NIfTI 输入）时的 fallback。结构见 ingest.extract_source_instances。
    source_instances_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="series")
    mask_versions: Mapped[list[MaskVersion]] = relationship(back_populates="series")
    review_tasks: Mapped[list[ReviewTask]] = relationship(back_populates="series")


# ---------------------------------------------------------------------------
# MaskVersion / QCReport / ReviewTask / ModelVersion
# ---------------------------------------------------------------------------


class MaskVersion(Base):
    """mask 版本：teacher 初版、student 重生成、人工修正各为一个版本。

    source: 'teacher' | 'student' | 'manual'（legacy 画笔）| 'human'
    （M5 规范画笔修正）| 'nninteractive_guided'（M5-e：人工提示引导的模型
    输出，20 字符，列宽 String(32)）。
    provenance（M5-e 新增，JSON nullable）：{sessionId,
    baselineTeacherVersion, prompts[], acceptedClasses[]}（+ submit-nni
    附加 targetLabel / correctionSidecar），prompts 可重放。
    nnInteractive 修正产物绝不进商用蒸馏训练集（门禁见 ontology /
    distill_data，与列宽无关）。
    """

    __tablename__ = "mask_versions"
    __table_args__ = (
        UniqueConstraint("series_id", "version", name="uq_maskversion_series_version"),
        Index("ix_mask_versions_series_id", "series_id"),
    )

    mask_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.series_id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # M5-e：String(16) -> String(32)，装得下 20 字符的 nninteractive_guided
    # （M4 遗留；迁移 e7f3a1c9d204）。
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    # M5-e：血缘（M5-PLAN §4）：{sessionId, baselineTeacherVersion,
    # prompts[], acceptedClasses[]}；无血缘的老行 = NULL。
    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_versions.model_version_id"), nullable=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    series: Mapped[Series] = relationship(back_populates="mask_versions")
    model_version: Mapped[ModelVersion | None] = relationship(back_populates="mask_versions")
    qc_reports: Mapped[list[QCReport]] = relationship(back_populates="mask_version")


class QCReport(Base):
    """QC 引擎输出（proposal §7）：分数、flags、是否低质量过滤。"""

    __tablename__ = "qc_reports"
    __table_args__ = (Index("ix_qc_reports_mask_version_id", "mask_version_id"),)

    qc_report_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mask_version_id: Mapped[str] = mapped_column(
        ForeignKey("mask_versions.mask_version_id"), nullable=False
    )
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    flags: Mapped[list[Any]] = mapped_column(JSON, default=list)  # ['volume_prior', ...]
    rule_scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    mask_version: Mapped[MaskVersion] = relationship(back_populates="qc_reports")
    review_tasks: Mapped[list[ReviewTask]] = relationship(back_populates="qc_report")


class ReviewTask(Base):
    """人工抽检任务（proposal §6 第 5 阶段 / §8 抽检混合采样）。

    status: 'pending' | 'claimed' | 'accepted' | 'corrected' | 'rejected'
    每任务锚定一个 QCReport（代表被抽检的 mask 版本），同时挂 Series 便于检索。
    """

    __tablename__ = "review_tasks"
    __table_args__ = (Index("ix_review_tasks_status", "status"),)

    review_task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    qc_report_id: Mapped[str] = mapped_column(ForeignKey("qc_reports.qc_report_id"), nullable=False)
    series_id: Mapped[str] = mapped_column(ForeignKey("series.series_id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    assignee: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Q10: role 预留
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    corrected_mask_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("mask_versions.mask_version_id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, onupdate=utcnow)

    qc_report: Mapped[QCReport] = relationship(back_populates="review_tasks")
    series: Mapped[Series] = relationship(back_populates="review_tasks")


class DecisionTrace(Base):
    """决策审计痕迹（M6-f，proposal §7 过程信任）：决策在哪发生就在哪记一条。

    每行 = 一个流水线阶段的一次决策记录（append-only；human_outcome 由决策
    回流端点事后补填，只改 human_outcome/updated_at 两列——决策内容
    （signals/rules/tier）不回写，历史不改写）：

    - pipeline_stage: 'qc_scoring'（qc_series 产 QCReport 同一事务）|
      'gate_decision'（generate_review_tasks 分档 + 建任务同一事务）。
    - input_refs: MaskVersion / ModelVersion / QCReport 的 id 指针（非像素）。
    - process_signals: M6-c priority 分量（w1–w4 + availability）+ M6-b
      过程规则逐类 flag + 交互覆盖（该 case 是否有过 nnInteractive 交互——
      小样本本身是要记录的事实）。
    - rules_triggered: 与同决策流产出的 QCReport.flags 逐项一致（验收 b）。
    - gate_decision: M6-d 三档 tier + 是否生成 ReviewTask（含 id/组）；
      qc_scoring 阶段尚无门禁决策 → null。
    - human_outcome: null = 尚无人工决策（不造假）；回流后
      {"decision", "notes", "decided_at"}——空缺本身是 M7 决策回流的信号。
    - calibration: 生效中的校准参数（gating 三比例 + priority 权重 + source
      cold_start）——M7 校准重算的对照基准。
    """

    __tablename__ = "decision_traces"
    __table_args__ = (
        Index("ix_decision_traces_case_id", "case_id"),
        Index("ix_decision_traces_mask_version_id", "mask_version_id"),
        Index("ix_decision_traces_stage", "pipeline_stage"),
    )

    trace_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("dt")
    )
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # qc 阶段可未给出
    series_id: Mapped[str] = mapped_column(String(64), nullable=False)
    mask_version_id: Mapped[str] = mapped_column(String(64), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    pipeline_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    input_refs: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    process_signals: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    rules_triggered: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    gate_decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    human_outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    calibration: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    review_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, onupdate=utcnow)


class ModelVersion(Base):
    """学生模型版本 + 晋级门禁结果（proposal §8：mean Dice + 每类 Dice 下限）。

    gate_status: 'pending' | 'passed' | 'failed'
    gate_metrics: {"mean_dice": 0.87, "per_class_dice": {"liver": 0.9}, ...}
    """

    __tablename__ = "model_versions"

    model_version_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_model: Mapped[str | None] = mapped_column(String(200), nullable=True)  # nnU-Net v2
    weights_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    gate_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    gate_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    gate_holdout_file: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )  # eval/holdout.txt
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    mask_versions: Mapped[list[MaskVersion]] = relationship(back_populates="model_version")


# ---------------------------------------------------------------------------
# LicenseState / UsageLedger（计数基础，M1 起强制执行）
# ---------------------------------------------------------------------------


class LicenseState(Base):
    """无 License 试用限制的计数状态（proposal §13：每年 200 例 / 每月 20 例，先到者为准）。

    license_id 为空 = 未激活（trial）。机器指纹只存哈希，不存原始指纹
    （proposal §2.1 数据最小化：激活信息只传指纹哈希与 license ID）。
    激活校验（Ed25519）M4/M5 落地，本表只存结论字段。
    """

    __tablename__ = "license_state"
    __table_args__ = (UniqueConstraint("fingerprint_hash", name="uq_license_fingerprint"),)

    license_state_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    license_id: Mapped[str | None] = mapped_column(String(128), nullable=True)  # None = trial
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trial_yearly_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    trial_monthly_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    # 试用计数缓存（当年/当月已处理例数）：由 UsageLedger 流水在写路径维护，
    # 查询方读这两列即可判定超限，不必每次 SUM 流水。真相源仍是 UsageLedger。
    trial_yearly_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_monthly_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, onupdate=utcnow)


class UsageLedger(Base):
    """已处理例数的流水账：导入/处理一个 Case 记一行，计数走 SUM/COUNT。

    period 形如 '2026-11'（月）或 '2026'（年），导入路径 M1 起写双行（年 + 月）。
    """

    __tablename__ = "usage_ledger"
    __table_args__ = (
        UniqueConstraint("fingerprint_hash", "period", "case_id", name="uq_usage_fp_period_case"),
        Index("ix_usage_ledger_fp_period", "fingerprint_hash", "period"),
    )

    usage_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    license_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # '2026' 或 '2026-11'
    case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

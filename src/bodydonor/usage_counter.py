"""试用限制计数（M1，proposal §11 M0 / §13 / M0-FIX-REPORT §5 遗留项 1）。

导入路径维护 LicenseState/UsageLedger 计数：

- **UsageLedger 为真相源**：逐 Case 流水，`(fingerprint_hash, period, case_id)` 唯一
  （幂等计数：重复导入同一 Case 不重复计）。
- **LicenseState 缓存列**（trial_yearly_used / trial_monthly_used）由写路径同步维护，
  查询方读缓存即可判定超限，不必每次 SUM 流水。
- **无 License 试用限制**：每年 200 例 / 每月 20 例，先到者为准。超限拦截并给出明确提示。
- 计数以 **Case** 为单位（一个 Case 一次导入计 1 例；同 Case 的多个 Series 不重复计）。

写路径走 db.write_lock 单一写入纪律（M0-fix）。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from .models import LicenseState, UsageLedger

DEFAULT_YEARLY_LIMIT = 200
DEFAULT_MONTHLY_LIMIT = 20


class TrialLimitError(Exception):
    """试用限制超限（每年 200 例 / 每月 20 例，先到者为准）。"""

    def __init__(self, period: str, used: int, limit: int):
        self.period = period
        self.used = used
        self.limit = limit
        kind = "年" if "-" not in period else "月"
        super().__init__(
            f"试用限制超限：{kind}度已处理 {used} 例（上限 {limit} 例，"
            f"先到者为准）。period={period}。导入被拦截；激活 license 后解除。"
        )


def fingerprint_hash() -> str:
    """机器指纹哈希（sha256 hex）。M1 用稳定的机器级随机 ID 占位——
    真实指纹（硬件/容器容错）在 M4/M5 Ed25519 激活流程落地，不绑死。"""
    import platform

    raw = f"{platform.node()}|bodydonor-fingerprint-v1"
    return hashlib.sha256(raw.encode()).hexdigest()


def current_periods(now: datetime | None = None) -> tuple[str, str]:
    """返回 (年 period, 月 period)，形如 ('2026', '2026-11')。"""
    now = now or datetime.now(UTC)
    return f"{now.year}", f"{now.year}-{now.month:02d}"


def get_or_create_license_state(session, fp: str) -> LicenseState:  # noqa: ANN001 — Session
    state = session.execute(
        select(LicenseState).where(LicenseState.fingerprint_hash == fp)
    ).scalar_one_or_none()
    if state is None:
        state = LicenseState(
            fingerprint_hash=fp,
            license_id=None,
            trial_yearly_limit=DEFAULT_YEARLY_LIMIT,
            trial_monthly_limit=DEFAULT_MONTHLY_LIMIT,
            trial_yearly_used=0,
            trial_monthly_used=0,
        )
        session.add(state)
        session.flush()
    return state


def _refresh_counts_from_ledger(session, fp: str, state: LicenseState) -> None:  # noqa: ANN001
    """从流水（真相源）重算缓存列。幂等：重复导入后缓存仍正确。"""
    yearly, monthly = current_periods()
    y_used = len(
        session.execute(
            select(UsageLedger.usage_id).where(
                UsageLedger.fingerprint_hash == fp, UsageLedger.period == yearly
            )
        ).all()
    )
    m_used = len(
        session.execute(
            select(UsageLedger.usage_id).where(
                UsageLedger.fingerprint_hash == fp, UsageLedger.period == monthly
            )
        ).all()
    )
    state.trial_yearly_used = y_used
    state.trial_monthly_used = m_used


def check_trial_limits(session, fp: str) -> dict[str, Any]:  # noqa: ANN001
    """预检：当前 period 的计数是否已达上限。超限 raise TrialLimitError。"""
    state = get_or_create_license_state(session, fp)
    if state.license_id is not None:
        return {"enforced": False, "reason": "licensed", "state": state}
    _refresh_counts_from_ledger(session, fp, state)
    yearly, monthly = current_periods()
    if state.trial_yearly_used >= state.trial_yearly_limit:
        raise TrialLimitError(yearly, state.trial_yearly_used, state.trial_yearly_limit)
    if state.trial_monthly_used >= state.trial_monthly_limit:
        raise TrialLimitError(monthly, state.trial_monthly_used, state.trial_monthly_limit)
    return {"enforced": True, "state": state}


def record_case_usage(session, case_id: str, fp: str) -> dict[str, Any]:  # noqa: ANN001
    """导入路径的计数写入口：写双行流水（年 + 月）+ 同步缓存列。幂等。

    重复导入同一 case_id：流水唯一约束命中 → 不重复计，缓存重算后仍正确。
    返回 {recorded: bool, yearly_used, monthly_used}。
    """
    state = get_or_create_license_state(session, fp)
    yearly, monthly = current_periods()
    recorded = False
    for period in (yearly, monthly):
        exists = session.execute(
            select(UsageLedger.usage_id).where(
                UsageLedger.fingerprint_hash == fp,
                UsageLedger.period == period,
                UsageLedger.case_id == case_id,
            )
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                UsageLedger(
                    fingerprint_hash=fp,
                    license_id=state.license_id,
                    period=period,
                    case_id=case_id,
                )
            )
            recorded = True
    _refresh_counts_from_ledger(session, fp, state)
    return {
        "recorded": recorded,
        "yearly_used": state.trial_yearly_used,
        "monthly_used": state.trial_monthly_used,
    }


def new_machine_id() -> str:
    """测试辅助：机器指纹生成。"""
    return uuid.uuid4().hex

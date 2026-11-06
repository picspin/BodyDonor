"""CLI 命令树（M0 起步，M1 接 ingest）：bodydonor import / license / status。

Typer + rich.table（proposal §4.4：M0/M1 纯 CLI 起步，M2 再嵌 Textual）。
TUI/CLI 是本地盘通道：import 直读磁盘路径，不经 HTTP。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import load_settings
from .license_registry import LicenseRegistry

app = typer.Typer(
    help="BodyDonor — medical imaging segmentator agent (research tool)",
    no_args_is_help=True,
)
license_app = typer.Typer(help="第三方许可核查状态")
app.add_typer(license_app, name="license")

console = Console()
err_console = Console(stderr=True)


def _settings():
    return load_settings()


# ---------------------------------------------------------------------------
# bodydonor import
# ---------------------------------------------------------------------------

@app.command("import", context_settings={"allow_extra_args": False})
def import_cmd(
    path: Path = typer.Argument(  # noqa: B008 — Typer 要求默认值为调用表达式
        ..., exists=True, readable=True, help="NIfTI/DICOM 文件夹/zip 路径"
    ),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", "-r", help="递归扫描子目录"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只扫描出导入报告，不落库"),
    no_series_ui: bool = typer.Option(
        True, "--select/--no-select", help="应用 Series 选择器规则（默认开）"
    ),
    include: list[str] = typer.Option(  # noqa: B008 — Typer 要求默认值为调用表达式
        None, "--include", help="显式勾选的 series_id（可多次；勾回被规则排除的序列）"
    ),
    as_json: bool = typer.Option(False, "--json", help="以 JSON 输出报告（API 同构）"),
) -> None:
    """导入影像：ingest -> 导入报告（自检门）-> [确认后] 落库。

    自检门：先输出导入报告（Series 数、被跳过原因、层厚、方向、HU 范围），
    dry_run 到此为止；不带 --dry-run 则继续落库并维护试用计数。
    """
    settings = _settings()

    # 像素落盘在数据目录（WSL2 原生 FS，禁 /mnt/c 由 resolved_data_dir 把关）
    from .pipeline import commit, scan
    from .storage import LocalFsStorage

    try:
        data_dir = settings.resolved_data_dir
    except ValueError as e:
        err_console.print(f"[red]✘ 配置错误：{e}[/red]")
        raise typer.Exit(1) from None

    try:
        outcome = scan(path, settings, source_name=path.name, recursive=recursive)
    except Exception as e:  # noqa: BLE001 — IngestError/ExtractLimitError 统一呈现
        err_console.print(f"[red]✘ 导入失败：{e}[/red]")
        raise typer.Exit(1) from None

    if as_json:
        console.print_json(json.dumps(outcome.to_dict(), ensure_ascii=False))
    else:
        _print_import_report(outcome, path)

    if dry_run:
        err_console.print("[yellow]dry-run：只出导入报告，不落库。[/yellow]")
        return

    # 落库（确认门之后）
    if not outcome.report.series:
        err_console.print("[red]✘ 无可导入 Series，中止。[/red]")
        raise typer.Exit(1)
    try:
        storage = LocalFsStorage(data_dir / "storage")
        overrides = list(include) if include else None
        outcome = commit(outcome, settings, storage, user_overrides=overrides)
    except Exception as e:  # noqa: BLE001
        err_console.print(f"[red]✘ 落库失败：{e}[/red]")
        raise typer.Exit(1) from None

    if as_json:
        console.print_json(json.dumps(outcome.to_dict(), ensure_ascii=False))
        return
    _print_commit_result(outcome)


def _print_import_report(outcome, path: Path) -> None:  # noqa: ANN001 — ImportOutcome
    """导入报告（自检门）：rich.table 打印（proposal §5）。

    M1-fix：选择器列按 **series_id** 从 candidates 取（apply_rules 排序 candidates
    但 report.series 保持扫描序，位置 zip 会错位——M1-REPORT §3 实录 bug 根因）。
    """
    report = outcome.report
    cand_map = {c.series_id: c for c in outcome.candidates}
    table = Table(title=f"导入报告（自检门）— {path}")
    table.add_column("Series", style="cyan", overflow="fold")
    table.add_column("模态")
    table.add_column("层数", justify="right")
    table.add_column("层厚 mm", justify="right")
    table.add_column("方向")
    table.add_column("HU 范围", justify="right")
    table.add_column("烧录")
    table.add_column("选择器")
    for rec in report.series:
        cand = cand_map.get(rec.series_id)
        if cand is not None:
            sel_state = "[green]选中[/green]" if not cand.excluded else "[red]排除[/red]"
            sel = (
                sel_state
                if not cand.excluded
                else f"[red]排除：{cand.exclude_reason}[/red]"
            )
            if cand.flags:
                sel += " [yellow]" + "; ".join(cand.flags) + "[/yellow]"
        else:
            sel = "—"
        hu = f"{rec.hu_range[0]}..{rec.hu_range[1]}" if rec.hu_range else "—"
        bia = rec.burned_in_annotation
        bia_s = {"YES": "[red]YES[/red]", "NO": "no", "UNKNOWN": "[yellow]?[/yellow]"}.get(bia, bia)
        table.add_row(
            rec.series_id[:32] + ("…" if len(rec.series_id) > 32 else ""),
            rec.modality or "—",
            str(rec.num_slices),
            f"{rec.slice_thickness_mm:.2f}" if rec.slice_thickness_mm else "—",
            rec.orientation,
            hu,
            bia_s,
            sel,
        )
    console.print(table)

    meta = Table(title=None, show_header=False)
    meta.add_column("项", style="cyan")
    meta.add_column("值")
    meta.add_row("输入形态", report.input_kind)
    meta.add_row("扫描文件数", str(report.n_files_scanned))
    meta.add_row("Series 数", str(report.n_series))
    meta.add_row("被跳过数", str(report.n_skipped))
    for sk in report.skipped[:10]:
        meta.add_row(f"  跳过：{sk['file'][:40]}", sk["reason"])
    for w in report.warnings[:5]:
        meta.add_row("  警告", w)
    # 几何警告（NIfTI）+ 重排方向（M1-fix）
    for rec in report.series:
        for w in rec.geom_warnings[:3]:
            meta.add_row(f"  几何：{rec.series_id[:24]}", w)
        if rec.reorientation:
            meta.add_row(f"  重排：{rec.series_id[:24]}", rec.reorientation)
        if rec.ocr_result is not None and rec.ocr_result.scanned:
            ocr_s = (
                f"[red]{rec.ocr_result.reason}: {rec.ocr_result.texts[:3]}[/red]"
                if rec.ocr_result.detected
                else rec.ocr_result.reason
            )
            meta.add_row(f"  OCR：{rec.series_id[:24]}", ocr_s)
        for w in rec.deid_warnings[:2]:
            meta.add_row(f"  De-ID：{rec.series_id[:24]}", w)
    console.print(meta)


def _print_commit_result(outcome) -> None:  # noqa: ANN001 — ImportOutcome
    table = Table(title="落库结果")
    table.add_column("项", style="cyan")
    table.add_column("值")
    table.add_row("Case 数", str(len(outcome.case_ids)))
    for cid in outcome.case_ids[:10]:
        table.add_row("  case", cid)
    table.add_row("选中 Series 数", str(len(outcome.selected_series_ids)))
    usage = outcome.usage
    if usage:
        table.add_row(
            "试用计数",
            f"年 {usage.get('yearly_used', '?')}/200 · 月 {usage.get('monthly_used', '?')}/20",
        )
    for err in outcome.db_errors[:5]:
        table.add_row("  错误", f"[red]{err}[/red]")
    console.print(table)


# ---------------------------------------------------------------------------
# bodydonor license
# ---------------------------------------------------------------------------

@license_app.command("status")
def license_status() -> None:
    """打印第三方许可核查状态（LICENSES-THIRD-PARTY.md 摘要）。"""
    settings = _settings()
    registry = LicenseRegistry(settings)
    s = registry.summary()

    table = Table(title="第三方许可核查状态（M0）")
    table.add_column("组件", style="cyan")
    table.add_column("许可", style="green")
    table.add_column("用途边界")
    for name, fact in s["license_facts"].items():  # type: ignore[union-attr]
        table.add_row(name, fact.get("license", "?"), fact.get("usage", fact.get("note", "")))
    console.print(table)

    t2 = Table(title="任务白名单（teacher.allowed_tasks）")
    t2.add_column("白名单任务数", style="green")
    t2.add_column("商用蒸馏白名单")
    t2.add_column("禁用任务", style="red")
    t2.add_column("参考文件")
    t2.add_row(
        str(len(s["allowed_tasks"])),  # type: ignore[arg-type]
        ", ".join(s["commercial_distill_tasks"]),  # type: ignore[arg-type]
        ", ".join(s["blocked_tasks"]),  # type: ignore[arg-type]
        str(s["reference_doc"]),
    )
    console.print(t2)


@license_app.command("check")
def license_check(task: str = typer.Argument(..., help="要检查的任务名")) -> None:
    """检查单个任务是否在白名单。退出码 0=允许，1=拒绝。"""
    registry = LicenseRegistry(_settings())
    ok, msg = registry.check_task(task)
    if ok:
        console.print(f"[green]✔ {task}: {msg}[/green]")
        raise typer.Exit(0)
    err_console.print(f"[red]✘ {task}: {msg}[/red]")
    raise typer.Exit(1)


@license_app.command("usage")
def license_usage() -> None:
    """打印试用计数（LicenseState/UsageLedger）：年 200 例 / 月 20 例。"""
    from sqlalchemy import select

    from . import db
    from .models import LicenseState
    from .usage_counter import current_periods

    settings = _settings()
    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            states = s.execute(select(LicenseState)).scalars().all()
            s.close()
    finally:
        engine.dispose()

    yearly, monthly = current_periods()
    table = Table(title=f"试用计数（{yearly} / {monthly}；年 200 例 · 月 20 例，先到者为准）")
    table.add_column("指纹（前 16 位）", style="cyan")
    table.add_column("模式")
    table.add_column(f"{yearly} 已用", justify="right")
    table.add_column(f"{monthly} 已用", justify="right")
    if not states:
        table.add_row("—", "trial（未导入）", "0", "0")
    for st in states:
        table.add_row(
            st.fingerprint_hash[:16],
            "licensed" if st.license_id else "trial",
            f"{st.trial_yearly_used}/{st.trial_yearly_limit}",
            f"{st.trial_monthly_used}/{st.trial_monthly_limit}",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# bodydonor status
# ---------------------------------------------------------------------------

@app.command()
def status() -> None:
    """打印系统状态：部署形态、限制、队列、存储、许可白名单。"""
    settings = _settings()
    table = Table(title=f"BodyDonor v{__version__} — status")
    table.add_column("项", style="cyan")
    table.add_column("值")
    table.add_row("deployment_mode", settings.deployment_mode)
    table.add_row("max_upload_mb（当前形态）", str(settings.max_upload_mb))
    table.add_row("试用限制", "年 200 例 / 月 20 例（无 License，先到者为准）")
    table.add_row("cloud_demo 限制", str(settings.limits.cloud_demo.model_dump()))
    table.add_row("on_prem 限制", str(settings.limits.on_prem.model_dump()))
    table.add_row("解压保护", str(settings.extract_limits.model_dump()))
    table.add_row("data_dir", str(settings.resolved_data_dir))
    table.add_row("storage backend", settings.storage.backend)
    table.add_row("queue backend", settings.queue.backend)
    table.add_row("database", settings.database.url)
    table.add_row("teacher engine", settings.teacher.engine)
    table.add_row(
        "任务白名单", f"{len(settings.teacher.allowed_tasks)} 个（bodydonor license status 查看）"
    )
    escape = (
        "enabled" if settings.experimental_text_prompt.enabled else "disabled（显式开启才可用）"
    )
    table.add_row("实验逃生通道", escape)
    console.print(table)


@app.command()
def version() -> None:
    """打印版本。"""
    console.print(f"BodyDonor v{__version__}")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())

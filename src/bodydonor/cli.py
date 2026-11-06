"""CLI 命令树起步（M0）：bodydonor import / license / status。

Typer + rich.table（proposal §4.4：M0/M1 纯 CLI 起步，M2 再嵌 Textual）。
TUI/CLI 是本地盘通道：import 直读磁盘路径，不经 HTTP。
"""

from __future__ import annotations

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
    recursive: bool = typer.Option(False, "--recursive", "-r", help="递归扫描子目录"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只扫描出导入报告，不落库"),
) -> None:
    """导入影像：M0 只做扫描自检报告，M1 接 ingest 落库。"""
    settings = _settings()
    table = Table(title=f"导入自检报告 — {path}")
    table.add_column("项", style="cyan")
    table.add_column("值")

    # 路径安全：本命令走本地盘通道；数据目录禁 /mnt/c 由 settings.resolved_data_dir 把关
    nifti = sorted(path.glob("**/*.nii*")) if recursive else list(path.glob("*.nii*"))
    dicom_dirs: list[Path] = []
    if path.is_dir():
        for dcm in sorted(path.rglob("*.dcm")) if recursive else sorted(path.glob("*.dcm")):
            parent = dcm.parent
            if parent not in dicom_dirs:
                dicom_dirs.append(parent)

    table.add_row("NIfTI 文件数", str(len(nifti)))
    for f in nifti[:10]:
        table.add_row("  nii", f.name)
    table.add_row("DICOM 序列数（按目录聚合）", str(len(dicom_dirs)))
    for d in dicom_dirs[:10]:
        table.add_row("  series dir", str(d))
    table.add_row("deployment_mode", settings.deployment_mode)
    table.add_row("max_upload_mb (cloud_demo)", str(settings.limits.cloud_demo.max_upload_mb))
    table.add_row("data_dir", str(settings.resolved_data_dir))
    mode_label = "dry-run（M0：扫描报告，不落库）" if dry_run else "scan（M0：扫描报告，不落库）"
    table.add_row("mode", mode_label)
    console.print(table)

    if not dry_run:
        err_console.print(
            "[yellow]提示：M0 的 import 只产出扫描自检报告；ingest 落库在 M1 交付。[/yellow]"
        )


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

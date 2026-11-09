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
ontology_app = typer.Typer(help="本体映射（NL 标签 -> TotalSegmentator 类别）")
app.add_typer(ontology_app, name="ontology")

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
# bodydonor ontology
# ---------------------------------------------------------------------------

@ontology_app.command("resolve")
def ontology_resolve(
    labels: list[str] = typer.Argument(..., help="要查询的 NL 标签（可多个）"),  # noqa: B008
    as_json: bool = typer.Option(False, "--json", help="JSON 输出（API 同构）"),
) -> None:
    """NL 标签三态查询：resolved / ambiguous（候选交用户点选）/ unresolved。"""
    from .ontology import OntologyService

    svc = OntologyService(_settings())
    results = svc.resolve_batch(list(labels))
    if as_json:
        import json as _json

        console.print_json(
            _json.dumps([r.to_dict() for r in results], ensure_ascii=False)
        )
        return
    for r in results:
        state_color = {
            "resolved": "green",
            "ambiguous": "yellow",
            "unresolved": "red",
        }.get(r.state, "white")
        console.print(f"[{state_color}]{r.state}[/{state_color}]  {r.label}")
        if r.state == "resolved" and r.entry:
            console.print(
                f"    -> totalseg_class: {r.entry.totalseg_class}"
                f"  (SNOMED {r.entry.snomed_ct} / Uberon {r.entry.uberon})"
                f"  [{r.reason}]"
            )
        for c in r.candidates:
            console.print(
                f"    候选: {c.entry.totalseg_class}"
                f"  (SNOMED {c.entry.snomed_ct} / {c.entry.nl_label})"
            )
        if r.state == "unresolved":
            console.print(f"    {r.reason}")


@ontology_app.command("disambiguate")
def ontology_disambiguate(
    label: str = typer.Argument(..., help="ambiguous 态的 NL 标签"),
    totalseg_class: str = typer.Argument(..., help="用户点选的 totalseg_class"),
) -> None:
    """用户消歧写回：点选结果写进 overrides.yaml，下次 resolve 直接 resolved。"""
    from .ontology import OntologyError, OntologyService

    svc = OntologyService(_settings())
    try:
        chosen = svc.disambiguate(label, totalseg_class)
    except OntologyError as e:
        err_console.print(f"[red]✘ {e}[/red]")
        raise typer.Exit(1) from None
    console.print(
        f"[green]✔ 已写回[/green] {label} -> {chosen.totalseg_class}"
        f"（ontology/{svc.version}/overrides.yaml）"
    )


@ontology_app.command("escape")
def ontology_escape(
    labels: list[str] = typer.Argument(..., help="要试逃生通道的 NL 标签"),  # noqa: B008
    enable: bool = typer.Option(
        False, "--experimental-text-prompt", help="显式开启实验逃生通道"
    ),
) -> None:
    """实验逃生通道（SegVol/SAT 真文本提示）状态查询。

    默认关闭；--experimental-text-prompt 显式开启后结果打 experimental 标记，
    绝不进蒸馏训练集。M2 只落地开关与隔离逻辑（模型接入 M3+）。
    """
    from .ontology import OntologyService

    settings = _settings()
    if enable and not settings.experimental_text_prompt.enabled:
        settings.experimental_text_prompt.enabled = True
    svc = OntologyService(settings)
    for lb in labels:
        out = svc.escape_hatch(lb)
        color = "green" if out["allowed"] else "red"
        tag = " [experimental]" if out["experimental"] else ""
        console.print(f"[{color}]{out['allowed']}[/{color}]  {lb}{tag} — {out['reason']}")


# ---------------------------------------------------------------------------
# bodydonor segment（M2，TotalSegmentator worker 入口）
# ---------------------------------------------------------------------------

@app.command()
def segment(
    series_id: str = typer.Argument(..., help="已 ingest 的 Series ID"),
    task: str = typer.Option("total", "--task", help="TS 任务（白名单判定）"),
    device: str = typer.Option("gpu", "-d", "--device", help="gpu | cpu"),
    as_json: bool = typer.Option(False, "--json", help="JSON 输出（API 同构）"),
) -> None:
    """对已 ingest 的 Series 跑 TotalSegmentator：原图 → 多类 mask → MaskVersion。

    - 白名单判定（license_registry.check_task）：blocked 一票否决，不许绕过
    - <30 层防线复查（定位像残片 3D 卷积报错）
    - mask 落盘 case_id/series_id/ 目录 + MaskVersion 记录（affine 守卫强制）
    """
    from .segmentation import SegmentWorkerError, segment_series

    settings = _settings()
    try:
        result = segment_series(series_id, settings, task=task, device=device)
    except SegmentWorkerError as e:
        err_console.print(f"[red]✘ 分割失败：{e}[/red]")
        raise typer.Exit(1) from None
    if as_json:
        console.print_json(json.dumps(result.to_dict(), ensure_ascii=False))
        return
    table = Table(title=f"TotalSegmentator {task} — {series_id}")
    table.add_column("项", style="cyan")
    table.add_column("值")
    table.add_row("case", result.case_id)
    table.add_row("mask", result.mask_key)
    table.add_row("combined_mask", result.combined_mask_key or "—")
    table.add_row("labels_json", result.labels_json_key or "—")
    table.add_row("类别数", str(result.n_classes))
    table.add_row("耗时", f"{result.elapsed_s:.1f}s")
    # 显存峰值：主值 = runner torch 分配器实测；nvidia-smi 轮询交叉核对
    if result.peak_memory_mi_b is not None:
        smi_s = (
            f"（nvidia-smi 交叉核对 {result.peak_memory_nvidia_smi_mi_b:.0f}）"
            if result.peak_memory_nvidia_smi_mi_b is not None
            else "（nvidia-smi 不可用，无交叉核对值）"
        )
        table.add_row("显存峰值（主值）", f"{result.peak_memory_mi_b:.0f} MiB {smi_s}")
    else:
        table.add_row("显存峰值", "null（不可用，不猜数字）")
    shown = ", ".join(result.class_names[:8])
    if len(result.class_names) > 8:
        shown += f" … 共 {len(result.class_names)} 类"
    table.add_row("类别", shown or "—")
    console.print(table)


# ---------------------------------------------------------------------------
# bodydonor nninteractive-correct（M4 阶段 3，交互修正后端入口）
# ---------------------------------------------------------------------------

@app.command("nninteractive-correct")
def nninteractive_correct(
    image: Path = typer.Argument(  # noqa: B008 — Typer 要求默认值为调用表达式
        ..., exists=True, readable=True, help="原图 NIfTI 路径（不预处理，原样推理）"
    ),
    point: list[str] = typer.Option(  # noqa: B008 — Typer 要求默认值为调用表达式
        [], "--point", help="正点提示 x,y,z（体素坐标，可多次）"
    ),
    negative_point: list[str] = typer.Option(  # noqa: B008
        [], "--negative-point", help="负点提示 x,y,z（可多次）"
    ),
    bbox: list[str] = typer.Option(  # noqa: B008
        [], "--bbox", help="2D bbox 提示 x0,x1,y0,y1,z0,z1（单切片维用 [d,d+1]）"
    ),
    target_label: int = typer.Option(1, "--target-label", help="目标标签值（体素统计用）"),
    output_dir: Path = typer.Option(  # noqa: B008
        None, "--output-dir", help="修正产物目录（缺省 = 原图旁 nninteractive-correction/）"
    ),
    device: str = typer.Option(
        "auto", "--device", "-d", help="auto | cpu | cuda（auto = CUDA 可用则 cuda）"
    ),
    job_id: str = typer.Option("", "--job-id", help="任务 ID（锁文件/sidecar 审计用）"),
    as_json: bool = typer.Option(False, "--json", help="JSON 输出（API 同构）"),
) -> None:
    """nnInteractive 交互修正：点/框提示 → 修正 mask + sidecar JSON 落盘。

    - GPU 锁（interactive fast-fail）：批量推理/训练持卡时立即报错，不排队
    - 修正产物 sidecar 带 source=manual-correction-nninteractive（CC BY-NC-SA
      4.0 权重传染标记），**绝不进商用蒸馏训练集**（蒸馏门禁强制）
    - 短会话模式：acquire → 推理 → 保存 → release（保存后立即释放）
    """
    from .nninteractive_backend import (
        GpuBusyError,
        NnInteractiveError,
        correct_image,
    )

    def _parse_xyz(spec: str, flag: str) -> dict[str, float]:
        parts = [p.strip() for p in spec.split(",")]
        if len(parts) != 3:
            raise typer.BadParameter(f"{flag} 须为 x,y,z 三个数值，收到 {spec!r}")
        try:
            x, y, z = (float(p) for p in parts)
        except ValueError:
            raise typer.BadParameter(f"{flag} 数值非法：{spec!r}") from None
        return {"x": x, "y": y, "z": z}

    prompts: list[dict] = []
    for spec in point:
        p = _parse_xyz(spec, "--point")
        prompts.append({"type": "point", **p, "include": True})
    for spec in negative_point:
        p = _parse_xyz(spec, "--negative-point")
        prompts.append({"type": "point", **p, "include": False})
    for spec in bbox:
        parts = [p.strip() for p in spec.split(",")]
        if len(parts) != 6:
            raise typer.BadParameter(
                f"--bbox 须为 x0,x1,y0,y1,z0,z1 六个数值，收到 {spec!r}"
            )
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            raise typer.BadParameter(f"--bbox 数值非法：{spec!r}") from None
        prompts.append({
            "type": "bbox",
            "bbox": [vals[0:2], vals[2:4], vals[4:6]],
        })
    if not prompts:
        err_console.print("[red]✘ 至少一个 --point / --negative-point / --bbox 提示[/red]")
        raise typer.Exit(1)

    out_dir = output_dir or image.parent / "nninteractive-correction"
    try:
        result = correct_image(
            image,
            out_dir,
            prompts,
            device=device,
            target_label=target_label,
            job_id=job_id,
        )
    except GpuBusyError as e:
        err_console.print(f"[red]✘ GPU 忙：{e}[/red]")
        raise typer.Exit(2) from None
    except NnInteractiveError as e:
        err_console.print(f"[red]✘ 交互修正失败：{e}[/red]")
        raise typer.Exit(1) from None

    if as_json:
        console.print_json(json.dumps(result.to_dict(), ensure_ascii=False))
        return
    table = Table(title=f"nnInteractive 修正 — {image.name}")
    table.add_column("项", style="cyan")
    table.add_column("值")
    table.add_row("job", result.job_id)
    table.add_row("mask", result.mask_path)
    table.add_row("sidecar", result.sidecar_path)
    table.add_row("source", "manual-correction-nninteractive（CC BY-NC-SA 4.0，不进蒸馏训练集）")
    vram_s = (
        f"{result.peak_vram_mb:.0f} MiB"
        + (
            f"（nvidia-smi 交叉核对 {result.peak_vram_nvidia_smi_mb:.0f}）"
            if result.peak_vram_nvidia_smi_mb is not None
            else "（nvidia-smi 不可用，无交叉核对值）"
        )
        if result.peak_vram_mb is not None
        else "null（不可用，不猜数字）"
    )
    table.add_row("显存峰值（主值）", vram_s)
    table.add_row("加载耗时", f"{result.load_seconds:.1f}s")
    table.add_row("推理耗时", f"{result.inference_seconds:.1f}s")
    table.add_row("目标体素数", str(result.n_target_voxels))
    console.print(table)


# ---------------------------------------------------------------------------
# bodydonor tui（M2，Textual 监控面）
# ---------------------------------------------------------------------------


@app.command()
def tui(
    base_url: str = typer.Option(
        "http://127.0.0.1:8000", "--url", help="本地 FastAPI 后端地址"
    ),
) -> None:
    """启动 Textual 监控面板：GPU 显存、队列、流水线 ①→⑧ 状态机。

    只读监控 + 本地磁盘导入入口；CLI 批处理与交互式 TUI 互不耦合。
    """
    from .tui import run_tui

    run_tui(base_url=base_url)


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


@app.command("qc-calibrate")
def qc_calibrate() -> None:
    """在线校准（proposal §7.2）：抽检决策回流标签 -> 重算 QC 阈值注记。

    从 ReviewTask.decision（accepted/corrected/rejected）+ QCReport.flags 取
    校准标签，逐规则算判别力（flag 组 vs clean 组标签均值差），写回
    config/qc_thresholds.yaml（calibrated_on/at + discrimination + source 注记）。
    阈值数值 M3 不动（标签量不足以重算数值，§7 明示只当起点）。
    """
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from . import db
    from .models import QCReport, ReviewTask
    from .qc import calibrate_thresholds, load_qc_thresholds, save_calibrated_thresholds

    settings = _settings()
    engine = db._engine(settings.database.url)
    try:
        with engine.connect() as conn:
            s = Session(conn)
            try:
                rows = s.execute(
                    select(ReviewTask.decision, QCReport.flags)
                    .join(QCReport, ReviewTask.qc_report_id == QCReport.qc_report_id)
                    .where(ReviewTask.decision.is_not(None))
                    .order_by(ReviewTask.created_at)
                ).all()
            finally:
                s.close()
    finally:
        engine.dispose()
    labels = [{"decision": d, "flags": list(f or [])} for d, f in rows]
    if not labels:
        console.print(
            "[yellow]无已决策的抽检任务（ReviewTask.decision 全空）——先在抽检台完成决策[/yellow]"
        )
        raise typer.Exit(0)

    th = load_qc_thresholds()
    out = calibrate_thresholds(labels, th)
    save_calibrated_thresholds(out)

    table = Table(title=f"QC 在线校准 — {len(labels)} 条决策标签")
    table.add_column("规则", style="cyan")
    table.add_column("判别力")
    table.add_column("source")
    table.add_column("calibrated_on")
    for rule, cfg in out.get("rules", {}).items():
        if isinstance(cfg, dict):
            table.add_row(
                rule,
                f"{cfg.get('discrimination', 0):.4f}",
                cfg.get("source", ""),
                str(cfg.get("calibrated_on", 0)),
            )
    console.print(table)
    console.print(f"[green]✔ qc_thresholds.yaml 已更新（{len(labels)} 条标签）[/green]")


def main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(main())

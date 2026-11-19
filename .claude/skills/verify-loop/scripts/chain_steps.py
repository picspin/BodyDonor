#!/usr/bin/env python
"""verify-loop 链路步骤实现 + 逐步断言（M7 verify-loop skill，Step 2–12）。

由 run_chain.sh 调用（也可独立运行单步）。全部临时产物（合成 DICOM、SEG
导出、scratch DB）建在 tempfile.mkdtemp() 下、仓外（DESIGN.md 决定 3）——
绝不写真实 config/、bodydonor.db、~/data/BodyDonor。

链路（顺序固定，每步消费上一步产物）：
  Step 2  合成 DICOM 导入：make_dicom_folder 造数据 → zip → POST /ingest/scan
          → POST /ingest/{id}/commit
  Step 3  真实 CPU 分段：POST /segments?device=cpu（TS 权重在盘，实测 87s；
          DESIGN.md Phase B 决定 1——不建 mock）
  Step 4  QC 评分：POST /qc/series/{sid} → QCReport 落库
  Step 5  分档：POST /qc/review-tasks（generate_review_tasks 三档）
  Step 7  POST 保存：/review/{sid}/save（deleted_classes 整类删除 → 新 MV）
  Step 8  断言 MaskVersion 落库（version 递增）
  Step 9  导出 SEG：export_seg.export_and_verify（source_info_from_persisted
          真实引用路径）
  Step 10 断言 verify_seg 体素差 = 0（export_and_verify 内含，此处复核报告）
  Step 11 断言 DecisionTrace 两档各 ≥1（pipeline_stage 列，DESIGN.md 决定 4）
  Step 12 断言 git-status-clean（白名单外新文件即链路泄漏，决定 3）

Step 6（CDP 渲染确认）在 cdp_open_review.py。Step 12 的日志无 ERROR 由
run_chain.sh 在 uvicorn stderr 上断言。

退出码：0 = 全步绿；1 = 任一步红（打印 FAIL 步骤）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # noqa: S404 — 仅 git status（repo 自身）
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # .claude/skills/verify-loop/scripts → repo
sys.path.insert(0, str(REPO))  # tests.factories 在 repo 根

APP = "http://127.0.0.1:8765"


def _settings():
    """绑定 scratch 配置（VERIFY_LOOP_TMP/settings.yaml，run_chain.sh 写入）。

    fail-closed：VERIFY_LOOP_TMP 未设即拒绝（不静默回落真实 config/——
    绝不碰真实 bodydonor.db，DESIGN.md 决定 3）。
    """
    import os

    import bodydonor.config as cfg

    tmp = os.environ.get("VERIFY_LOOP_TMP", "")
    if not tmp:
        print("ERROR: VERIFY_LOOP_TMP 未设（fail-closed）", file=sys.stderr)
        raise SystemExit(2)
    yaml_path = Path(tmp) / "settings.yaml"
    if not yaml_path.exists():
        print(f"ERROR: settings.yaml 不存在：{yaml_path}", file=sys.stderr)
        raise SystemExit(2)
    cfg.DEFAULT_CONFIG_PATH = yaml_path
    return cfg.load_settings()

# git-status-clean 白名单（DESIGN.md 决定 3）：bodydonor.db+-wal/-shm、
# eval/*.json、tests/data/、logs/、__pycache__/、*.py[cod]。链路临时产物在
# 仓外 tmp，天然不进 status。
GIT_EXEMPT_PREFIXES = (
    "bodydonor.db",
    "bodydonor.db-wal",
    "bodydonor.db-shm",
    "eval/",
    "tests/data/",
    "logs/",
)
GIT_EXEMPT_FRAGMENTS = ("__pycache__/", ".pyc", ".pyo")


def _git_exempt(path: str) -> bool:
    if any(path.startswith(p) for p in GIT_EXEMPT_PREFIXES):
        return True
    return any(frag in path for frag in GIT_EXEMPT_FRAGMENTS)


def _post(
    path: str, body: bytes | None = None, ctype: str = "application/json"
) -> tuple[int, dict | bytes]:
    req = urllib.request.Request(
        f"{APP}{path}", data=body, method="POST", headers={"Content-Type": ctype}
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            payload = r.read()
            return r.status, json.loads(payload) if payload[:1] in (b"{", b"[") else payload
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _get(path: str) -> tuple[int, dict | bytes]:
    try:
        with urllib.request.urlopen(f"{APP}{path}", timeout=120) as r:
            payload = r.read()
            return r.status, json.loads(payload) if payload[:1] in (b"{", b"[") else payload
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _post_multipart(path: str, field: str, filename: str, data: bytes) -> tuple[int, dict]:
    """multipart/form-data POST（/ingest/scan 上传通道：File(...) 参数）。"""
    boundary = "----verify-loop-boundary-9d3f"
    parts = [
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        f"Content-Type: application/zip\r\n\r\n"
    ]
    body = parts[0].encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{APP}{path}",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def step2_ingest() -> dict:
    """合成 DICOM 导入 → {series_id, outcome_id}。断言 scan+commit 200、有候选。"""
    from tests.factories import dicom_folder_zip, make_dicom_folder

    with tempfile.TemporaryDirectory(prefix="vl-dicom-") as td:
        root = make_dicom_folder(Path(td), n_series=1, slices_per_series=40)
        zdata = dicom_folder_zip(root)
    status, scan = _post_multipart("/ingest/scan", "file", "synthetic.zip", zdata)
    assert status == 200, f"ingest/scan {status}: {scan}"
    assert scan.get("candidates") or scan.get("selector"), f"ingest/scan 无候选: {list(scan)[:8]}"
    outcome_id = scan["outcome_id"]

    status, committed = _post(f"/ingest/{outcome_id}/commit")
    assert status == 200, f"ingest/commit {status}: {committed}"
    selected = committed.get("selected_series_ids") or []
    assert selected, f"commit 无 selected series: {list(committed)[:8]}"
    sid = selected[0]
    return {"series_id": sid, "outcome_id": outcome_id}


def step3_segment(series_id: str) -> dict:
    """真实 CPU 分段（决定 1：不 mock）。断言 200 + n_classes > 0。"""
    status, out = _post(f"/segments?series_id={series_id}&task=total&device=cpu")
    assert status == 200, f"/segments {status}: {out}"
    assert out.get("n_classes", 0) > 0, f"/segments n_classes=0: {out}"
    return out


def step4_qc(series_id: str) -> dict:
    """QC 评分。断言 200 + qc_report_id 在。"""
    status, out = _post(f"/qc/series/{series_id}")
    assert status == 200, f"/qc/series {status}: {out}"
    assert out.get("qc_report_id"), f"无 qc_report_id: {out}"
    return out


def step5_review_tasks() -> dict:
    """分档生成 ReviewTask。断言 200 + plan 在。"""
    status, out = _post("/qc/review-tasks")
    assert status == 200, f"/qc/review-tasks {status}: {out}"
    assert out.get("plan"), f"无 plan: {out}"
    return out


def step7_save(series_id: str) -> dict:
    """POST 保存（deleted_classes 整类删除 → 新 MaskVersion）。断言 200。"""
    # 取现有 mask 的一个类名做整类删除（segment 产物 label-1 恒在——117 类）
    status, seg_meta = _get(f"/review/{series_id}/seg-meta")
    assert status == 200, f"seg-meta {status}: {seg_meta}"
    entries = seg_meta if isinstance(seg_meta, list) else []
    classes = [c["name"] if isinstance(c, dict) else c for c in entries]
    assert classes, f"seg-meta 空: {str(seg_meta)[:200]}"
    first = classes[0]
    body = json.dumps({"deleted_classes": [first], "source": "manual"}).encode()
    status, out = _post(f"/review/{series_id}/save", body)
    assert status == 200, f"review/save {status}: {out}"
    return {"deleted_class": first, "save": out}


def step8_mask_version(series_id: str, min_version: int = 2) -> dict:
    """断言 MaskVersion 落库且 version >= min_version（teacher v1 + save v2）。"""
    from sqlalchemy import select

    from bodydonor import db as bdb
    from bodydonor.models import MaskVersion

    engine = bdb._engine(_settings().database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            rows = s.execute(
                select(MaskVersion.version, MaskVersion.source, MaskVersion.mask_version_id)
                .where(MaskVersion.series_id == series_id[:60])
                .order_by(MaskVersion.version)
            ).all()
            s.close()
    finally:
        engine.dispose()
    assert rows, f"MaskVersion 无行: series={series_id}"
    versions = [r[0] for r in rows]
    assert max(versions) >= min_version, f"MaskVersion version 未递增: {versions}"
    return {"versions": versions, "sources": [r[1] for r in rows]}


def step9_10_export_seg(series_id: str) -> dict:
    """Step 9+10：导出 SEG + verify_seg 体素差 = 0（export_and_verify 一步）。"""
    from sqlalchemy import select

    from bodydonor import db as bdb
    from bodydonor.export_seg import export_and_verify
    from bodydonor.models import Series
    from bodydonor.pipeline import source_info_from_persisted

    sset = _settings()
    engine = bdb._engine(sset.database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            row = s.execute(
                select(Series).where(Series.series_id == series_id[:60])
            ).scalar_one()
            persisted = row.source_instances_json
            s.close()
    finally:
        engine.dispose()

    assert persisted, f"series {series_id} 无 source_instances_json（DICOM ingest 应已提取）"
    modality = row.modality or "CT"
    study_uid = persisted.get("study_uid", "") or ""
    source = source_info_from_persisted(persisted, series_id[:60], study_uid, modality)
    # storage_key 读回最新 mask
    from bodydonor.storage import LocalFsStorage

    st = LocalFsStorage(sset.resolved_data_dir / "storage")
    mask_key = f"{series_id[:60]}/mask.nii.gz"
    mask_path = st.root / mask_key
    assert mask_path.exists(), f"mask 缺失: {mask_key}"

    out_dir = Path(tempfile.mkdtemp(prefix="vl-seg-"))
    seg_path = out_dir / "seg.dcm"
    rep = export_and_verify(mask_path, source, seg_path)
    assert rep.ok, f"verify_seg 报告非 ok: {rep.errors}"
    assert seg_path.exists() and seg_path.stat().st_size > 0, "SEG 导出为空"
    return {"seg_path": str(seg_path), "n_errors": len(rep.errors)}


def step11_traces(series_id: str) -> dict:
    """断言 DecisionTrace 两档（pipeline_stage）各 ≥1（DESIGN.md 决定 4）。"""
    from sqlalchemy import func, select

    from bodydonor import db as bdb
    from bodydonor.models import DecisionTrace

    engine = bdb._engine(_settings().database.url)
    try:
        with engine.connect() as conn:
            from sqlalchemy.orm import Session

            s = Session(conn)
            rows = s.execute(
                select(DecisionTrace.pipeline_stage, func.count())
                .where(DecisionTrace.series_id == series_id[:60])
                .group_by(DecisionTrace.pipeline_stage)
            ).all()
            s.close()
    finally:
        engine.dispose()
    stages = {r[0]: r[1] for r in rows}
    assert stages.get("qc_scoring", 0) >= 1, f"qc_scoring trace 缺: {stages}"
    assert stages.get("gate_decision", 0) >= 1, f"gate_decision trace 缺: {stages}"
    return {"stages": stages}


def step12_git_clean() -> dict:
    """断言 git status --porcelain 白名单外无条目（链路泄漏即红，决定 3）。"""
    proc = subprocess.run(  # noqa: S603 — git status，repo 自身
        ["git", "-C", str(REPO), "status", "--porcelain", "-z", "--no-renames"],
        capture_output=True,
        text=False,
        timeout=30,
    )
    assert proc.returncode == 0, f"git status 失败: {proc.stderr[:200]}"
    raw = proc.stdout
    dirty: list[str] = []
    # -z NUL 分隔（M7-GATE-R1 教训：$(...) 吃 NUL）——XY 后空格起为路径
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        text = entry.decode("utf-8", errors="replace")
        path = text[3:] if len(text) > 3 else text
        if not _git_exempt(path):
            dirty.append(text)
    assert not dirty, f"git status 白名单外脏: {dirty}"
    return {"dirty": [], "exempt_ok": True}


STEPS = {
    "ingest": step2_ingest,
    "segment": step3_segment,
    "qc": step4_qc,
    "review_tasks": step5_review_tasks,
    "save": step7_save,
    "mask_version": step8_mask_version,
    "export_seg": step9_10_export_seg,
    "traces": step11_traces,
    "git_clean": step12_git_clean,
}


def main() -> int:
    desc = "verify-loop 链路步骤（Step 2–12，Step 6 见 cdp_open_review.py）"
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--steps", default="all", help="逗号分隔步名（默认 all）")
    ap.add_argument("--series-id", default=None, help="续跑已有 series（缺省从 ingest 起）")
    args = ap.parse_args()

    names = list(STEPS) if args.steps == "all" else args.steps.split(",")
    state: dict = {}
    if args.series_id:
        state["series_id"] = args.series_id
    results: dict = {}
    for name in names:
        fn = STEPS[name]
        kwargs: dict = {}
        if name in ("segment", "qc", "save", "mask_version", "export_seg", "traces"):
            assert state.get("series_id"), f"步 {name} 需先 ingest（--series-id 续跑）"
            kwargs = {"series_id": state["series_id"]}
        try:
            out = fn(**kwargs)
        except AssertionError as e:
            fail = {"step": name, "ok": False, "error": str(e)[:500]}
            print(json.dumps(fail, ensure_ascii=False))
            return 1
        if name == "ingest":
            state["series_id"] = out["series_id"]
            # Step 6（cdp_open_review.py，run_chain.sh 调）取 sid 的 state 文件
            sid_file = Path(os.environ.get("VERIFY_LOOP_TMP", "/tmp")) / "series_id"
            sid_file.write_text(out["series_id"], encoding="utf-8")
        results[name] = out
    done = {"all_pass": True, "series_id": state.get("series_id"), "results": results}
    print(json.dumps(done, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

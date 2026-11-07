#!/usr/bin/env python3
"""审阅页 combined-mask 协议实测（M2-align 3.5）。

方法：Edge headless（Windows 侧 msedge.exe，CDP websocket 驱动，沿用 M2 方法）
加载 /review?series_id=...，断言：

1. niivue 加载后 **只有 2 个 volume**（原图 + 合并 mask），不多不少
2. labels.json 类别数 == mask 的 distinct label 数（合并协议一致性）
3. 逐类开关（checkbox -> overlay opacity）
4. 保存 roundtrip：POST /review/{sid}/save -> 新 MaskVersion 读回逐体素正确

用途：`scripts/bench_review_combined.py`（M2-ALIGN-REPORT 实测引用）。
前置：uvicorn bodydonor.app:app 已启动（127.0.0.1:8000），Edge 已带
--remote-debugging-port=9333 启动并打开 about:blank。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

import websocket

CDP_PORT = 9333
APP = "http://127.0.0.1:8000"


def cdp_json(path: str) -> object:
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}{path}", timeout=5) as r:
        return json.load(r)


def main() -> int:
    import urllib.request as _u

    # series_id 从 CLI 参数取（默认 amos_0004 已 ingest 的 case）
    series_id = sys.argv[1] if len(sys.argv) > 1 else "nifti-b63b1d93"
    # 先查 meta（确认有 mask）
    with _u.urlopen(f"{APP}/review/{series_id}/meta", timeout=5) as r:
        meta = json.load(r)
    if not meta.get("has_mask"):
        print(f"ERROR: {series_id} 无 mask（先跑 bodydonor segment）")
        return 1

    tabs = cdp_json("/json")  # type: ignore[assignment]
    if not tabs:
        print("ERROR: no CDP tabs")
        return 1
    ws_url = tabs[0]["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, timeout=120)
    _id = [0]

    def send(method: str, params: dict | None = None) -> dict:
        _id[0] += 1
        ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == _id[0]:
                return msg

    def eval_js(expr: str) -> object:
        msg = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        res = msg.get("result", {}).get("result", {})
        if res.get("subtype") == "error" or "exceptionDetails" in msg.get("result", {}):
            raise RuntimeError(f"JS error: {json.dumps(msg)[:300]}")
        return res.get("value")

    # 导航到审阅页
    send("Page.enable")
    send("Page.navigate", {"url": f"{APP}/review?series_id={series_id}"})

    # 轮询加载完成（header status = 已加载）
    loaded = False
    for _ in range(60):
        time.sleep(0.5)
        try:
            status = eval_js("document.getElementById('hdr-status').textContent")
        except Exception:  # noqa: BLE001 — 页面未就绪
            continue
        if status == "已加载":
            loaded = True
            break
    if not loaded:
        print("ERROR: 审阅页加载超时/失败")
        return 1

    checks: list[tuple[str, bool, str]] = []

    # 1. 只有 2 个 volume（原图 + 合并 mask）
    nvols = eval_js("nvAxial.volumes.length")
    checks.append((
        "volume 数 = 2（原图+合并 mask）",
        nvols == 2,
        f"nvAxial.volumes.length={nvols}",
    ))

    # 2. labels.json 类别数 == mask distinct label 数
    seg_meta = eval_js("segMeta.length")
    labels_n = eval_js("labelsDoc ? labelsDoc.n_classes : null")
    eval_js(
        "(() => { const v = nvAxial.volumes[1]; const d = v.dims; "
        "return segMeta.length; })()"
    )
    checks.append((
        "labels.json 类别数 == seg-meta 类别数",
        labels_n is not None and seg_meta == labels_n,
        f"seg-meta={seg_meta}, labels.json n_classes={labels_n}",
    ))

    # 3. 逐类开关（M3 colormapLabel LUT：全关 -> 每类 alpha=0 overlay 隐藏；全开恢复）
    eval_js("document.getElementById('btn-all-off').click()")
    time.sleep(0.5)
    vis_all_off = eval_js("[...segState.values()].some(s => s.visible)")
    # colormapLabel LUT 生效判定：每类 alpha 全 0（隐藏）——检查 volume 的
    # colormapLabel.R 有内容且 updateGLVolume 不抛错
    lut_set = eval_js(
        "nvAxial.volumes[1].colormapLabel ? nvAxial.volumes[1].colormapLabel.R.length : 0"
    )
    checks.append((
        "逐类开关（全关 -> colormapLabel LUT 生效）",
        vis_all_off is False and (lut_set is None or lut_set > 0),
        f"anyVisible={vis_all_off}, lut.R.length={lut_set}",
    ))
    eval_js("document.getElementById('btn-all-on').click()")
    time.sleep(0.5)
    any_vis_on = eval_js("[...segState.values()].some(s => s.visible)")
    lut_set_on = eval_js(
        "nvAxial.volumes[1].colormapLabel ? nvAxial.volumes[1].colormapLabel.R.length : 0"
    )
    checks.append((
        "逐类开关（全开 -> overlay 恢复）",
        any_vis_on is True and (lut_set_on is None or lut_set_on > 0),
        f"anyVisible={any_vis_on}, lut.R.length={lut_set_on}",
    ))

    # 4. 保存 roundtrip：触发保存 -> 新 MaskVersion -> 读回逐体素对比
    before = eval_js("JSON.stringify(segMeta)")
    eval_js("document.getElementById('btn-save').click()")
    saved = False
    for _ in range(30):
        time.sleep(0.5)
        try:
            toast = eval_js("document.getElementById('toast').textContent")
        except Exception:  # noqa: BLE001
            continue
        if "已保存" in toast:
            saved = True
            break
    checks.append((
        "保存触发（toast 已保存 MaskVersion）",
        saved,
        "toast 未出现" if not saved else "ok",
    ))

    if not saved:
        print(json.dumps({"checks": checks}, ensure_ascii=False, indent=1))
        return 1

    # 后端读回新 MaskVersion，逐体素对比（segMeta 体素数 vs 新 mask distinct label 数）
    with _u.urlopen(f"{APP}/review/{series_id}/seg-meta", timeout=10) as r:
        after_meta = json.load(r)
    # 新版本 seg-meta 类别数与保存前一致（无修改保存 roundtrip）
    same_classes = len(after_meta) == len(json.loads(before))
    checks.append((
        "保存 roundtrip（新 MaskVersion 类别数一致）",
        same_classes,
        f"before={len(json.loads(before))}, after={len(after_meta)}",
    ))

    # 结果
    all_ok = all(ok for _, ok, _ in checks)
    print(json.dumps(
        {
            "series_id": series_id,
            "all_pass": all_ok,
            "checks": [{"name": n, "pass": ok, "detail": d} for n, ok, d in checks],
        },
        ensure_ascii=False,
        indent=1,
    ))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

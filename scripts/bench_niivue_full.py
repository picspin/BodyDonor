#!/usr/bin/env python3
"""niivue 完整管线单帧耗时实测（M3，M2-REPORT 遗留 2：M2 只测了 WebGL 代理负载）。

方法：Edge headless（Windows 侧 msedge.exe，CDP websocket 驱动，沿用 M2 方法）
加载真实审阅页 /review?series_id=...（niivue 完整管线：多视口 4 canvas +
原图/mask 双 volume 叠加 + WebGL2 渲染），在页面里对每个视口连续触发
drawScene()（niivue 真实重绘路径：4 视口全渲染，含 overlay 合成），
setTimeout 链测 N 帧 mean/p95，对照 <100ms/帧红线（proposal §9.1）。

与 M2 的差别：M2 的 bench_browser_frames.py 测的是手写 WebGL2 代理页
（texSubImage3D 单切片 + drawArrays），不是 niivue 完整管线；本脚本直接对
运行中的 niivue 实例调 drawScene()（其完整渲染管线的真实入口）。

前置：uvicorn bodydonor.app:app 已启动（127.0.0.1:8000），Edge 已带
--remote-debugging-port=9333 启动并打开 about:blank。

用法：
    uv run python scripts/bench_niivue_full.py [series_id] [n_frames]
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
    series_id = sys.argv[1] if len(sys.argv) > 1 else "nifti-b63b1d93"
    n_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 100

    with urllib.request.urlopen(f"{APP}/review/{series_id}/meta", timeout=5) as r:
        meta = json.load(r)
    if not meta.get("has_mask"):
        print(f"ERROR: {series_id} 无 mask（先跑 bodydonor segment）")
        return 1

    tabs = cdp_json("/json")  # type: ignore[assignment]
    if not tabs:
        print("ERROR: no CDP tabs")
        return 1
    ws = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=180)
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

    # 导航到审阅页（niivue 完整管线加载：4 canvas + 双 volume + labels.json）
    send("Page.enable")
    send("Page.navigate", {"url": f"{APP}/review?series_id={series_id}"})
    loaded = False
    for _ in range(90):
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

    # 注入帧测量：对 4 个 niivue 实例连续 drawScene()（真实重绘入口，
    # 含多视口 + overlay 合成 + 拾取缓冲），setTimeout 链（headless 无合成器，
    # rAF 不触发，沿用 M2 方法），每帧记录耗时，跑完写 window.__benchResult
    bench_js = f"""
    (() => {{
      const nvs = [nvAxial, nvCoronal, nvSagittal, nvMip];
      const times = [];
      let i = 0;
      const N = {n_frames};
      function step() {{
        const t0 = performance.now();
        for (const nv of nvs) nv.drawScene();
        times.push(performance.now() - t0);
        i++;
        if (i < N) setTimeout(step, 0);
        else {{
          const sorted = [...times].sort((a, b) => a - b);
          const mean = times.reduce((a, b) => a + b, 0) / N;
          const p95 = sorted[Math.floor(N * 0.95)];
          const mx = sorted[N - 1].toFixed(2);
          window.__benchResult =
            `N=${{N}} MEAN=${{mean.toFixed(2)}}ms P95=${{p95.toFixed(2)}}ms MAX=${{mx}}ms`;
        }}
      }}
      step();
    }})()
    """
    eval_js(bench_js)
    result = ""
    for _ in range(120):
        time.sleep(0.5)
        try:
            val = eval_js("window.__benchResult || ''")
        except Exception:  # noqa: BLE001
            continue
        if val:
            result = str(val)
            break
    ws.close()
    if not result:
        print("ERROR: benchmark did not finish in 60s")
        return 1
    print(result)
    mean = float(result.split("MEAN=")[1].split("ms")[0])
    p95 = float(result.split("P95=")[1].split("ms")[0])
    vm = "PASS" if mean < 100 else "FAIL"
    vp = "PASS" if p95 < 100 else "FAIL"
    print(f"红线 <100ms/帧（niivue 完整管线，4 视口 drawScene）: mean {vm} / p95 {vp}")
    return 0 if mean < 100 else 1


if __name__ == "__main__":
    sys.exit(main())

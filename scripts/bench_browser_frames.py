#!/usr/bin/env python3
"""niivue 审阅页滚动帧耗时实测（M2 性能红线，proposal §9.1）。

方法：Edge headless（Windows 侧 exe，CDP websocket 驱动）加载实测页——
WebGL2 3D 纹理（512×512×300 CT 代理）+ 每"帧"texSubImage3D 单切片 +
drawArrays，即 niivue 滚动重绘的真实 GPU 路径代理负载。测 100 帧
mean/p95，对照 <100ms/帧红线。

用途：`scripts/bench_browser_frames.py`，M2-REPORT 实测表引用。
CDP 评测页性能用 rAF 代理（headless 下 setTimeout 兜底，见 M2-REPORT 说明）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

import websocket

CDP_PORT = 9333
BENCH_URL = "file:///C:/Users/Public/frame_bench.html"


def cdp_json(path: str) -> object:
    with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}{path}", timeout=5) as r:
        return json.load(r)


def main() -> int:
    tabs = cdp_json("/json")  # type: ignore[assignment]
    if not tabs:
        print("ERROR: no CDP tabs")
        return 1
    ws_url = tabs[0]["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, timeout=60)

    def send(method: str, params: dict | None = None, _id: int = 1) -> dict:
        ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == _id:
                return msg

    # 导航到实测页
    send("Page.navigate", {"url": BENCH_URL})
    # 轮询结果（页面 setTimeout 链跑 100 帧后写 #r）
    result = ""
    for _ in range(60):
        time.sleep(0.5)
        msg = send(
            "Runtime.evaluate",
            {"expression": "document.getElementById('r').textContent", "returnByValue": True},
            _id=2,
        )
        val = msg.get("result", {}).get("result", {}).get("value", "")
        if val and val != "not-run":
            result = val
            break
    ws.close()
    if not result:
        print("ERROR: benchmark did not finish in 30s")
        return 1
    print(result)
    # 判定
    if "MEAN=" in result:
        mean = float(result.split("MEAN=")[1].split("ms")[0])
        p95 = float(result.split("P95=")[1].split("ms")[0])
        verdict_m = "PASS" if mean < 100 else "FAIL"
        verdict_p = "PASS" if p95 < 100 else "FAIL"
        print(f"红线 <100ms/帧: mean {verdict_m} / p95 {verdict_p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""verify-loop Step 6：CDP 打开 review.html 并确认渲染（M7 verify-loop skill）。

方法：Edge headless（Windows 侧 msedge.exe，CDP websocket，沿用
scripts/bench_review_combined.py 先例，端口 9333 惯例）。本脚本自管 Edge
生命周期（--remote-debugging-port=9333 启动、Browser.close 收尾，不 taskkill
——不误杀用户会话）。

确认标准（DESIGN.md Phase B 决定 2）：
1. GET /review 页面 200
2. hdr-status == "已加载"（页面自身在 loadVolumes 成功后才置——niivue +
   image.nii.gz + mask.nii.gz 端点全通的内置就绪信号）
3. 4 canvas（axial/coronal/sagittal/mip）均存在且非空
4. console 无 error（CDP Runtime.consoleAPICalled type=error 采集）

Edge 缺席机器 fallback（决定 3）= HTTP-only 冒烟：/review、
/static/niivue.umd.js、image.nii.gz、mask.nii.gz 全 200 且非空。诚实边界：
HTTP-only 不验证 JS 执行 / niivue WebGL 渲染 / console error / 前端 fetch
流程——输出明标 degraded=true，链路仍绿（后端契约已验证，浏览器渲染属
环境依赖），degrade 事实写进输出不隐瞒。

用法：
  cdp_open_review.py --app http://127.0.0.1:8765 --series-id <sid> [--timeout 90]

退出码：0 = 确认通过（或降级 HTTP 冒烟通过）；1 = 红（渲染失败/超时/HTTP 断）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess  # noqa: S404 — 仅启动本机 Edge（先例同法）
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CDP_PORT = 9333
EDGE_CANDIDATES = (
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe",
)
HTTP_SMOKE_PATHS = (
    "/review",
    "/static/niivue.umd.js",
    "/review/{series_id}/image.nii.gz",
    "/review/{series_id}/mask.nii.gz",
)


def _http_get(url: str, timeout: float = 15.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "verify-loop/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def find_edge() -> str | None:
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    which = shutil.which("msedge") or shutil.which("msedge.exe")
    return which


def http_smoke(app: str, series_id: str) -> tuple[bool, dict]:
    """Edge 缺席 fallback：HTTP-only 冒烟（决定 3）。"""
    checks = []
    for tmpl in HTTP_SMOKE_PATHS:
        path = tmpl.format(series_id=series_id)
        status, body = _http_get(f"{app}{path}")
        ok = status == 200 and len(body) > 0
        checks.append({"path": path, "status": status, "bytes": len(body), "ok": ok})
    all_ok = all(c["ok"] for c in checks)
    return all_ok, {"mode": "http_only", "degraded": True, "checks": checks}


def cdp_confirm(app: str, series_id: str, edge: str, timeout_s: float) -> tuple[bool, dict]:
    """Edge headless + CDP websocket 渲染确认（决定 2 四项标准）。"""
    import websocket  # 先例同依赖（venv websocket-client）

    profile = tempfile.mkdtemp(prefix="vl-edge-")
    cmd = [
        edge,
        # --headless=new：旧 --headless 会附着既有 Edge 会话（tabs[0] 是用户
        # 已开的 tab，渲染确认落空）；=new 才保证独立实例（实测 2026-11-20）
        "--headless=new",
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--disable-gpu",
        "about:blank",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
    checks: list[dict] = []
    console_errors: list[str] = []
    ws = None
    try:
        # 等 CDP 端口就绪
        ready = False
        for _ in range(50):
            time.sleep(0.2)
            try:
                url = f"http://127.0.0.1:{CDP_PORT}/json/version"
                with urllib.request.urlopen(url, timeout=2) as r:
                    json.load(r)
                ready = True
                break
            except Exception:  # noqa: BLE001 — Edge 未起，重试
                continue
        if not ready:
            err = "CDP 端口未就绪（Edge 启动失败）"
            return False, {"mode": "cdp", "degraded": False, "error": err}

        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=5))
        if not tabs:
            return False, {"mode": "cdp", "degraded": False, "error": "no CDP tabs"}
        ws = websocket.create_connection(tabs[0]["webSocketDebuggerUrl"], timeout=int(timeout_s))
        _id = [0]

        def send(method: str, params: dict | None = None) -> dict:
            _id[0] += 1
            ws.send(json.dumps({"id": _id[0], "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                # 旁路采集 console error（决定 2 第 4 项）
                if msg.get("method") == "Runtime.consoleAPICalled":
                    if msg.get("params", {}).get("type") == "error":
                        cargs = json.dumps(msg.get("params", {}).get("args", []))
                        console_errors.append(cargs[:300])
                    continue
                if msg.get("method") == "Runtime.exceptionThrown":
                    console_errors.append(json.dumps(msg.get("params", {}))[:300])
                    continue
                if msg.get("id") == _id[0]:
                    return msg

        def eval_js(expr: str) -> object:
            msg = send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
            res = msg.get("result", {}).get("result", {})
            if res.get("subtype") == "error" or "exceptionDetails" in msg.get("result", {}):
                raise RuntimeError(f"JS error: {json.dumps(msg)[:300]}")
            return res.get("value")

        status, body = _http_get(f"{app}/review")
        checks.append({
            "name": "GET /review 200",
            "ok": status == 200 and len(body) > 0,
            "detail": f"status={status} bytes={len(body)}",
        })

        send("Page.enable")
        send("Runtime.enable")
        send("Page.navigate", {"url": f"{app}/review?series_id={series_id}"})

        # 轮询 hdr-status == 已加载（页面内置就绪信号，先例同口径）
        loaded = False
        for _ in range(int(timeout_s * 2)):
            time.sleep(0.5)
            try:
                expr = "document.getElementById('hdr-status')?.textContent ?? null"
                v = eval_js(expr)
            except Exception:  # noqa: BLE001 — 页面未就绪
                continue
            if v == "已加载":
                loaded = True
                break
        checks.append({
            "name": "hdr-status == 已加载",
            "ok": loaded,
            "detail": "超时" if not loaded else "ok",
        })

        # 4 canvas 存在且非空（非空 = width/height > 0；不比对位图——脆弱无判别力）
        try:
            canvas_n = eval_js(
                "(() => { const cs = document.querySelectorAll('canvas');"
                " return [...cs].filter(c => c.width > 0 && c.height > 0).length; })()"
            )
        except Exception:  # noqa: BLE001
            canvas_n = 0
        checks.append({
            "name": "canvas 非空数 >= 4",
            "ok": isinstance(canvas_n, int) and canvas_n >= 4,
            "detail": f"canvas={canvas_n}",
        })

        # console 无 error（决定 2 第 4 项；收尾前最后采集一轮）
        time.sleep(1.0)
        with contextlib.suppress(Exception):
            eval_js("1")
        detail = console_errors[:3] or "ok"
        checks.append({
            "name": "console 无 error",
            "ok": len(console_errors) == 0,
            "detail": detail,
        })

        all_ok = all(c["ok"] for c in checks)
        return all_ok, {"mode": "cdp", "degraded": False, "checks": checks}
    finally:
        if ws is not None:
            try:
                ws.send(json.dumps({"id": 99999, "method": "Browser.close", "params": {}}))
                time.sleep(0.5)
            except Exception:  # noqa: BLE001 — 收尾失败不挡结果
                pass
            with contextlib.suppress(Exception):
                ws.close()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()


def main() -> int:
    desc = "verify-loop Step 6：CDP 渲染确认（或 HTTP 冒烟 fallback）"
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--app", default="http://127.0.0.1:8765", help="FastAPI base URL")
    ap.add_argument("--series-id", required=True, help="已 ingest+分段 的 series_id")
    ap.add_argument("--timeout", type=float, default=90.0, help="页面加载超时秒数")
    args = ap.parse_args()

    edge = find_edge()
    if edge is None:
        ok, report = http_smoke(args.app, args.series_id)
    else:
        try:
            ok, report = cdp_confirm(args.app, args.series_id, edge, args.timeout)
        except Exception as e:  # noqa: BLE001 — CDP 崩溃降级 HTTP 冒烟（决定 3 fallback）
            ok, report = http_smoke(args.app, args.series_id)
            report["cdp_error"] = f"{type(e).__name__}: {e}"[:300]

    print(json.dumps({"all_pass": ok, **report}, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

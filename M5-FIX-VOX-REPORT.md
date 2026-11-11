# M5 提示落点修复报告（bug 1：scene.vox）

- 日期：2026-11-11；系列：`nifti-b63b1d93`（amos_0004），体数据 512×512×78
- 批准：用户 2026-11-11 16:41“先修提示落点”；只修这一处，不碰 submit-nni 文件名（bug 2 另节）
- 提交：`713e354`、`10340e4`（本地，main 领先远端 6 提交、未推）
- 验证：真实 Chromium 149（Playwright）+ 真实 GPU 推理，无 mock

## 1. 问题

`src/bodydonor/static/review.html` 3 处用 `scene.vox`（`syncSlidersFromVox`、
切片滑条写、`m5CurrentVox()`），但 vendored niivue 0.53.1 里根本没有 `scene.vox`
（bundle 内 0 次出现）。于是 `m5CurrentVox()` 恒回 `(0,0,0)`，画布上任何提示都
落在体数据角落。run3 实证：`scene.vox` 为 null、`crosshairPos≈(0.499,0.5,0.5)`；
sidecar prompts `[{x:0,y:0,z:0}]`；修正 mask 的 32 体素全在 x/y/z∈[0,3]。

## 2. API 核对依据（读 vendored bundle，不凭记忆）

- `scene.crosshairPos`：fractional 坐标 [0,1]，默认 `G(.5,.5,.5)`。
- `frac2vox(frac)` → `convertFrac2Vox`：`round(frac[i]*dims[i+1]-0.5)`，
  与 niivue 内部 `onLocationChange` 的 `vox: frac2vox(scene.crosshairPos)` 同式。
- `dragModePrimary` 默认 `0`；DRAG_MODE_PRIMARY 枚举为 `{crosshair:0, windowing:1}`，
  故左键点击/拖动会经 `mouseClick` 把 `crosshairPos` 落到点击处。
- `moveCrosshairInVox(dx,dy,dz)`：内部即 frac2vox + clamp + vox2frac 回写 + 重绘，
  是体素空间移动十字线的标准 API。
- `canvasPos2frac([x,y])`：入参 device px（`setDragStart/setDragEnd` 内 ×dpr），
  返回首个命中切片的 fractional 坐标，体外返回 `[-1,-1,-1]`；`screenXY2TextureFrac`
  的 z 分量即当前切片。`mouseClick` 落十字线与它同源。

## 3. 修复内容（`review.html`，仅提示落点）

提交 `713e354`：

- `syncSlidersFromVox` / `m5CurrentVox`：`scene.vox ?? [0,0,0]` 改为
  `frac2vox(scene.crosshairPos)`。
- 切片滑条：`scene.vox` 赋值是空操作，改为
  `moveCrosshairInVox(0, 0, z - frac2vox(crosshairPos)[2])`。

提交 `10340e4`（bbox 实测暴露的第二层问题，见 §4.2）：

- `pointerdown` 先于 niivue 的 `mousedown` 触发，此时 `crosshairPos` 还是上一次的
  位置，直接读会落后一次点击。`m5CurrentVox(ev)`：有指针事件时按事件坐标经
  `canvasPos2frac`（CSS px × dpr）+ `frac2vox` 换算，体外点返回 null；无事件时
  仍读当前 `crosshairPos`（滑条/传播按钮）。`pointerdown`/`pointermove`/
  `pointerup` 三处传 `ev`；`pointerup` 补 `(ev)` 形参（初版漏写曾导致 `ev`
  未定义，提交前已修正并经 `node --check` 与浏览器重验确认）。

其余逻辑未动；`app.py` 的 3 处 `mask_key.replace("image.nii.gz", …)` 留着别动（bug 2）。

## 4. 浏览器实测（Chromium 149，`~/workspace/bodydonor/browser-test/`，脚本 `review-test-vox.mjs`）

每项均为独立浏览器会话（`run4-*`，含 `results.json` 与截图）；页面加载约 160–180s
（17MB 原图 + mask 经代理加载）；推理走真实 GPU（cuda）。

### 4.1 point（两轮）

| 轮次 | 点击（画布相对坐标） | 前端记录 prompt | sidecar prompts | 修正 mask（体素/包围盒/质心） | 检查 |
|---|---|---|---|---|---|
| run4-point（`713e354`） | (35%, 40%) = 客户区 (245.38, 235.92) | point (142, 308, 38) | (142.0, 308.0, 38.0) | 5604 体素；x[122,168] y[299,317] z[29,47]；质心 (141.9, 308.1, 38.3) | 8/8 |
| run4-point2（`10340e4`，终版代码回归） | (35%, 40%) = 客户区 (245.38, 231.60) | point (139, 307, 38) | (139.0, 307.0, 38.0) | 2977 体素；x[127,161] y[300,315] z[30,44]；质心 (143.0, 307.5, 37.5) | 8/8 |

- 两轮点击的客户区 y 不同（235.92 vs 231.60）是画布高度随布局在 454–465px 间浮动，
  同一相对坐标 (35%, 40%) 映射到 (142,308) vs (139,307)，均等于实时
  `frac2vox(crosshairPos)`，提示即点击处，修正体素包围点击点而非角落。
- 附：`/nninteractive-correct` 请求体与前端记录完全一致；推理 HTTP 200；
  point 轮 load 10.57s / infer 1.9s。

### 4.2 bbox（拖拽，修复前后对照）

拖拽：画布 (30%, 35%) → (55%, 60%)，即客户区 (211.04, 208.90) → (382.74, 322.40)。

| 轮次 | 前端记录 bbox | sidecar bbox | 修正 mask | 检查 |
|---|---|---|---|---|
| run4-bbox（`713e354`，修前） | [[256,294],[205,257],[38,39]] | 同左 | 6229 体素；x[252,299] y[206,260] z[28,50] | 7/7（检查项通过，但起点错） |
| run4-bbox2（`10340e4`，修后） | [[100,295],[204,333],[38,39]] | 同左 | 1,552,384 体素；x[97,362] y[165,352] z[0,77] | 7/7 |

- 修前起点 (256,256,38) 是滑条复位后的旧十字线（体中心），不是拖拽起点；
  按画布仿射映射（point 轮两点 + 终点十字线 (293,205,38) 标定）拖拽起点应约
  (104,334,38)。修后起点 (100,332) 与标定值吻合（±4 体素内，含布局浮动与
  取整）；终点 (294,204) 与实时十字线 (293,205) 差 ±1（亚体素取整）。
- bbox 提示本就是区域级：修正 mask 包围盒覆盖提示框并向外延展，符合预期；
  修前 mask 锚定在体中心附近，修后跟随实际拖拽矩形。

### 4.3 scribble（笔画）

笔画 5 个航点：(0.38,0.42) → (0.44,0.45) → (0.50,0.44) → (0.55,0.48) → (0.58,0.52)。
前端记录 25 点，首点 (162,296,38)、末点 (317,245,38)；sidecar 25 点完全一致；
修正 mask 7779 体素，x[158,330] y[234,301] z[33,47]，质心 (252.2,275.3,39.5)，
沿笔画轨迹分布。检查 7/7，推理 HTTP 200，草稿正常出现。

### 4.4 滑条与切片显示（同页验证）

- 加载后 `slice-pos` 显示 `slice 40/78 · vox (256,256,39)`（修前恒 `(0,0,0)`）。
- 滑条拨到 19：`slice 20/78 · vox (256,256,19)`，`frac2vox` 同步为 [256,256,19]。

### 4.5 测试过程注记

- 首轮 point 曾因测试脚本先 `slider.fill()` 使焦点落在 range input 上，
  窗口 `keydown` 处理器对 input 目标直接 return，`p` 键未切模式、提示未记录。
  这是测试脚本问题（已在脚本里 blur 后按键），不是产品 bug；产品侧键盘守卫是
  有意设计（避免在输入框里误触模式键）。
- 各轮均有 1 条 404 console error，与 run3 相同，为既有现象，非本次引入。

## 5. 清理（已做）

- 删除测试产生的 `nninteractive-correction/` 目录；未走 submit，全程未建
  MaskVersion 行。
- `mask.nii.gz` md5 `2b228392bac46a21a039e598149a961d`、753545 字节，与测试前基线
  一致（mtime 仍为 2026-11-11 15:46）。
- DB：MaskVersion 仍为 v1–v9（v1–5 teacher、v6–9 manual），总数 10 行不变；
  ModelVersion `gate_status=passed`；alembic `b8e21c4a7f05`。
- 转发代理（127.0.0.1:18082）已停；uvicorn :8000（PID 223000）未动。

## 6. 遗留

- `slice-pos` 文本只在滚轮/滑条时刷新，点击后不自动更新（既有行为，非本次范围）。
- `m5CurrentVox(ev)` 的事件坐标路径依赖 `canvasPos2frac` 与 `uiData.dpr` 两个
  niivue 0.53.1 内部成员；升级 niivue 大版本时需复核。
- bug 2（submit-nni 版本文件名）见下节。

## 7. bug 2：submit-nni 版本文件名

- 日期：2026-11-11；系列：`nifti-b63b1d93`（amos_0004）
- 提交：见 §8（fix + 报告各一 commit，本地 main、未推）
- 验证：真实 API 提交 + 真实 GPU 推理（nninteractive-correct），无 mock

### 7.1 问题

`src/bodydonor/app.py` 3 处（912 / 1199 / 1431 行）写版本文件 key 用

```python
key = mask_key.replace("image.nii.gz", f"mask_v{version}.nii.gz")
```

但 `mask_key` 本身来自 `row.storage_key.replace("image.nii.gz", "mask.nii.gz")`
（app.py:842/1132/1369），值已是 `amos_0004/nifti-b63b1d93/mask.nii.gz`，不含
`"image.nii.gz"` —— 第二次 replace 是 no-op，`key` 恒等于 `mask.nii.gz`，每次
提交都覆盖同一个文件。DB 实证：该 series 的 v1–v9 共 9 行 MaskVersion 全指同一
storage_key `amos_0004/nifti-b63b1d93/mask.nii.gz`（v1–5 teacher、v6–9 manual）。

### 7.2 修复内容

3 处一致改为从 `row.storage_key`（image 路径）派生版本 key：

```python
key = (row.storage_key or "").replace("image.nii.gz", f"mask_v{version}.nii.gz")
```

| 位置 | 端点/函数 | 改后 key（version=10 例） |
|---|---|---|
| app.py:912 | `POST /review/{series_id}/save`（review_save） | `amos_0004/nifti-b63b1d93/mask_v10.nii.gz` |
| app.py:1199 | `POST /review/{series_id}/propagate`（review_propagate） | 同上式 |
| app.py:1431 | `POST /review/{series_id}/submit-nni`（review_submit_nni） | 同上式 |

未动 `review.html`（bug 1 已修）、eval/holdout.txt、M4 门禁、蒸馏门禁与数据；
未迁移历史行（v1–v9 原样保留，见 §7.4）。

### 7.3 验证（真实提交，两轮）

流程：每轮先 `POST /review/nifti-b63b1d93/nninteractive-correct`（point 提示
(139,307,38) include=true，target_label=1，真实 GPU 推理），再
`POST /review/nifti-b63b1d93/submit-nni`（同 prompts + `class: "label-1"`，
acceptedClasses=["label-1"]，sessionId 区分轮次，baselineTeacherVersion=null）。

| 轮次 | version | storage_key | md5 | 字节 | 推理 |
|---|---|---|---|---|---|
| sess-1 | 10 | `amos_0004/nifti-b63b1d93/mask_v10.nii.gz` | `98baa831909deded8de4d9ef9e7d6812` | 754786 | 11.37s / 2979 体素 |
| sess-2 | 11 | `amos_0004/nifti-b63b1d93/mask_v11.nii.gz` | `42fa264f4fe83c8ddf6ef42b45df8945` | 754777 | 11.47s / 2977 体素 |

- 连续两次提交生成两个不同的 storage_key、两个不同的磁盘文件、内容不同
  （md5 与字节数各异）—— no-op 修复生效。
- 旧行核对：v1–v9 的 storage_key 均保持 `amos_0004/nifti-b63b1d93/mask.nii.gz`
  语义不变，历史行未被改写；本修法不需要迁移历史行。
- 过程注记：首轮 submit 被 422 拒一次——provenance 的 prompts 须经
  `validate_prompt` 校验，每条提示须带非空 `class` 字段（review_session.py:86），
  补 `"class": "label-1"` 后通过。`nninteractive-correct` 端点无此要求（与
  runner 契约一致），仅 submit-nni 的 provenance 重放校验要求。
- uvicorn 重启：PID 223000 → 231261，同一命令（`--host 0.0.0.0 --port 8000`），
  `/health` 返回 `{"status":"ok","version":"0.1.0"}`。

### 7.4 清理与复核（已做）

- 删除测试产生的 MaskVersion v10、v11 两行（`deleted rows: 2`），未动其他行。
- 删除 `mask_v10.nii.gz`、`mask_v11.nii.gz` 与 `nninteractive-correction/` 目录。
- `mask.nii.gz` 未被提交覆盖（两次提交都写 v10/v11 新文件），md5
  `2b228392bac46a21a039e598149a961d`、753545 字节，与基线一致（/tmp 快照未启用恢复）。
- DB 复核：MaskVersion 回到 v1–v9、总数 10 行；ModelVersion `gate_status=passed`；
  alembic `b8e21c4a7f05` 不变。
- nnInteractive 修正产物未进蒸馏训练集（未动 distill 门禁与数据）。

### 7.5 测试与提交

- pytest 全套：**464 passed**，exit 0（`.venv/bin/python -m pytest`，189.10s）。
- ruff：`ruff check src tests scripts eval alembic config` **All checks passed**，exit 0。

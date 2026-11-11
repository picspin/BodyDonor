# M5-REPORT — BodyDonor M5 立项迭代补丁交付报告

> 日期：2026-11-11。M5 补丁 6 个提交（本地 main，未推）：`c2b2901` 审阅会话
> 状态模型 → `a431478` 传播 + 风险队列端点 → `dcd5cd2` niivue 审阅屏 →
> `539ffe6` M5-e 血缘修正 → `0a292dd` GPU 探针 → `9353f6e` submit-nni
> 基线自动解析 → 本报告。
> 依据：~/M5-PLAN.md（2026-11-09 用户定）、M4-REPORT.md §5.1 遗留、
> LICENSES-THIRD-PARTY.md §8。非交互运行；全程未 push、未建 PR、未改
> eval/holdout.txt、未改 M4 门禁结果。

---

## 1. 各任务交付清单

### 1.1 修正传播（最优先，M5-PLAN §3.1）

- **`review_session.propagate_correction`**：本层某类形态精确复制到相邻
radius 层空白处，不膨胀（避免逐层外扩）、不越类（绝不覆盖其他已有
类别体素）；返回 (new_arr, 总 voxel 数，{层号: 该层数})。
- **端点 `POST /review/{series_id}/propagate`**：affine 守卫同 review_save；
0 传播拒建空版本（422）；写回新 MaskVersion source=manual（画笔修正语义）。
- 实测：fixture mask label-1 在 z=5..14（每层 100 体素），取边缘层 z=14
radius=1 → 只向空白 z=15 传播 100 体素（z=13 已有该类不重复写）；
z=0 层无该类 → 422。

### 1.2 M5-a 状态模型与模式切换

- **`review_session.PromptSession`**：EditMode（browse / prompt_pos /
prompt_neg / bbox / scribble / paint / erase）+ 键盘映射 P/N/B/S
（PLAN 的 + - & * 为符号映射，UI 同标符号）+ 空格平移优先
（space_held 时 click_allowed() 为 False）+ Esc 回 browse 不清提示。
- **前端**：画布角落持续可见状态徽标（模式 + 当前类 + 提示数 + 推理中 +
草稿/确认角标）；悬停预览提示落点（+ / − / & / * 标记，不触发推理）。
- 验收（单测）：切模式不丢已有提示；Esc 只改模式不清提示；空格按住不落提示。

### 1.3 M5-b 草稿生命周期

- **`review_session.DraftStore`**：per-class teacher/draft/accepted；
accept 单类不影响其他类；discard 回 teacher 且提示标记清空；
submit_payload() 校验 acceptedClasses 非空 + prompts 全量可重放，
产出 {sessionId, baselineTeacherVersion, prompts[], acceptedClasses[]}。
- **前端**：每类“放弃本类 / 确认本类”按钮；未提交切病例（离开页面）时
beforeunload 确认框；提交按钮逐类调 submit-nni 建 MaskVersion 行。

### 1.4 M5-c 第三视觉态渲染

- **`RENDER_SOURCES`**：teacher（半透明实色 0.6）/ gt_human（低填充亮色轮廓
0.15）/ guided_draft（低透明 0.25 + 虚线琥珀 + 角标 `nnInteractive · 未确认`）
/ guided_accepted（实色 0.6 + 亮青实线 + 角标 `nnI ✓`），4 种
(fill_alpha, outline) 两两可区分（单测锁定）。
- **前端**：LUT alpha 按渲染源 × 审阅模式缩放；draft↔accepted 切换只改
LUT 属性不重载 volume（无闪烁）；提示标记层（正提示青点、负提示红叉、
框、涂鸦轨迹）跨切片可见（按类存，不随切片清空）。
- 诚实边界（见 §3.3）：niivue 0.53.1 的 colormapLabel LUT 按 label 值着色，
无描边通道——“虚线轮廓”表达为低透明填充 + 琥珀色 + 角标 + 类别行虚线
边框，不是逐像素描边；真描边需第二 overlay 通道（后置）。

### 1.5 自适应渲染开关（M5-PLAN §1.2）

- **后端 `default_render_mode(modality, task_kind)`**：边界关键任务
（task_kind=boundary）与 CT/MRI 默认精细边界（fine）；检测/定位任务
（task_kind=detection）与 X-ray 默认快速定位（quick）。
- **前端**：“快速定位 / 精细边界”切换（quick：轮廓弱化、色块强化，
alpha×1.25；fine：GT 与预测边界差异强化，偏差区域脉动提示，
prefers-reduced-motion 时 CSS 动画自动停用）+ “一键剥离 overlay”
（隐藏全部 overlay 只看原图，可恢复）；高风险类行在 fine 模式下脉动高亮。

### 1.6 M5-d 推理调度

- **`review_session.InferenceScheduler`**：version 递增；只有最新 version
的结果被接受，旧 version / 已 cancel 的结果丢弃（AbortController 等价语义）；
`merge_prompts`（同类同向 ±1 体素去重）；`local_roi`（提示周围 margin
包围盒，裁剪到 volume 形状）。
- **前端**：快速连点只发最新一次请求（300ms debounce + version 比对，
旧结果丢弃）；推理为异步 fetch，推理中仍可平移/切切片（UI 不锁）。
- 验收（单测）：乱序结果丢弃、cancel 后结果无效、合并去重计数、ROI 裁剪。

### 1.7 M5-e 血缘修正（M4 §5.1 遗留关闭）

- **模型**：`MaskVersion.source` String(16)→String(32)；
新增 `provenance` JSON 列（nullable）。
- **迁移 `e7f3a1c9d204`**：开发库实测升级（先 `stamp 3c17abaf19f7`——库由
create_all 建成、从未 stamp——再 `upgrade head`，version=e7f3a1c9d204，
10 行保留，FK 检查空，20 字符行插入 + provenance 回读实测）。
- **迁移基础设施修复（同提交，见 §3.5）**：alembic env 迁移连接改 FK=OFF
（批模式 DROP 被引用的 mask_versions 表时 FK=ON 报 IntegrityError；
pragma 必须在 connect 时设——Connection 上 exec 会开隐式事务，
SQLite transactional_ddl=False 下 alembic 不提交、改动静默 ROLLBACK）。
- **端点 `POST /review/{series_id}/submit-nni`**：修正 mask（二值）
合并进基线 → 新 MaskVersion source=nninteractive_guided + provenance 列
（+ targetLabel / correctionSidecar）；无 sidecar / target 不一致 /
空修正 mask 均 422。`review_save` 接 source=manual|human + provenance
（前端画笔保存传 human；guided 走 submit-nni，save 拒收）。
- **基线自动解析（`9353f6e`）**：前端未带 baselineTeacherVersion 时，
后端复用 M4 `_lookup_baseline_teacher_version` 查最新 MaskVersion
版本号（修正所基于的显示基线）；查不到记 null + 原因，不猜数字。
实测：先 save 建 v1 再提交（不带 baseline）→
provenance.baselineTeacherVersion=1。
- **门禁不倒退**：规范名 + legacy 别名（manual-correction-nninteractive /
nninteractive）仍拒绝；纯画笔 human 允许（LICENSES §8.1 商用安全路径）。
- 实测：提交 16 体素修正 → 新版本 v1（source=nninteractive_guided，
n_corrected_voxels=16，基线 label-1 区域 100 体素保留）；两类修正
group-by 计数可分别统计（human 2 / nninteractive_guided 1 / manual 1 /
teacher 1，单测锁定）。

### 1.8 SegMed GPU 探针（M5-PLAN §1.1）

- **`scripts/gpu_probe.py`**：nvidia-smi 检测（产品名含逗号从右解析、
GPU- UUID 校验）→ 算力排序 → 实跑 CUDA 验算（1024×1024 matmul + 同步）
通过才算选中 → 不行 CPU 兜底 → --sample-file 离线验证；GPU 锁
interactive wait=0（占卡时 CPU 兜底不排队）。
- **关键决定：不设驱动版本号门槛**。SegMed CU12.6≥560.76 / CU12.8≥570.65
会把本机 Quadro（556.18）误判 CPU-only；实测 torch 2.5.1+cu124 /
CUDA 12.4 在 556.18 上验算通过，选 CUDA。
- 实测（~/m5-logs/gpu-probe.json）：backend=cuda，Quadro RTX 5000，
驱动 556.18，算力 7.5，16384 MiB；torch 2.5.1+cu124，CUDA 12.4，
验算 elapsed 0.8s（wall 2.671s），torch 峰值 16.1 MiB；锁已释放、
显存回基线 620 MiB。离线模式：backend=cpu + 候选 + 未验算说明；
sample-file 不存在退出 2。

### 1.9 异常驱动队列（M5-PLAN §3.3）

- **`review_session.risk_queue`**：多教师一致性 flag 优先，其次逐类 Dice
升序；high = flagged 或 Dice < 0.80，medium = 0.80–0.90，low > 0.90。
- **端点 `GET /review/{series_id}/risk-queue`**：数据源
eval/student_holdout_results.json（M4 实测 15 类 Dice）+
eval/student_teacher_consistency.json（below_080_classes，阶段 5）；
文件缺失报 503，不编造数据。
- **前端**：右侧“优先审阅队列”面板，点类名聚焦该类（仅显示该类）；
高风险行在 fine 模式下脉动高亮。
- 实测（M4 真实数字）：首位 prostate 0.6099（high），前 5 为 prostate /
duodenum / adrenal_gland_left / adrenal_gland_right / gallbladder
（5 个 flagged high 按 Dice 升序），末位 liver 0.9433（low）。

---

## 2. 实测验证表（每项写清在真机上怎么验的）

| 验证 | 方式 | 结果 |
|---|---|---|
| **审阅会话状态模型** | `pytest tests/test_review_session.py`（合成小数组 + M4 实测 Dice） | **26 项全绿** |
| **传播端点** | TestClient + fixture mask（label-1 z=5..14 每层 100 体素） | z=14 → z=15 传播 **100 体素**；z=0 无该类 → 422；越界 slice → 422 |
| **风险队列端点** | TestClient + eval 真实 JSON | 15 类；首位 prostate 0.6099 high、末位 liver 0.9433 low |
| **前端静态标记** | TestClient GET /review 断言 16 个 M5 标记存在 | 全在（只断言存在，不谎称渲染实测） |
| **前端 JS 语法** | esprima 解析（ES2020 `?.`/`??`/`?.(`/`?.[` 中和后；HEAD 对照同过） | HEAD 与 WORK 均 OK；16 个 M5 标记全在 |
| **M5-e 迁移（空库）** | 空库 `alembic upgrade head`（subprocess 实测） | version=e7f3a1c9d204；source VARCHAR(32)；provenance 列在；FK 检查空；20 字符行 + provenance 回读 OK |
| **M5-e 迁移（开发库）** | stamp + upgrade head（bodydonor.db，ignored） | version=e7f3a1c9d204；10 行保留；FK 检查空；gate_status 仍 passed |
| **submit-nni** | TestClient + 合成二值修正 mask（16 体素）+ sidecar | 新版本 source=nninteractive_guided，n_corrected_voxels=16，基线 100 体素保留；无产物 / target 不一致 → 422 |
| **GPU 探针真机** | `.venv/bin/python scripts/gpu_probe.py --python.venv-nninteractive/bin/python --json`（持 GPU 锁） | **backend=cuda**（驱动 556.18）；torch 2.5.1+cu124 / CUDA 12.4；elapsed 0.8s；峰值 16.1 MiB；锁已释放、显存回 620 MiB |
| **GPU 探针离线** | `--sample-file`（实录 CSV）/ 不存在文件 | 离线 backend=cpu + 候选 + 未验算说明；不存在退出 2 |
| **全套 pytest** | `.venv/bin/python -m pytest -q`（~207s；总结行未落盘，以进度点计数复核） | **446 passed, 0 failed**（M4 基线 394 → 本轮 +52：review_session 26 + review_api 13 + m5e 5 + gpu_probe 8） |
| **ruff** | `ruff check src tests scripts eval alembic config` | 全绿 |

---

## 3. 诚实结论（不粉饰）

### 3.1 前端渲染未经浏览器实测

review.html 的 M5 交互（键盘模式切换、悬停预览、草稿按钮、风险队列点击、
传播按钮、推理 version 去重）只经过：esprima 语法校验 + served HTML 静态
标记断言 + 后端单测。**niivue 画布上的真实渲染与点击行为未经浏览器实测**
（本任务无 live browser）。M5-REPORT 不声称“审阅屏已验证可用”，只声称
“代码已落地、语法与标记已校验”。浏览器实测待用户或有浏览器能力的代理做。

### 3.2 bbox / scribble 提示后端现仅支持点提示

前端已实现 bbox 拖拽与 scribble 轨迹记录（提示存入会话、可重放、随提交
payload 保存），但 `/review/{sid}/nninteractive-correct` 现只接 point /
negative_point——bbox/scribble 提示**不触发推理**，前端已用 toast 明示。
接 bbox/scribble 推理是 M5-e 后的工作（nni_runner 侧本就支持 bbox prompt，
见 nninteractive_backend.correct_image 的 prompts 契约）。

### 3.3 虚线轮廓是“表达为”，不是逐像素描边

niivue 0.53.1 的 colormapLabel LUT 按 label 值着色、无描边通道。M5-c 的
“虚线轮廓 + 低透明实色”在画布上表达为：低透明填充（0.25）+ 琥珀色 +
角标 `nnInteractive · 未确认` + 类别行虚线边框。4 种 source 在
(fill_alpha, outline) 描述层两两可区分（单测锁定），但画布上没有逐像素
虚线描边。真描边需第二 overlay 通道（轮廓提取 + 独立 volume），后置。

### 3.4 baselineTeacherVersion 的来源（已解决）

submit-nni 的 provenance.baselineTeacherVersion：前端送 null 时，后端
复用 M4 `_lookup_baseline_teacher_version` 查最新 MaskVersion 版本号
（修正所基于的显示基线）；查不到记 null + 原因，不猜数字（`9353f6e`
实测：save 建 v1 后提交 → baseline=1）。前端仍送 null 是有意的
（本页 meta 未带基线版本，不在前端猜）。

### 3.5 迁移静默 ROLLBACK 教训（已修复，有复现）

M5-e 迁移首次在开发库上“成功”却是假的：alembic RC=0、无报错，但临时表
空留、版本未变、原表未动。根因是 env.py 补丁在 Connection 上
exec_driver_sql(PRAGMA foreign_keys=OFF)，开了一个隐式事务；SQLite
transactional_ddl=False 下 alembic 不提交它，连接关闭时静默 ROLLBACK。
复现：echo 日志显示 CREATE/INSERT/DROP/RENAME/UPDATE 全跑完后 ROLLBACK。
修复：pragma 改在 connect 监听器里设（事务外），迁移连接 FK=OFF，
应用连接仍 FK=ON；之后升级一次成功（version=e7f3a1c9d204）。另发现：
开发库从未 stamp（create_all 建成），首次 upgrade head 因 M0 迁移重建
已存在的表而失败——stamp 后才可升级；此为既有状态，非 M5 引入。

### 3.6 M0 迁移与模型已漂移（新发现，本轮不动）

空库 create_all 与 upgrade head 的 schema 对比：`series.source_instances_json`
（M2-5(a) 加的列）只在模型里，不在 M0 迁移里。M0 迁移是 stale 的；本次
stamp 3c17abaf19f7 时已在报告里写明此偏差。修 M0 迁移（补列）是独立事项，
留给用户定（不动，避免扩大 M5-e 范围）。

### 3.7 数字口径说明

- 风险队列用的学生 Dice（prostate 0.6099 等）与一致性 below_080 类均取自
M4 运行产物 eval/*.json（不入仓）；文件缺失时端点报 503，不编造。
- GPU 探针的“选 CUDA”结论只针对本机实测组合（Quadro RTX 5000 + 驱动
556.18 + torch 2.5.1+cu124）；换卡/换驱动须重跑探针，不通用化。
- pytest 基线：M4 报告 394 项；本轮新增 52 项（review_session 26 +
  review_api 13 + m5e 5 + gpu_probe 8），合计 446 项全绿。另注意：本轮
  两次全套运行的 pytest 总结行均未落盘（输出在 warnings 汇总处截断，
  退出码 0）——数字以进度点逐行计数复核（445 点时为 445 项，+1 基线
  测试后 446 项），非读总结行。

---

## 4. 非目标清单（M5 补丁不做项）

- nnInteractive server 模式（M3 §9 遗留不变）
- bbox/scribble 提示的后端推理接线（§3.2）
- 第二 overlay 通道的逐像素轮廓描边（§3.3）
- M0 迁移补 series.source_instances_json（§3.6）
- 学生 mask 接入生产 QC 路径（M4 §3.3 遗留不变）
- s0209 极端椭球漏检修正（M4 §3.6 遗留不变）
- hold-out 扩容 / 第三源（M3 §3.1 遗留不变）
- 3d_fullres / cascade 训练（M4 非目标不变）
- 真实医生抽检决策回流（抽检台 UI 与院内流程，M5；qc-calibrate 已就位）
- HU 分布本院化校准（需院内真实脱敏 CT，M5+）

---

## 5. 遗留问题

1. **审阅屏浏览器实测未做**（§3.1）：键盘/点击/渲染须 live browser 驗。
2. **bbox/scribble 推理未接**（§3.2）：前端已记录，后端待接。
3. **逐像素轮廓描边未做**（§3.3）：需第二 overlay 通道。
4. **baselineTeacherVersion 自动解析已解决**（§3.4）：前端仍送 null
（有意），后端自动查最新 MaskVersion 版本补上。
5. **M0 迁移 stale**（§3.6）：series.source_instances_json 缺列。
6. **本地 main 领先远端 46 提交、未推**（截至本报告提交，含本报告；
提交后以 `git rev-list --count origin/main..HEAD` 复核）。push 是显式决策。
7. **M4 遗留不变项**：qc-calibrate 数值重算仍 pending（抽检决策 0/20）；
hold-out 仍两源；39/117 类 <10 cases；s0209 未处理；BSL LICENSE 占位；
SegVol 待核实；gh 还差用户跑 `gh auth login`。
8. **Claude Code headless 在 WSL2 上不可用**（本次实证）：`claude -p
--dangerously-skip-permissions` 以 `[claude-code:unrecognized_model]
{"model":"litellm/mga-glm-5.3-flash[1m]","query_source":"sdk"}` 失败，
21 分钟仅 42 次工具调用且卡在基线 pytest 输出捕获上；本轮实施改由
素子直接动手完成。WSL2 的 Claude Code 模型配置待查（不在 M5 范围内）。

---

## 6. 交付物清单

```
src/bodydonor/review_session.py 新：M5-a/b/c/d + 传播 + 风险队列（纯逻辑）
src/bodydonor/app.py propagate / risk-queue / submit-nni 端点；review_save 接 source/provenance
src/bodydonor/models.py MaskVersion.source String(32) + provenance 列
src/bodydonor/nninteractive_backend.py 文档：M5-e 后提交走 submit-nni 建行
src/bodydonor/static/review.html 549→1001 行：M5-a/b/c/d + 自适应渲染 + 风险队列 + 传播入口
alembic/versions/e7f3a1c9d204_...py M5-e 迁移
alembic/env.py 迁移连接 FK=OFF（M5-e 基础设施修复）
scripts/gpu_probe.py 新：Edge GPU 探针（SegMed 模式，不抄门槛）
tests/test_review_session.py 新：26 项
tests/test_m5e.py 新：迁移 + 统计 + 门禁
tests/test_gpu_probe.py 新：8 项
tests/test_review_api.py +13（propagate 4 + risk-queue 2 + submit-nni 4 + save 2 + M5 标记 1）
~/m5-logs/gpu-probe.json GPU 探针真机决策 JSON（不入仓）
/tmp/bodydonor-pre-m5e.db 迁移前开发库备份（不入仓）
```

---

## 7. Blockers（单列）

1. **M5-e 迁移在开发库上静默 ROLLBACK**（§3.5）：env 补丁在
Connection 上 exec pragma 开隐式事务，alembic 不提交；改为 connect
监听器设 FK=OFF 后一次成功。
2. **开发库从未 stamp**：首次 upgrade head 因 M0 迁移重建已存
在的表而失败；stamp 3c17abaf19f7 后升级。
3. **批模式 DROP 被 FK 挡**：qc_reports 引用 mask_versions；
迁移连接 FK=OFF 解决（应用连接仍 FK=ON，实测 FK 检查空）。
4. **Claude Code headless 不可用**（§5.8）：模型未识别；
本轮改由素子直接实施。
5. ****：M5 补丁无未解决 blocker。

---

## 8. 工作方式记录（提案精神的最小合理选择）

- **门槛按实测定，不抄表**：SegMed 的驱动门槛会误判本机 Quadro；
探针不设版本号门槛，实跑验算说了算。
- **静默成功比报错更危险**：迁移 RC=0 但 ROLLBACK——以 echo 日志与
PRAGMA 读回为准，不以退出码为准；临时表残留是第一信号。
- **旧值写明，不掩盖**：传播测试初版断言 200 体素是错的（相邻层已有该类，
正确值 100）——测试与实现一起改，原因写进提交信息；风险队列期望顺序
初版也错（0.7068 < 0.7315），同理修正。
- **前端诚实边界**：静态标记测试只断言存在，不谎称渲染实测；LUT 表达
极限写进报告，不谎称画出了虚线。
- **许可纪律贯穿**：nnInteractive 修正产物仍不进商用蒸馏训练集（门禁
三值拒绝 + legacy 别名保留拒绝）；M5-e 只改“能不能建行”，不改“能不能训”。
- **eval/ 产物不入仓**：风险队列读运行产物 JSON，缺失报 503，不编造。

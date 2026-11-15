# M7-S3F 第 2 轮报告：白名单回退退役 + allowed_tasks 任务登记进注册表（S3 后续三项 ②）

日期：2026-11-15。基线：S3F 第 1 轮（`a7173eb` + `f9d80b1`，显式 modality）。
分支：`feat/m7-s3-followup-3items`。

## 1. 提交列表

| 提交 | 内容 |
|------|------|
| `842c713` | feat：`check_task` 白名单回退退役 + 24 个 TS 任务登记进 `TEACHER_REGISTRY` |
| `40a5839` | test：seg worker 拒绝断言跟随注册表语义（`not in teacher registry`） |
| `b76f24e` | test：mock 分发层拒绝消息对齐注册表语义 |

## 2. 白名单回退退役（任务 1）

`license_registry.py` `check_task`（`src/bodydonor/license_registry.py:347`）：

- **删除**回退分支 `if task in self.settings.teacher.allowed_tasks: return True, "allowed"`。
- 注册表无条目即拒绝，消息改为 `task '{task}' not in teacher registry (register as a
  registry entry, see LICENSES-THIRD-PARTY.md)`——不再提白名单。
- `blocked_tasks`（`brain_aneurysm`）一票否决保持不变（任务 4），且即使误写进
  `allowed_tasks` 也被否决（`test_blocked_overrides_whitelist` 不变仍绿）。
- 回退分支已删除的测试证据：`test_whitelist_fallback_retired`（改 `allowed_tasks`
  内容不影响路由——`legacy_task` 在白名单里仍被拒）+ `test_bare_settings_registry_
  ready_task_allowed` 补充断言（配置加白注册表外任务 `never_registered` 仍拒绝）。

## 3. allowed_tasks 25 任务逐项核实与登记（任务 2/3）

### 3.1 核实依据（不造假、不编 provenance）

逐项跑了两道证据，均实测（2026-11-15）：

1. **许可**：`LICENSES-THIRD-PARTY.md §1.2` README open 清单（访问 2026-11-06，
   Apache-2.0 权重）；`§1.3` 需 license 清单（`commercial_models` 字典）。
2. **本地实况**：TS 2.10.0 安装包（`~/work/BodyDonor/.venv-totalseg`）的
   `map_to_binary.class_map`（任务→输出类）与 `commercial_models`（任务→需 license
   权重 id）逐项实测。

### 3.2 登记 24 个（分三组）

**组一：双证据齐全（18 个）→ `research_use: allowed`、`ready=False`**
（README open 清单 + 本地 class_map 含该任务且 commercial_models 不含）：

`lung_vessels`、`body`、`cerebral_bleed`、`hip_implant`、`pleural_pericard_effusion`、
`head_glands_cavities`、`head_muscles`、`headneck_bones_vessels`、`headneck_muscles`、
`liver_vessels`、`oculomotor_muscles`、`lung_nodules`、`kidney_cysts`、`breasts`、
`liver_segments`、`craniofacial_structures`、`abdominal_muscles`、`ventricle_parts`。

**组二：仅 README 单证据（5 个）→ `research_use: unverified`、`ready=False`**
（本地 TS 2.10.0 安装包 `class_map` **不含**该任务，结构输出未核实）：

`teeth`、`trunk_cavities`、`liver_lesions`、`vertebrae_pp`、`vertebrae_pp_refined`。

- `known_restrictions` 注明"仅 README open 清单；本地安装包 class_map 不含，
  结构输出未核实"。`class_map_ref` 为待落地占位——`validate_entry` 的
  ready=True×占位守卫保证它们永远不能在类映射落地前 ready。

**组三：安装包实测需 license（1 个）→ `unverified`、`ready=False`**：

`vertebrae_body`——TS 2.10.0 安装包 `commercial_models['vertebrae_body']=305`
（需 license，非商用走 academic 渠道）。这与 M0 核查时上游 README open 清单
**有出入**（上游 2.10.0 之后可能调整过归属）；按安装包实况登记 `unverified`，
出入在 `known_restrictions` 与 `provenance` 双处注记。**不按 README 报 allowed。**

### 3.3 不登记 1 个：`lung_vessels_LEGACY`

- README open 清单里该项标注为 `...`（原文如此，含义不明）。
- 本地 TS 2.10.0 安装包 `class_map` 不含该任务（`lung_vessels` 含，LEGACY 不含）。
- 处置：**不登记**。理由：含义不明（可能是旧版保留名），登记就得编 modalities
  与结构覆盖——违反"不准猜"红线。留 `settings.yaml` 白名单作历史记录（该配置项
  去留见 §4），后续拿到上游定义或 TS 新版实测再补登记。

### 3.4 模态

25 个均按 TS 任务定义登记 CT（无 `_mr` 后缀即 CT 路线；`_mr` 族 5 个第 1 轮已
在注册表，不重复登记）。`ready` 一律 False（权重未部署/未核实，不造假）。

## 4. `allowed_tasks` 配置项去留（任务 1 附属）

**保留**。改后仍有调用方：

- `app.py:147`：`/status` 端点 `allowed_tasks` 计数（`test_api.py:36` 断言）。
- `cli.py:225`：`license status` 表格展示；`cli.py:596`：`limits` 展示。
- `license_registry.py` `summary()`：API `/license` 端点字段。
- `config.py` `is_task_allowed()`：`test_storage_config.py` 冒烟断言在用。

路由语义已与它完全解耦（注册表唯一来源）；它现在只作"已核查任务清单"的展示
记录（含不登记的 `lung_vessels_LEGACY` 的历史痕迹）。若后续做③或 UI 改版，可
将其降级为纯展示字段或迁移进注册表 summary，本轮不动（超范围）。

## 5. 调用方行为跟随（任务 5）

- `ts_runner.py`：runner 侧只有 `RUNNER_BLOCKED_TASKS`（brain_aneurysm）第二道
  检查，从不读 `allowed_tasks`——无需改动，行为已是"注册表唯一来源 + blocked
  纵深"。
- `segmentation.py`：`check_worker_task` 直通 `check_task`，无白名单逻辑。
- `cli.py` `license check`：直通 `check_task`，跟随新语义。
- `segmentation.py:360` 分发处 `check_worker_task(task, settings, modality=...)`：
  行为跟随，未改。

## 6. 测试

- **新增**（失败先写→修→跑绿）：
  - `test_whitelist_fallback_retired`：白名单加白注册表外任务仍拒绝；
    blocked 覆盖白名单。
  - `test_allowed_tasks_registered_tasks_route_via_registry`：24 个任务已登记、
    `ready=False`、分组 `research_use` 断言（open=allowed / 单证据=unverified /
    vertebrae_body 注记 commercial_models）、空配置下 ready=False 拒绝带原因、
    ready 条目不依赖白名单。
  - `test_bare_settings_registry_ready_task_allowed` 补断言：配置加白
    `never_registered` 仍拒绝。
- **更新**：`test_not_in_whitelist_rejected`（`whitelist`→`not in teacher
  registry`）；mock 分发层拒绝消息对齐；两文件 docstring。
- **结果**：受影响 7 文件（license_whitelist / teacher_registry / storage_config /
  api / segmentation / ts_runner / export_seg）共 **88 passed / 0 F/E，exit 0**；
  ruff 3 文件全绿。无 skipif 跳过的逻辑测试（纯合成 fixture，CI 可跑）。

## 7. 红线核对（任务 6）

用户原话"研究条件下不同分割任务都能注册"的理解：**注册表是科研路由唯一来源，
任务走登记、不走白名单旁路**。实施与该理解一致，未发现冲突、未破坏现有可用
行为（`total`/nnInteractive 等已 ready 条目路由不变；blocked 一票否决不变）。

## 8. 遗留

- `vertebrae_body` 许可归属以安装包实况（需 license）为准，上游 README 出入已
  注记；如需商用蒸馏资格，走 Basel academic/commercial 渠道确认。
- 组二 5 任务的类映射待 TS 新版安装包或上游定义落地后补（占位守卫防 ready）。
- `lung_vessels_LEGACY` 留白名单历史记录，未登记。

# M0-FIX-REPORT — BodyDonor M0 补丁交付报告

> 日期：2026-11-06。commit：`25fb6df`（M0-fix，本地 main，未推）。
> 依据：proposal.md（v2，§11 M0 / §12 启动指令 / §13 已定项）+ 用户对 M0-REPORT 的评审意见。
> 只做三件事（M0-fix），不做 M1。非交互运行，每步本地测试通过后继续。

---

## 1. 做了什么

### 1.1 七个核心实体 + Alembic（评审意见 1）

M0 的 `db.py` 只建了 cases / series / jobs 三张裸表且无迁移。本次补齐：

- **`src/bodydonor/models.py`（新增）**：SQLAlchemy 2.0 ORM（DeclarativeBase +
  `Mapped`/`mapped_column`，不换 ORM、不引入 SQLModel）。7 个核心实体：

  | 实体 | 表名 | 关系与要点 |
  |---|---|---|
  | Project | `projects` | 病例组织单位；─< Case |
  | Case | `cases` | **沿用 M0 表名**；project_id 可空（未归组允许，M0 扫描产物可直接入库） |
  | Series | `series` | **沿用 M0 表名**；case_id NOT NULL；保留 modality/num_slices/slice_thickness_mm/orientation/hu_range/storage_key 全部原字段 |
  | MaskVersion | `mask_versions` | 归属 Series；`version` + `source: teacher/student/manual`；同 series 同版本号唯一约束；可挂 ModelVersion（student 来源时） |
  | QCReport | `qc_reports` | 归属 MaskVersion；overall_score / flags(JSON) / rule_scores(JSON) / passed / config_version |
  | ReviewTask | `review_tasks` | 锚定 QCReport + 兼挂 Series（便于检索）；status 状态机 pending/claimed/accepted/corrected/rejected；assignee 预留（Q10） |
  | ModelVersion | `model_versions` | 学生模型版本 + 门禁结果：gate_status pending/passed/failed + gate_metrics JSON（mean_dice + per_class_dice，proposal §8 门禁判据成对） |

- **沿用表不推倒**：cases/series/jobs 三张 M0 表全部保留——前两者纳入实体体系
  （字段只增不改），jobs 保留为通用流水线任务表（对应 §6 的 8 阶段触发/状态，
  无 ORM 实体，M1 落库），由 `init_db` 兜底创建。
- **Alembic 初始化**：`alembic.ini` + `alembic/env.py`（target_metadata 取
  `models.Base.metadata`；URL 覆盖方式 `alembic -x db_url=sqlite:///...`）。
  初始迁移 `3c17abaf19f7`（autogenerate 对全新库生成，含 9 张表 + 索引 + 约束）。
- **迁移验证**（见 §2）：全新库 `upgrade head` → `downgrade base` → `upgrade head`
  全通过；roundtrip 进 CI（`.github/workflows/ci.yml` 新增 Alembic migration roundtrip 步骤）。

### 1.2 SQLite 并发纪律（评审意见 2）

- **WAL + busy_timeout 30s**：`db.connect()` 与 `db._engine()` 统一设置
  `journal_mode=WAL`、`busy_timeout=30000`、`foreign_keys=ON`；Alembic 经同一
  `_set_sqlite_pragma` event 监听，无第二套配置。
- **单一写入路径**：`db.write_lock`（`threading.RLock`，init_db 可重入）——同进程内
  所有写操作共享此锁；后台 Worker 走队列单线程消费。跨进程由 WAL + busy_timeout 兜底。
- **纪律成文**：写入 `src/bodydonor/db.py` 模块注释（4 条：PRAGMA、单一写入路径、
  短事务、PG 可选 profile）。
- **并发测试两条**（`tests/test_db.py`）：
  1. `test_concurrent_writers_no_locked`：三方并发写（FastAPI 请求 / CLI 批量导入 /
     Worker 状态更新，各 20 条 × 3 线程，各持 write_lock），断言不抛
     `database is locked` 且 60 条全落库。
  2. `test_concurrent_writers_busy_timeout_holds`：纪律被绕过的裸并发写（3 线程 × 30 条
     无锁 INSERT），busy_timeout=30s 兜底下同样不抛 locked、90 条全落库。
- **PG 不切**：仍为可选 profile（docker-compose 只含 PG），多进程 worker 部署时再切（§13）。

### 1.3 许可证计数基础（评审意见 3，只做数据模型）

- **LicenseState**（`license_state` 表）：`fingerprint_hash`（sha256 hex，唯一约束；
  只存哈希不存原始指纹，proposal §2.1 数据最小化）、`license_id`（NULL = trial）、
  `activated_at`、`trial_yearly_limit=200` / `trial_monthly_limit=20`、
  `trial_yearly_used` / `trial_monthly_used`（计数缓存，由写路径维护，真相源是流水）。
- **UsageLedger**（`usage_ledger` 表）：逐 Case 流水，`(fingerprint_hash, period,
  case_id)` 唯一（幂等计数）；`period` 形如 `'2026'` / `'2026-11'`，M1 导入路径写双行。
- **只做模型，不强制执行**：无激活校验、无拦截逻辑。API 仅加一条只读查看端点
  `GET /license/usage`（返回计数与 `enforcement: deferred_to_m1`）。
  强制执行节奏不变：M1 导入路径计数拦截，M4/M5 完整 Ed25519 激活流程。

### 1.4 笔误修复

M0-REPORT.md 两处 `picsum` → `picspin`（§1.1 与 §5.1 的远程仓库名）。

### 1.5 顺带修正

- pyproject 增加 `sqlalchemy` + `alembic` 依赖（uv.lock 同步，41 包）。
- ruff 配置增加 `extend-exclude = ["alembic/versions"]`（迁移文件由工具生成，不 lint；
  其 docstring 含 alembic 自动生成的超长行）。
- 修复 app.py 重复的 `settings` 赋值（原文件第 20/31 行加载了两次）。

## 2. 表与迁移验证结果

```
$ uv run alembic -x db_url=sqlite:////tmp/final_mig.db upgrade head
  -> 3c17abaf19f7 OK
$ ... downgrade base   -> OK（仅剩 alembic_version）
$ ... upgrade head     -> OK

全新库 upgrade head 后的表（sqlite_master）：
  alembic_version, cases, license_state, mask_versions, model_versions,
  projects, qc_reports, review_tasks, series, usage_ledger

已有库（bodydonor.db）init_db 幂等验证：10 张表共存，WAL 生效。
Alembic roundtrip 进 CI：upgrade head → downgrade base → upgrade head。
```

## 3. 并发测试结果

```
tests/test_db.py 12 条全过（2.3s）：
  - PRAGMA：WAL/busy_timeout/FK 三项断言、非 sqlite URL 拒绝
  - 实体链：Project─<Case─<Series─<MaskVersion─<QCReport─<ReviewTask 关系回读；
    student mask 挂 ModelVersion；mask 同 series 同版本唯一；
    usage_ledger (fp, period, case) 唯一
  - 并发：三方并发写（3 线程 × 20 条，write_lock 纪律路径）无 locked、无丢数据；
    裸并发写（3 线程 × 30 条，绕过纪律）busy_timeout 兜底无 locked、90 条全落库
  - Alembic：subprocess 隔离跑全新库 upgrade head / downgrade base / re-upgrade

$ .venv/bin/python -m pytest tests/ -o addopts=
46 passed（34 旧 + 12 新），2.9s
$ .venv/bin/ruff check src tests alembic
All checks passed!
```

## 4. 上一轮中断后的接续情况

上一轮会话被运行时重启中途杀掉，工作树遗留大量未提交产物。本轮逐项核对后接续：

- **核对确认已完成的**：alembic 初始化与初始迁移 `3c17abaf19f7`、models.py 9 实体定义、
  db.py WAL/busy_timeout/write_lock 纪律、test_db.py 12 条测试骨架、proposal/pyproject 改动。
- **核对发现并修复的**：
  1. `db.write_lock` 原为 `threading.Lock()`，而 `test_write_lock_is_single_path` 断言可重入
     嵌套——普通 Lock 会永久死锁、整个套件挂住；改为 `threading.RLock()`。
  2. `LicenseState` 缺任务要求的「试用计数」字段——补 `trial_yearly_used`/`trial_monthly_used`
     缓存列（真相源仍是 UsageLedger 流水），迁移文件同步。
  3. 三条测试自身的 bug（上一轮没调完）：SQLite 唯一约束报错为列名形式而非命名约束 ID
     （regex 不匹配）；`conn.execute(insert(...))` 立即抛 IntegrityError 而非等 commit
     （未包进 pytest.raises）；裸 SQL INSERT 缺 `created_at` 触发 NOT NULL。
  4. CI 无显式迁移步骤——加 Alembic roundtrip 步骤。
  5. M0-REPORT.md 两处 picsum → picspin 笔误。
- **提交过程说明**：本轮会话中期发生过上下文压缩；`25fb6df`（代码）与 `0f2c3cb`（本报告）
  的 commit 记录不在本会话可见操作内，但两个提交的全部内容均经逐文件核实与当前工作树一致
  （RLock、计数字段、测试修复、CI 步骤、笔误、`/license/usage` 端点、ruff exclude），
  且最终状态经全套验证：46 测试通过、ruff 全绿、alembic 全新库 roundtrip 成功。
  本地 main 共 3 个 commit，未推。

## 5. 遗留问题

1. **计数缓存的写路径维护**：`trial_yearly_used/monthly_used` 已建模但尚无代码维护
   （M1 导入路径实现计数时同步更新，UsageLedger 为真相源）。
2. **多指纹部署语义**：LicenseState 按 fingerprint_hash 一行；单机单库默认单行。
   M4/M5 激活流程落地时再明确多容器/多库场景的指纹归属。
3. **jobs 表不在 Alembic 迁移内**（无 ORM 实体，init_db 兜底）：M1 队列落库时若需要
   状态持久化，再决定纳入迁移或换成真正的队列表。
4. **绕过纪律的写路径**：`write_lock` 是纪律而非硬约束——未来新增写代码若绕过它，
   `test_concurrent_writers_no_locked` 可能非确定性失败，这是刻意的执行提示。
5. M0-REPORT 原有遗留项不变（GitHub 远程未建、BSL LICENSE 占位、SegVol 权重待核实等）。

## 6. 交付物清单（本 commit 新增/修改）

```
src/bodydonor/models.py       新增：7 核心实体 + LicenseState/UsageLedger（SQLAlchemy 2.0）
alembic.ini + alembic/        新增：Alembic 初始化（env.py 复用 db 纪律 PRAGMA）
alembic/versions/3c17abaf19f7_*.py  初始迁移（9 张表）
src/bodydonor/db.py           WAL + busy_timeout + write_lock 单一写入纪律 + 纪律注释
src/bodydonor/app.py          /license/usage 只读计数端点；去重 settings 加载
tests/test_db.py              新增：12 条（PRAGMA/实体链/唯一约束/并发写/Alembic）
.github/workflows/ci.yml      新增 Alembic migration roundtrip 步骤
pyproject.toml + uv.lock      sqlalchemy/alembic 依赖；ruff exclude alembic/versions
M0-REPORT.md                  picsum → picspin 两处
proposal.md                   用户既有改动（§11 M0 / §12 / §13 已定项）随本 commit 入库
```

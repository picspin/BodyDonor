# M7-S3P2-R2 轮次报告：① 来源证明层级（attestation），不删文件

基线：R1b HEAD `f005e42`。分支 `feat/m7-s3-postmerge-2`，worktree `~/work/BodyDonor-S3P2`。
本轮只做 ①（attestation 层级），不动 R1a/R1b 的代码。

## 用户定性（方法学问题）

S3P 的回填脚本没有"补全证据"，是把 evidence 缺失替换为脚本自身的推断断言，
`source=total` 从此看起来像实测事实。

## 提交列表（8 个代码提交 + 1 报告，实测）

| 提交 | 内容 |
|------|------|
| `4802460` | read_teacher_provenance 三元状态 attestation 层级 + ct_lite evidence_scope 读侧闭合 |
| `312612c` | 回填脚本证明字段——attestation/inferred + evidence/mtime_window + evidence_scope + generated_by |
| `4c4d8f0` | 三元状态 + 证明字段测试（迁移布尔断言 + 新测试 6 个） |
| `91b7dab` | roundtrip 断言补 attestation/evidence 两字段 |
| `73c2cf3` | 修复 W：ct_lite 库级文件就地补字段时 evidence_scope 枚举文件内 cases（非空列表） |
| `e2bed4d` | 审查 W1——attestation 非枚举值 raise，不静默放行 |
| `0a5f050` | 审查 W2——半升级文件（attestation 有但证明字段不全）继续补齐不永久跳过 |
| `94ffc98` | M7-S3P2-R2-REPORT.md（本文件） |

## Diffstat（实测）

```
 scripts/backfill_source_json.py    |  97 ++++++++++++++++++--
 src/bodydonor/distill_data.py      |  70 ++++++++++++++---
 tests/test_distill_license_gate.py |  34 +++++++-
 tests/test_m7_s3p2_backfill.py     | 181 ++++++++++++++++++++++++++++++++++++--
 4 files changed, 356 insertions(+), 26 deletions(-)
```

## ① 逐项证据

### 1. 三元状态（provenance_status 取代布尔）

- `src/bodydonor/distill_data.py:255-292`（`read_teacher_provenance`）：返回
  dict 增加 `provenance_status`（`"asserted" | "inferred" | "missing"`）；
  布尔键 `provenance_missing` 保留但由同一状态同源推导
  （`:289` `doc["provenance_missing"] = doc["provenance_status"] == "missing"`），
  不维护两套事实。
- 映射规则定死在 `provenance_status_of`（`src/bodydonor/distill_data.py:294-303`）：
  - `attestation` 字段存在 → 取该值；
  - 无 attestation：`backfill=true` → `"inferred"`；否则 → `"asserted"`
    （旧实测写入默认实测）。
- 缺文件 → `{"provenance_status": "missing", "provenance_missing": True}`
  （`src/bodydonor/distill_data.py:278-279`）。

### 2. write_teacher_source_json 实测写 attestation

- `src/bodydonor/distill_data.py:305-338`：新写文件一并带
  `attestation="asserted"` + `evidence="run_output"`；升级路径——覆盖
  inferred 文件时同样写 asserted（asserted 只能被更新的 asserted 覆盖）。

### 3. DistillCase.attestation + manifest 条目

- `src/bodydonor/distill_data.py:240-247`（dataclass 字段 + 注释）、
  `:256-267`（`manifest_entry` 增加 `"attestation"` 键）——manifest 每条
  case 记录必带，inferred 与 asserted 在 manifest 中肉眼可区分。

### 4. prepare_dataset 预检迁移

- `src/bodydonor/distill_data.py:502-522`：missing 仍 raise（消息不变）；
  `c.attestation = str(prov.get("provenance_status", ""))` 传播到 manifest。

### 5. ct_lite evidence_scope 读侧闭合（S3P 未决 #1）

- `src/bodydonor/distill_data.py:281-286`：`teacher_out.name` 不在库级清单
  `cases` 内的新增 case 按 missing 处理、不继承库级 provenance。

### 6. 回填脚本证明字段

- `scripts/backfill_source_json.py:98-105`（`_generated_by`：运行时自算
  `__file__` sha256）、`:108-109`（ATTESTATION/EVIDENCE 常量）、
  `:139-166`（`_upgrade_plan`：已有文件就地补字段，asserted 永不降级，
  inferred 已升级过幂等跳过，坏 JSON 不猜）、`:212-215`（ct_lite 升级分支）、
  `:241-253`（upgrade 写路径：只增证明字段保留原有）、`:255-277`（新写文档
  一并带证明字段；evidence_scope AMOS 本 case id / ct_lite 枚举 cases）。

### 7. 磁盘 21 份文件已升级（实测）

- AMOS 回填 20 份 + ct_lite 库级 1 份全部带
  attestation=inferred / evidence=mtime_window / evidence_scope（AMOS 本
  case id、ct_lite 25-case 枚举）/ generated_by（脚本 sha256）。
- 另发现磁盘还有 20 份 amos_0035+ 的**实测写入** source.json（无 backfill
  键，`write_teacher_source_json` 产出）——脚本正确地不升级不覆盖（无
  backfill=true 不满足 `_upgrade_plan` 条件），实测验证 20 份全部未被改动。
- `--dry-run` 幂等验证：`可回填 0，可升级 21，定不下来 0`（实跑后再跑
  dry-run，0 可升级——证明字段已存在跳过）。

### 8. 抽查清单（read_teacher_provenance 逐份读回，实测）

- `amos_0001` / `amos_0014` / `amos_0033` → `inferred`（3 份 AMOS 抽查）；
- `ct_lite/s0045` → `inferred`（库级 scope 内）；
- scope 外新增目录（tmp 构造，拷贝真实 ct_lite source.json 布局）→
  `missing`；scope 内 `s0045` 同布局 → `inferred`。

## 两个方法学漏洞（用户定性，全文）

**(a) mtime 窗口判据无法区分"同源产生"与"同一次 cp -r 拷贝"。**
回填脚本 `decide_*_basis` 的判据是"ts_out 117 类完整 + 全部 nii mtime 落在
M3/M4 推理窗口内"（`scripts/backfill_source_json.py:55-57` 三个窗口常量、
`:89-95` `_all_in_window` 全目录 mtime 一致性）。这个判据只能证明"这批文件
的 mtime 彼此接近且落在历史窗口内"，不能证明它们是 TS 推理的直接产物：
一次非 `-a` 的 `cp -r` 会把拷贝的全部 117 个 nii 的 mtime 刷成拷贝时刻，
若拷贝发生在窗口内（或窗口边界按拷贝时刻选取），恰好满足"全目录 mtime 一致
性"。因此 `attestation=inferred` + `evidence=mtime_window` 是**推断**不是
实测：source=total 的许可判断仍然建立在"mtime 长得像推理产物"上，而非
"确知这批文件由 run_distill_teacher_inference.py 写出"。这正是回填把
evidence 缺失替换为脚本自身推断断言的核心风险——`source=total` 看起来
像实测事实，实际是窗口匹配的结论。

**(b) WSL2 下 ext4 与 drvfs 的 mtime 语义、以及 git checkout/rsync 的
重写行为未验证。**`~/data` 挂载在 WSL2 的 drvfs（9p/Plan 9 文件系统桥）上，
其 mtime 语义与 ext4 不同（写透延迟、缓存不一致、时间戳精度差异均有已知
报告）；本轮**未验证**：(1) drvfs 上 `stat().st_mtime` 返回的时间与文件真实
产生时间的偏差是否足以把窗口外文件误判进窗口内或反之；(2) `git checkout`
/`rsync`（不带 `-a`）重写文件树时是否刷新 mtime——若发生，重写后的
历史目录会整体落在重写时刻，恰好使 `_all_in_window` 失败（fail-closed，
安全方向）或若窗口按重写时刻重新选取则完全失效。两条均属已知局限：
回填结论的可信度以"WSL2 drvfs mtime 行为可信"为前提，该前提未经实验
验证。升级路径：未来真 manifest 到达时以 `write_teacher_source_json` 的
实测写入（attestation=asserted）覆盖 inferred 文件。

## 测试数字（实测）

- 受影响文件（test_m7_s3p2_backfill.py + test_distill_license_gate.py +
  test_holdout.py）：**101 passed**（W1+W2 修复后，实测尾行）。
- 新增测试：test_m7_s3p2_backfill.py 9 个（test_new_write_has_attestation_fields、
  test_upgrade_in_place_adds_fields_keeps_existing、
  test_upgrade_never_downgrades_asserted、
  test_upgrade_idempotent_second_run_zero_changes、
  test_ct_lite_library_scope_enumerates_cases、
  test_old_measured_write_without_backfill_maps_asserted、
  test_upgrade_ct_lite_scope_enumerates_from_existing_cases、
  test_upgrade_half_upgraded_file_gets_completed、
  test_upgrade_invalid_attestation_file_skipped）。
- 新增测试：test_distill_license_gate.py 2 个（W1 失败测试先行：
  test_provenance_invalid_attestation_raises、
  test_provenance_null_attestation_raises）。
- 迁移测试：test_distill_license_gate.py 布尔断言改写 4 处（:476-484
  missing、:495-513 inferred、:815-827 roundtrip asserted、:775-777
  prepare_dataset 前置 missing）+ roundtrip dict 断言补
  attestation/evidence 两字段。
- 全仓 ruff：All checks passed（实测）。
- 全仓 pytest：全量后台跑完（数字见收尾行——若报告完结时仍在跑，以
  task 输出文件为准；受影响文件 101 passed 已实测）。

## 审查 findings 清单与处理（deep-reviewer，1 agent）

**W1（已修，`e2bed4d`）**：attestation 非枚举值（"bogus"/null 落 "None"
字符串）被 `provenance_status_of` 原样返回，预检放行、manifest 写入非法值
——fail-open。处理：失败测试 2 个先行（均复现穿透），`provenance_status_of`
对 attestation 限定 {"asserted","inferred"}，枚举外 raise DistillDataError
（与坏 JSON 处理一致），101 passed。

**W2（nit 升级，已修，`0a5f050`）**：`_upgrade_plan` 以 `"attestation" in doc`
作幂等判据，半升级文件（attestation=inferred 但缺 generated_by/evidence，
只可能来自旧版本代码或手改）被永久跳过、证明字段补不全。处理：幂等判据改
为证明字段齐全性（`req <= doc.keys()`），半升级文件继续补齐；非法 attestation
值的已有文件跳过（读侧会 raise）。回归测试 2 个，101 passed。

**不修的观察（写明理由）**：
- upgrade 写路径 `json.loads` 不捕获异常（backfill_source_json.py:261）：
  计划阶段已解析过同一文件，仅计划与写入之间文件被并发替换才触发——概率
  极低且异常中断循环已写入文件本身原子，不加防御（无场景）。
- ct_lite 库级文件升级时 `evidence_scope=[]`：仅当既有库级文件缺 `cases`
  键触发，当前写入代码总附带 `cases`，实际不可达——已由 W1 路径的 cases
  枚举修复覆盖。
- prepare_dataset `c.attestation` 空串 fallback：read_teacher_provenance
  总设置 provenance_status，空串仅可能来自测试桩，生产不可达——保留作防御。
- `"cases"` 非列表（如 null）抛 TypeError 而非 DistillDataError：手改才可
  触发，坏 JSON/非 dict 已有同类拒绝路径，风险极低不改。
- AMOS 布局读侧不受 ct_lite scope 影响、红线（不删不重做、asserted 不降级、
  已有 source.json 不重做）：审查确认属实。

## 未决清单

1. （承 ③ 未决）SOPClassUID 原样回填已修，但 source.json 无此键需重跑
   ingest——本轮未做（不在 ① 范围）。
2. 磁盘另有 20 份 amos_0035+ 实测写入的 source.json（无 backfill 键）——
   读侧映射 asserted（默认实测），但**无 attestation 字段**；若要显式标注
   需重跑 write_teacher_source_json 或扩展升级路径（本轮未做，读侧行为已
   正确，磁盘未被改动）。
3. 方法学漏洞 (b)：WSL2 drvfs mtime 语义未实验验证（见上文全文）。
4. 全仓 pytest 全量数字：受影响文件 101 passed 已实测；全量后台跑完以
   task 输出为准（本轮改动不触 GPU 路径，M6/M7 真实推理测试与本轮无关）。

# M7-CONSISTENCY-REVIEW-FINDINGS（deep-reviewer 审查发现）

分支 `feat/m7-consistency-baseline`，审查范围 `421ef0c..HEAD`（3 提交 6 文件 +318/−12）。
审查方法：逐文件读 diff、逐条亲读引用出处（M4-REPORT / M4-STAGE3-REPORT / M5-REPORT /
M6-F-REPORT / 源码 / 生成脚本 / holdout.txt / 源产物 json），另以独立 30 例真跑探针
（run_qc + 真实教师/学生 mask，工作树 src，两轮，run2 全 30 例完成）复核回归行为。
只读审查，未改任何源/测试/基线文件。

## Findings

```json
[
  {
    "severity": "warning",
    "file": "eval/consistency_baseline.json",
    "line": 5,
    "summary": "meta.run 写的构成「CT-Lite 21 例 + AMOS 9 例」是错的，真实构成 15 ts_public + 15 amos",
    "evidence": "eval/holdout.txt 逐行：s0045…s0847 共 15 行 ts_public + amos_0001…amos_0023 共 15 行 amos；生成脚本 eval/evaluate_student_holdout.py:4 自述「对 eval/holdout.txt 的 30 cases（ts_public 15 + amos 15）」。怀疑误读源：M4-REPORT.md:107「CT-Lite 21.7–99.4s/例，AMOS 60.9–150.1s/例」（耗时区间误当例数）。基线 flagged_cases 含 3 个 amos_*，与「AMOS 9 例」口径互相矛盾（amos 例若仅 9 个则 flag 3/9 比例异常，实际 15 例 flag 3）。",
    "suggestion": "改 meta.run 为「…30 例教师 mask vs 学生 mask（CT-Lite 15 例 + AMOS 15 例），2026-11-11」。基线数值无需动：overall/per_class/below_080/regression_flag 五例数值经 Python 逐项对比源产物 eval/student_teacher_consistency.json 全部一致（per_class equal: True，below080 equal: True，n_pairs 419 = sum cases 419，加权 mean 0.856 复核一致）。",
    "reproduction": "读 eval/holdout.txt（按 tab 第二列分组计数 15/15）+ eval/evaluate_student_holdout.py:4 + M4-REPORT.md:107（读码核实）；数值对比用 python json 加载两文件逐键比较"
  },
  {
    "severity": "warning",
    "file": "M7-CONSISTENCY-IMPL-REPORT.md",
    "line": 70,
    "summary": "B 项红证据记录异常：单键替换（amos_0006→amos_0004）的对称差应为 2 元素，报告记录 diff 三元素，第三例 amos_0016 与两轮独立复现的确定性行为矛盾",
    "evidence": "两轮独立 30 例真跑探针（run_qc + 真实教师/学生 mask）均得 amos_0016 mean_dice=0.7862 < 0.80、flagged=True（两轮逐位一致，非边界抖动——距阈值 0.0138，输入均为盘上固定文件、run_qc 确定性计算）。基线正确时探针 R == baseline（对称差空，5 例 flag 值与基线 diff=0.0）。红实验中 _baseline_flagged_cases() 改偏只影响期望集（阈值 0.80 硬编码在 MIN_THRESHOLDS，flag 判定不读基线），flagged 实测集不变，对称差应恰为 {amos_0004, amos_0006} 两元素；记录的三元素 diff=['amos_0004','amos_0006','amos_0016'] 意味着该次红跑 amos_0016 未 flag，与本次两轮复现矛盾。",
    "suggestion": "重做一次红实验（临时改偏基线副本→跑 test_regression_419_pairs_flag_baseline→恢复基线）确认 diff=2 并订正实施报告；若复得三元素，说明回归存在未解释的非确定性，须在测试 docstring 写明并调查该次红跑环境。",
    "reproduction": "审查只读不可改基线，红态无法在本审查内复现；绿态已双重复现（impl 全量 junit 该测试 733.13s passed + 独立探针 R==baseline、symdiff=[]、mean 0.8457）"
  },
  {
    "severity": "nit",
    "file": "docs/observation-list.md",
    "line": 26,
    "summary": "条目 2 引用 src/bodydonor/nni_runner.py:222 定位不精确——:222 在 docstring 区，实际实现 _count_target_voxels 定义在 :218、(arr > 0).sum() 在 :227",
    "evidence": "src/bodydonor/nni_runner.py:218 def _count_target_voxels(mask_arr)；:222-224 为 docstring（「mask 是二值 0/1…前景 = mask > 0 的体素」）；:227 return int((arr > 0).sum()) if arr.size else 0。观察清单引 :222 指「修为 (mask > 0).sum()（src/bodydonor/nni_runner.py:222）」落在 docstring 中间。",
    "suggestion": "行号改 :227（或写 :218-227 函数范围）。不影响条目内容正确性（f26693e 修复描述、liver=44、16754 体素均与 M4-REPORT.md:73-90 一致）。",
    "reproduction": "读 src/bodydonor/nni_runner.py:216-228（读码核实）"
  },
  {
    "severity": "nit",
    "file": "M7-CONSISTENCY-IMPL-REPORT.md",
    "line": 13,
    "summary": "diffstat 段写「5 files changed, 318 insertions(+)」与末尾 B 段不一致，实际 6 files changed, 318 insertions(+), 12 deletions(-)",
    "evidence": "git diff --stat 421ef0c..HEAD 实测：6 files changed, 318 insertions(+), 12 deletions(-)（eval/.gitignore、eval/consistency_baseline.json、tests/test_consistency_baseline.py、docs/observation-list.md、tests/test_observation_list.py、tests/test_m6e_wiring.py）。报告 :13 写「5 files changed, 318 insertions(+)」漏 files 计数与 deletions，与 :17 自己列的 b905b73 +16/-12 矛盾。",
    "suggestion": "diffstat 段改为「6 files changed, 318 insertions(+), 12 deletions(-)」。",
    "reproduction": "git diff --stat 421ef0c..HEAD（命令实测）"
  }
]
```

## 未证实观察（未列 findings）

1. **B 项红实验的三元素 diff 无法在本审查内复现**（findings 2 的根源）：审查约束只读、
   不可改基线文件，红态实验（改偏基线→跑测试→恢复）必须由实施方重做。绿态行为已
   双重复证（impl 全量 junit 733.13s passed + 审查探针 run2 全 30 例 R==baseline），
   红态矛盾仅来自实施报告的文字记录。
2. **探针与回归测试的 30 例 case 级 mean 0.8457 ≠ 基线 mean 0.856**：属预期——探针/
   回归是 30 例等权 case 级 mean，基线 0.856 是 419 对逐对等权 mean（两者口径不同，
   测试断言容差 <0.02 正是为吸收此差：|0.8457−0.856|=0.0103 < 0.02，通过）。非缺陷。
3. **回归测试对边界 case 的稳定性**（coordinator hint b）：amos_0016 0.7862 距阈值
   0.80 有 0.0138 余量，两轮复现逐位一致，无边界抖动迹象。但该测试的容差语义
   （len(diff) ≤ 2 即「±1 例」）允许边界 case 单例翻转不红，属设计内容差，
   实施报告已如实声明「允许 ±1 例边界差异，差异需在报告记录原因」，不列 findings。

## 已核实无问题（关键点）

- **A 基线数值全部正确**：overall（mean 0.856/p10 0.6986/median 0.922/p25 0.8521/min
  0.0/n_pairs 419）与源产物逐键一致；per_class 15 类 mean+cases 与源产物 dict 完全相等
  （python 比较 equal: True）；below_080 5 类一致；n_pairs 419 = per_class cases 求和
  419；按 cases 加权 mean 复算 0.856 一致。M4-REPORT.md:169（419 对分布行）、:249-257
  （§3.5 数字解读）、:142-143（flag 5 例）逐字核对一致。
- **raw_pairs_retained=false 属实**：生成脚本 :213-283 teacher_student_consistency()
  仅 scores.append(d) 后聚合（overall=round(mean/median/p10/p25/min,4)、
  n_pairs=len(scores)），逐对分数不落盘；student_holdout_results.json cases 仅
  case_id/source/mean_dice/n_classes_scored/per_class_dice。基线只固化充分统计量、
  注明「绝不编造原始数值」，诚实。
- **regression_flag source 段来源引用正确**：M4-REPORT.md:142-143 flag 5 例逐字一致
  （s0209 0.1181、amos_0005 0.6592、s0338 0.7081、amos_0006 0.7567、amos_0016 0.7862）；
  §3.4（:241-247）「校准的是聚合方式不是数值」转述准确；tolerance「对称差 ≤1 例」与
  测试断言 len(diff) ≤ 2 语义一致。
- **case_mean_note 正确区分两种聚合**：基线 flagged 值是 run_qc comparable-class mean
  （探针复现 5 例逐位一致），student_holdout_results.json 的 gate 口径值不同
  （s0209 0.1087、amos_0005 0.482 等）——note 写明「run_qc multi_teacher_consistency
  规则 case 级聚合，全部可比类均值」，未混淆。
- **eval/.gitignore 例外正确**：`!consistency_baseline.json` 插在 holdout.txt 后，
  与既有 `*` + 白名单结构一致；git ls-files 确认基线已 tracked、源产物
  student_teacher_consistency.json 仍 gitignored（不被连带放行）。
- **B 接线真单一事实来源**：`_baseline_flagged_cases()` 只读
  eval/consistency_baseline.json 的 regression_flag.flagged_cases（旧硬编码 5 例删除，
  git diff 确认）；`_real_products_available()` 探测路径同步换为基线文件（与回归测试
  实际读取路径一致，skip 判定不指向已删除的依赖）；baseline_mean 读取路径
  `["baseline"]["mean"]` 与基线结构匹配。flag 规则本身零改动（qc.py:411-413 mean
  聚合、阈值 0.80、`flagged = mean_dice < threshold` 均未触碰，src/ 无 diff）。
- **C 观察清单五条出处全部核实**：
  - 条目 1：M4-REPORT.md:58/:300、M4-STAGE3-REPORT.md:112/:270「String(16) 装不下
    nninteractive_guided 20 字符」逐字一致；M5-REPORT.md:81「String(16)→String(32)」、
    src/bodydonor/models.py:144 `String(32)` 属实；修复 commit 539ffe6 存在
    （feat(m5): M5-e 血缘修正——MaskVersion.source String(32)）。
  - 条目 2：f26693e（fix(m4): n_target_voxels 二值 mask 计数）存在；M4-REPORT.md:73-90
    liver=44、mask 非零 16754、sidecar 报 0 一致；修为 (mask>0).sum() 属实（仅行号
    :222 不精确，见 nit）。
  - 条目 3：M4-REPORT.md §3.1（:213-221）117 类 mean 0.1027 FAIL 落库、27387c8 分口径、
    in-scope 0.8014 passed、scope_naive 保留不删，逐字一致；commit 27387c8 存在。
  - 条目 4：M6-F-REPORT.md:164-170 Blocker（calibrated_on 20→22、sha256 变化、
    62e9e82 修复、759411f 回滚）逐字一致；两 commit 均存在。
  - 条目 5：M4-REPORT.md:115/:173/:374-376 max|cos|=1.2e-4、SVD 偏差 0.00011mm、
    QR 3.28mm 不可用一致；实现 eval/evaluate_student_holdout.py:135
    `u, _, vt = np.linalg.svd(rot)` 属实；src/bodydonor/nifti_geom.py:73
    check_nifti_geometry 存在；修复 commit 3e4674d 存在。
- **门槛句属实**：「同类问题两个 PR 出现过才固化进硬规则」写入文档首部；
  条目 1/2/5 修复 commit 经 git log --grep 逐条核实为单次发现（539ffe6 仅一处、
  f26693e 仅一处、3e4674d + M4 存量一处）；条目 3/4 各一次（27387c8、62e9e82/759411f），
  按门槛不进硬规则的判断成立。
- **守卫测试真守卫**：test_consistency_baseline.py 7 测试断言具体数值（EXPECTED_*
  硬编码与基线一致）、漂移即红（对照源产物测试 skipif 缺失、漂移即 fail——实测基线
  与源产物相等，若基线被篡改必红）；test_observation_list.py 4 测试断言五条标题、
  三段 ** 前缀各 ≥5、门槛句关键词。先红后绿证据可信（impl 报告记录 6 failed→7 passed、
  4 failed→4 passed，机制为文件存在性断言，可信）。
- **改动范围外科手术式**：src/、config/、eval/evaluate_student_holdout.py 在
  421ef0c..HEAD 零 diff；未决清单如实记录 3 处范围外旧引用
  （tests/test_review_session.py:6、eval/evaluate_student_holdout.py:532 均亲读确认；
  另发现 src/bodydonor/app.py:1456 运行时读旧源产物路径与 qc.py:366 docstring——
  均为本轮范围外的既有代码，与 impl 未决清单口径一致，不动）。
- **全量验证可信**：m7-consistency-impl-fulltest-junit.xml 实测 tests=759
  failures=0 errors=0 skipped=0（XML 属性解析），含 test_regression_419_pairs_flag_baseline
  time=733.13s 通过；consistency-impl-fullrun.log 尾行 759 passed in 3022.21s 一致。

## W2/N2 修复验证（2026-11-23）

审查方法：逐行读 `cases.jsonl` 独立算 flagged 集与对称差（Python -I 实测）、
逐位核对 30 例 mean_dice 与基线 regression_flag、XML/log 逐属性核对、
git diffstat/numstat/show 独立实测、两个快文件秒级重跑（11 passed in 0.08s）、
ruff 独立跑（All checks passed!）。审查期间基线 sha256
`ba48162ac306…409730` 与 pristine 副本逐字节一致、`git diff` 干净、
git status 仅未跟踪报告文件——审查自身未留任何改动。

### 1. W2 红实验重做 — **属实**

- flagged 集独立计算 = {s0209 0.1181、s0338 0.7081、amos_0005 0.6592、
  amos_0006 0.7567、amos_0016 0.7862}，恰 5 例，与 W2N2-REPORT 表逐位一致。
- 对称差（实测 flagged 集 vs 改偏期望集 {s0209, amos_0005, s0338, amos_0004,
  amos_0016}）独立算得 **{amos_0004, amos_0006}，len=2 ≤ 2**。
- amos_0016 本次红跑 mean_dice=0.7862 flagged=True（<0.80），30 例
  flagged/阈值一致性核对无一矛盾；基线 flagged 5 例数值与 cases.jsonl
  逐位（bitwise）相等。
- `w2_red_junit.xml`：tests=1 failures=0 errors=0 skipped=0、suite
  time=606.267s、testcase time=600.609s、timestamp 2026-11-23T16:31+08:00；
  `w2_red.log` 尾部 1 dot [100%]。均与 W2N2-REPORT 一致。
- 测试断言 `len(diff) <= 2` 在 tests/test_m6e_wiring.py:329 亲读确认——
  passed 是因 diff=2 恰在容差边界内，实施方结论成立（非红态失效、非非确定性）。
- 基线恢复确证：`eval/consistency_baseline.json` sha256 =
  `ba48162ac30607043ffa0ead95f8e7a63fe4a1f36ac671a5b459fd0c3b409730`，
  与 `baseline_pristine.json` 逐字节一致，`git diff` 干净。
- 探针插件在持久目录（repo 外，不进仓），包装/幂等标记/残留清理逻辑亲读无问题。

### 2. N2 diffstat 订正 — **属实**

- `git diff --stat 421ef0c..HEAD` 独立实测 = **6 files changed, 319
  insertions(+), 12 deletions(-)**；`--numstat` = observation-list 76/0、
  .gitignore 1/0、baseline 66/0、test_consistency_baseline 117/0、
  test_m6e_wiring 16/12、test_observation_list 43/0——与 IMPL-REPORT
  diffstat 段（:13 及 :16-18 的 75→76 解释句）逐项一致。
- N1（`62ff819`）`git show` 实测 docs/observation-list.md **+2/−1**：
  一行行号引用拆成两行（:227 + 函数范围 :218-227），净 +1，75→76 解释成立。
- 分提交 numstat 实测（0685287=184、9b7e2c3=118、b905b73=+16/−12）：
  插入数直加 318，加 `1a2c295`（+1/−1）与 `62ff819`（+2/−1）后净 319/−12
  ——与报告「318 来源」解释逐项吻合。
- 两报告 grep「318」仅 3 处均为解释性提及（「非 318」「318 的来源核实」
  「直加的 318」）；「5 files」残留 0 处。

### 3. 不动项确证 — **属实**

- `git status` 仅 4 个未跟踪报告文件 + 1 个 junit（与会话起始快照一致），
  tracked 零改动；`git diff b905b73..HEAD -- tests/` 为空——
  test_m6e_wiring.py docstring 及全部测试自 b905b73 未动。
- IMPL-REPORT §B 红证据段与 FIX-RECORD W2 节一致（2026-11-20 重做、
  1 passed 733.00s、5 例实测值、两元素结论、误记订正表述逐句吻合）。

### 4. 3 文件重跑与 ruff — **可信**

- `wiring_green_junit.xml`：tests=4 failures=0 errors=0 skipped=0、
  time=594.130s、regression testcase time=590.731s（真实跑非 skip）、
  3 接线单测 0.421/0.021/0.019s——与 W2N2-REPORT 逐项一致。
- 两个快文件审查者独立秒级重跑：**11 passed in 0.08s**（7+4，exit 0）。
- ruff check src tests 独立跑：**All checks passed!**（exit 0）。

### 5. 范围外 — **无越界改动**

- tracked 零改动（同上）；探针/产物全部在 `~/work/bodydonor-plans/
  consistency-w2probe/`（repo 外）。

### 新 findings

```json
[]
```

### 未证实观察（未列 findings）

1. **「首跑红实验插件目录不在 PYTHONPATH 报 usage error（exit 4）」无日志留证**：
   探针目录仅存成功跑的 `w2_red.log`，失败首跑未留日志。说法合理（`-p` 加载
   机制所致）、不影响结论（重跑成功且全程数字确证），但无法逐字核实。
2. **`M7-CONSISTENCY-FIX-RECORD.md`「未改」无法 diff 验证**：该文件从未
   tracked、无历史快照可比对；其内容与 IMPL-REPORT §B 及各轮记录一致，
   无任何被改动迹象，仅记录此局限。
3. **2026-11-20 首次重做（733.00s、/tmp 证据）本身不可复验**：/tmp 证据随
   WSL2 迁移清空，本轮可验证的是 2026-11-23 持久目录重做（606.267s）；
   两次 30 例逐位一致，结论不受影响。
4. **改偏态中间快照（5/5 numstat）不可事后验证**：基线已逐字节恢复，
   改偏 diff 只能由「对称差恰为单键替换预期值」间接确证——与实施方声称一致。

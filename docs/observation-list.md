# 观察清单（M7-CONSISTENCY 轮 C 项）

同类问题在**两个 PR** 出现过才固化进硬规则；只出现一次的先记观察清单。
下面五条均已逐条核对原始出处（报告行号 / commit 均亲读），触发条件与检查项
供 diff 审查时对照。

---

## 1. MaskVersion.source 列宽

- **历史故障**：`MaskVersion.source` 建为 `String(16)`，装不下来源值
  `nninteractive_guided`（20 字符）——M4 阶段 3 发现，本轮不建行不改列宽，
  留给 M5-e（M4-REPORT.md:58、:300；M4-STAGE3-REPORT.md:112、:270）。
  M5-e 已修为 `String(32)`（src/bodydonor/models.py:144，M5-REPORT.md:81）。
- **diff 触发条件**：新增或改动 `MaskVersion.source` 的取值，或改动该列宽
  （含 Alembic 迁移）。
- **检查项**：source 取值长度 ≤ 列宽；迁移与模型定义一致（模型改列宽必须
  有对应迁移，反之亦然）。

## 2. target_label 计数口径

- **历史故障**：`(mask == target_label)` 在 `target_label != 1` 时恒报 0
  ——修正 mask 是二值 0/1，`target_label` 是语义类号（liver=44）。2026-11-11
  GPU 验证 mask 非零 16754 体素，sidecar 却报 `n_target_voxels 0`
  （commit `f26693e`：fix(m4) n_target_voxels 二值 mask 计数）。修为
  `(mask > 0).sum()`（src/bodydonor/nni_runner.py:227；函数
  `_count_target_voxels` 定义在 :218-227）。
- **diff 触发条件**：mask 计数 / 比较 / 前景判定口径改动（任何
  `== 类号` 形式的 mask 比较）。
- **检查项**：二值 mask 一律 `> 0` 计数，不拿语义类号直接 `==` 比较；
  同口径不同 target_label 的结果差异要在报告写明，不掩盖。

## 3. 门禁/评估 scope 口径

- **历史故障**：首轮按 117 类评 15 类学生，mean 0.1027 FAIL 且已落库
  （gate_status=failed）——102 个超范围类天然 dice=0，完美学生也永远 FAIL。
  commit `27387c8` 修正为分口径：in-scope（student-15-classes）mean 0.8014
  passed；naive 117 类对照保留在 summary["scope_naive"] 不删
  （M4-REPORT.md §3.1，:213-221）。
- **diff 触发条件**：门禁 / 评估 scope 改动（评分子集、gate_scope、
  in_scope_filter 相关）。
- **检查项**：评分子集与训练子集一致；naive 对照保留不删；DB 已落库的
  错误口径纠正时，旧值在报告与提交信息里写明。

## 4. 测试污染真实 config

- **历史故障**：决策回流端点测试未传 path，回流落到模块级
  `QC_THRESHOLDS_PATH`，跑测试后真实 `config/qc_thresholds.yaml` 被改写
  （calibrated_on 20→22，sha256 变化；commit 62e9e82 修复，759411f 回滚
  误提交的污染文件；M6-F-REPORT.md:164-170 Blocker）。
- **diff 触发条件**：任何写 `config/` 的测试/代码，或走回流（reflux /
  safe_dump 写盘）路径的改动。
- **检查项**：测试前后真配置 sha256 不变；写操作一律走临时副本
  （monkeypatch 路径到 tmp_path）。

## 5. s0209 方向矩阵非严格正交

- **历史故障**：s0209 方向矩阵非严格正交（列间 max|cos|=1.2e-4）→
  SimpleITK 拒读 → 推理 worker 全灭。SVD 极分解正交化修复（世界坐标偏差
  0.00011mm；QR 符号翻转偏差 3.28mm 不可用）（M4-REPORT.md:115、:173、
  :374-376；实现 eval/evaluate_student_holdout.py:135，几何检查
  src/bodydonor/nifti_geom.py check_nifti_geometry）。
- **diff 触发条件**：方向矩阵 / affine 处理、正交化（orthofix / SVD /
  QR）相关改动。
- **检查项**：非正交矩阵走 SVD 极分解不走 QR；正交化偏差有断言；
  det<0 时翻列保右手系。

---

## 门槛记录

- **条目 3（门禁口径）与条目 4（config 污染）目前各只出现一次**，先记观察
  清单，不进硬规则。
- 条目 1、2、5 均为单次发现（git log --grep 逐条核实：条目 1 修复仅 M5-e
  `539ffe6`；条目 2 修复仅 `f26693e`；条目 5 修复仅 `3e4674d` + M4
  存量修复），未在两个 PR 重复出现，同样先记清单。

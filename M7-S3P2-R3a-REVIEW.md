# S3P2-R3a 深度审查（④ 删商业合规设施）

- 审查方：deep-reviewer 子代理（只读），2026-11-18。
- 范围：基线 d0ae8a5..HEAD，8 提交 19 文件（④ 删商用蒸馏白名单/`--profile` 双口径/
  brain_aneurysm 商用阻断镜像/商业审计附件化）。
- 结论：**Blocker 0，W 0**。静态核查（读码 + grep）未发现会导致错误结果、许可
  违规或 CI 失败的问题。审查方无 shell 工具，未实跑 pytest；实现方自行实跑的
  二次核实见 R3a-REPORT §5。

## 核查要点（全部通过）

1. **残留引用**：`COMMERCIAL_DISTILL_TASKS`、`check_teacher_task_commercial`、
   `RUNNER_BLOCKED_TASKS`、`is_commercial_distill_allowed`、
   `commercial_distill_tasks`、`_DISTILL_PROFILES` 在 src/scripts/config 零命中；
   tests 仅 R3 ④ 说明注释与 `hasattr` 反向断言。`distill_profile` 只是
   models.py:261 的 DB 列，与本轮无关。
2. **防线未倒退**：distill_data.py:112-119 total 分支委托
   ontology.mask_allowed_in_distill_training；ontology.py:640-645 无条目
   fail-closed；tissue_types/coronary_arteries 无注册表条目即阻断；
   vertebrae_body（license_registry.py:369）unverified 即阻断
   （ontology.py:649），test_prepare_dataset_rejects_unverified_teacher 钉住。
3. **manifest 兼容**：label_policy 已无 teacher_tasks 键；
   register_student_model.py 只 dict 透传，无 KeyError 风险。
4. **ts_runner 契约**：RUNNER_BLOCKED_TASKS 零引用；主进程 check_worker_task
   在 segmentation.py:243/:426 两个派发点、都在 build_runner_cmd 之前——
   删 runner 镜像不放开白名单。
5. **yaml/配置**：TeacherConfig.blocked_tasks 带 default_factory=list，缺键可
   加载；cli.py:233 渲染空列表正常。
6. **测试正确性**：test_cli_profile_flag_retired 的 `"--profile" not in src`
   对当前脚本成立（docstring 与 argparse 均无该串）；两个重写测试断言与
   ontology 实际 reason 一致（含任务名）。
7. **UI/API 面**：/license 端点走 registry.summary()，前端无读
   commercial_distill_tasks 的地方。

## 审查方自认未覆盖 + 实现方补查结果

- tui.py 与 static/ 的 grep 未覆盖 → 实现方补查：`grep -rn "commercial_distill\|blocked_tasks" src/bodydonor/tui.py src/bodydonor/static/`
  零命中；static/review.html `commercial|blocked` 零命中。无 finding。
- pytest 实跑未做 → 实现方实跑：受影响 9 文件 155 passed；m5e/nni 慢文件
  35 passed（后台实测 153s）；全仓 ruff 全绿。
- REVIEW 文件写入 → 按角色约定审查方不写文件，本文件由实现方按其最终回复转写。

# git hooks：config/ 硬隔离守卫

## 安装（每个 clone 设一次）

```bash
git config core.hooksPath scripts/git-hooks
```

## 验证方法

装完后跑一次无标记改 config：

```bash
echo "# test" >> config/settings.yaml
git add config/settings.yaml
git commit -m "test: no marker"   # 应被拒绝（exit 非 0，stderr 提示加 [config-change]）
git reset HEAD config/settings.yaml && git checkout -- config/settings.yaml
```

被拒即证明生效。

## 行为（commit-msg hook）

- 暂存含 `config/` 路径（新增/修改/重命名/删除都算）且消息无 `[config-change]`（精确子串、大小写敏感）→ 拒绝。
- 暂存列表按 `--no-renames -z` 读取：默认 renames 模式只列新路径（rename 移出 config/ 会逃检），非 ASCII 路径在默认 quotePath 下会被引号转义（`config/中文.yaml` 变 `"config/\344..."` 不匹配 `^config/`），两者均已修复并有测试固化。
- 消息检查忽略 `#` 开头行：git 默认剥离注释行，落在注释里的标记不会进入最终 message，故不认。
- 无 config/ 改动 → 放行，不影响正常提交。
- merge 提交（`MERGE_HEAD` 存在）→ 放行：拒绝会把仓库卡在 merge 状态，无法在 hook 里安全回退。**已接受风险：merge 提交可夹带 config/ 改动且无标记要求**——merge 前两边分支各自提交时已被守卫覆盖，夹带只可能来自带标记的分叉提交合并，属残余风险。
- `--amend`：无特殊处理，与其他提交同规则。**已接受限制：`--amend --no-edit` 复用旧消息里的标记，本次新增的 config 改动会被放行**（`test_amend_no_edit_reuses_old_marker_pinned_limitation` 固化该行为）——旧标记满足了对本次改动的标记要求是 git 固有行为，使用 amend 改 config 时应换新消息。
- `[config-change]` 只是显式声明，**不是免审**——改 config 的提交照常进 review。

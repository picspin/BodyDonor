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

- 暂存含 `config/` 路径（新增/修改/重命名/**删除**都算）且消息无 `[config-change]`（精确子串、大小写敏感）→ 拒绝。
- 消息检查忽略 `#` 开头行：git 默认剥离注释行，落在注释里的标记不会进入最终 message，故不认。
- 无 config/ 改动 → 放行，不影响正常提交。
- merge 提交（`MERGE_HEAD` 存在）→ 放行：拒绝会把仓库卡在 merge 状态，无法在 hook 里安全回退。
- `--amend`：无特殊处理，与其他提交同规则（含 config/ 时仍须标记；消息里的旧标记会被识别——使用时自行确认）。
- `[config-change]` 只是显式声明，**不是免审**——改 config 的提交照常进 review。

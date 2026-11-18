# M7-S3P2-R4b 审查 findings

审查者：deep-reviewer 子代理（只读，无 Bash），所有结论来自读码，"实际运行"类判断由调用方（实施方）实测复现。
待审提交：`6d5a557`（tests/test_license_whitelist.py 守卫测试 +68 行）。

## Findings 与处理

### F1 [Major] 自跳过整文件 `rel == _SELF`（tests/test_license_whitelist.py:176）

- **问题**：整个守卫测试文件被跳过，之后任何写进该文件的 NC "open source" 表述都不会被扫到。粒度过粗，守卫在本文件内形同虚设。
- **复现**（调用方实测）：在守卫文件追加注释行 `# F1 repro probe: NC weights are open source, described loosely`，运行守卫测试 → **passed（逃过守卫）**，属实。
- **处理**：已修复（`5786f54`）。不做整文件跳过——三管齐下使守卫文件自身零命中：(a) 正则字面量拆开（`"open[" r"\s" "-]?source|open" "source|开" "源"`），定义行不再自命中；(b) 白名单保留行按行号从真实文件动态读取（`_ALLOWED_LINE_REFS`），守卫文件内不硬编码 echo 行；(c) docstring/断言消息措辞改写。修复后探针复测：文件内新增 NC 表述行 → **failed（抓住）**，守卫生效。

### F2 [Major] rglob 扫未跟踪/gitignored 产物（tests/test_license_whitelist.py:52–58, 134–136）

- **问题**：`rglob` 不走 `git ls-files`，未跟踪、gitignored 的本地文件（临时目录、数据目录、本地产物）都被扫，本地红绿因机器状态而异，CI 与开发机可能不一致。
- **复现**（调用方实测）：建 `tmp_scan/probe.md`（内容含 NC "open source"），运行守卫测试 → **failed（本地红）**，属实。
- **处理**：已修复（`5786f54`）。改用 `git ls-files` 取受跟踪文件列表，只扫受跟踪文本文件。修复后复测：同探针 → **passed（不扫未跟踪文件）**，行为与 CI 一致。

### F3 [Minor] 正则漏抓变体（tests/test_license_whitelist.py:123–125）

- **声称**：漏抓 "OSI-approved"、非 ASCII 连字符、折行切开的 "open\nsource"。
- **核实**（调用方实测）：把 `OSI` 加进正则会**误伤普通单词**——LICENSES-THIRD-PARTY.md:184 的 "rep**osi**tory" 含子串 `osi`（Python 实测 `re.finditer(r'(?i)osi', ...)` 命中 index 27，上下文 `' while this repository is available'`）；须 `\bOSI\b` 词边界才行，但该行是 nnInteractive README 逐字引用（Apache-2.0 代码许可说明），与 NC 成分表述无关，加了只会误报。
- **处理**：**不采纳**。折行匹配（跨行归一化）与连字符变体属过度设计——守卫针对的是相邻组合表述（"open source"/"open-source"/"opensource"/"开源"），逐行扫描覆盖全部实际写法；词边界 OSI 判据改由 docstring 说明承担。

### F4 [Minor] `_SCAN_SUFFIXES` 缺扩展名（tests/test_license_whitelist.py:134–136）

- **核实**（调用方实测）：`git ls-files` 扩展名普查——仓库实际存在的未扫描类型：`.sh`（8 个 hooks/scripts）、`.js`（niivue.umd.js 707KB 第三方前端）、`.ini`（alembic.ini）、`.mako`、`.lock`（uv.lock）、`.gitignore`（2 个）、无后缀（alembic/README，单行 ASCII 无换行）。逐文件 grep + Python 交叉扫描实测这些类型**零命中**。
- **处理**：已修复（`5786f54`）。`_SCAN_SUFFIXES` 补 `.sh/.js/.ini/.mako/.lock/.gitignore`（6 类全覆盖实测存在的类型）；alembic/README 无后缀不扫，零命中风险，内容为 Alembic 通用说明一行。

### F5 [Minor] 静默跳过解码失败（tests/test_license_whitelist.py:166–171）

- **问题**：`UnicodeDecodeError`/`OSError` 直接 `continue`，GBK/UTF-16 文本整文件跳过无告警。
- **处理**：已修复（`5786f54`）。解码失败记为 violation（`<name>: <decode failed>`），在断言中报告。

### F6 [Nit] LICENSES-THIRD-PARTY.md:184 grep 命中来源不明

- **核实**（调用方实测）：该行命中即 "rep**osi**tory" 子串（审查者宽正则含 OSI 无词边界），非 "open source"/"开源"/术语表述。与 F3 同源。
- **处理**：闭环，无需改动。当前守卫正则不含 OSI，不受影响。

## 审查后状态

- 修复提交：`5786f54`（+44/−34）。
- 复测：tests/test_license_whitelist.py **9 passed**；与 test_distill_license_gate.py 同跑 **59 passed**；ruff 全绿。
- F1 修复后探针（守卫文件内 NC 表述）→ 红；F2 修复后探针（未跟踪文件 NC 表述）→ 绿（不扫）。两探针均已清理，工作树 clean。

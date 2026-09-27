---
name: ddl-guard
description: >
  数据库设计规范与检查，包括审查DDL、建表语句、表结构设计、字段设计、索引设计、MySQL建表、
  审查Mapper SQL、PO类规范、数据库规范检查、SQL审核、数据库设计审查。
  提供两类能力：(1) 按规范设计新表，(2) 用脚本审查 DDL 文件和 MyBatis SQL 合规性。
files:
  - README.md
  - badcase/001-forbidden-type-and-missing-comment/expected.md
  - badcase/001-forbidden-type-and-missing-comment/input/example.sql
  - badcase/001-forbidden-type-and-missing-comment/prompts.md
  - badcase/002-bad-where-function/expected.md
  - badcase/002-bad-where-function/input/test_mapper.xml
  - badcase/002-bad-where-function/prompts.md
  - badcase/003-bad-join/expected.md
  - badcase/003-bad-join/input/test_mapper.xml
  - badcase/003-bad-join/prompts.md
  - badcase/004-bad-index/expected.md
  - badcase/004-bad-index/input/example.sql
  - badcase/004-bad-index/prompts.md
  - badcase/005-bad-naming/expected.md
  - badcase/005-bad-naming/input/example.sql
  - badcase/005-bad-naming/prompts.md
  - badcase/006-bad-comment/expected.md
  - badcase/006-bad-comment/input/example.sql
  - badcase/006-bad-comment/prompts.md
  - badcase/010-atomic-charset-clause/expected.md
  - badcase/010-atomic-charset-clause/input/example.sql
  - badcase/011-atomic-collate-clause/expected.md
  - badcase/011-atomic-collate-clause/input/example.sql
  - badcase/012-atomic-auto-increment-clause/expected.md
  - badcase/012-atomic-auto-increment-clause/input/example.sql
  - badcase/013-atomic-engine-clause/expected.md
  - badcase/013-atomic-engine-clause/input/example.sql
  - badcase/014-atomic-row-format-clause/expected.md
  - badcase/014-atomic-row-format-clause/input/example.sql
  - badcase/015-atomic-partition/expected.md
  - badcase/015-atomic-partition/input/example.sql
  - badcase/016-atomic-hash-comment/expected.md
  - badcase/016-atomic-hash-comment/input/example.sql
  - badcase/017-atomic-dash-comment-space/expected.md
  - badcase/017-atomic-dash-comment-space/input/example.sql
  - badcase/018-atomic-table-name-length/expected.md
  - badcase/018-atomic-table-name-length/input/example.sql
  - badcase/019-atomic-table-name-start/expected.md
  - badcase/019-atomic-table-name-start/input/example.sql
  - badcase/020-atomic-table-name-double-underscore/expected.md
  - badcase/020-atomic-table-name-double-underscore/input/example.sql
  - badcase/021-atomic-table-name-reserved/expected.md
  - badcase/021-atomic-table-name-reserved/input/example.sql
  - badcase/022-atomic-table-name-abbrev/expected.md
  - badcase/022-atomic-table-name-abbrev/input/example.sql
  - badcase/023-atomic-table-comment-missing/expected.md
  - badcase/023-atomic-table-comment-missing/input/example.sql
  - badcase/024-atomic-table-comment-length/expected.md
  - badcase/024-atomic-table-comment-length/input/example.sql
  - badcase/025-atomic-table-comment-special/expected.md
  - badcase/025-atomic-table-comment-special/input/example.sql
  - badcase/026-atomic-required-fields/expected.md
  - badcase/026-atomic-required-fields/input/example.sql
  - badcase/027-atomic-field-count/expected.md
  - badcase/027-atomic-field-count/input/example.sql
  - badcase/028-atomic-primary-key-type/expected.md
  - badcase/028-atomic-primary-key-type/input/example.sql
  - badcase/029-atomic-del-flag-name/expected.md
  - badcase/029-atomic-del-flag-name/input/example.sql
  - badcase/030-atomic-del-flag-comment/expected.md
  - badcase/030-atomic-del-flag-comment/input/example.sql
  - badcase/031-atomic-foreign-key/expected.md
  - badcase/031-atomic-foreign-key/input/example.sql
  - badcase/032-atomic-field-name-length/expected.md
  - badcase/032-atomic-field-name-length/input/example.sql
  - badcase/033-atomic-field-name-start/expected.md
  - badcase/033-atomic-field-name-start/input/example.sql
  - badcase/034-atomic-field-name-double-underscore/expected.md
  - badcase/034-atomic-field-name-double-underscore/input/example.sql
  - badcase/035-atomic-field-name-reserved/expected.md
  - badcase/035-atomic-field-name-reserved/input/example.sql
  - badcase/036-atomic-generic-field-name/expected.md
  - badcase/036-atomic-generic-field-name/input/example.sql
  - badcase/037-atomic-field-name-abbrev/expected.md
  - badcase/037-atomic-field-name-abbrev/input/example.sql
  - badcase/038-atomic-field-comment-missing/expected.md
  - badcase/038-atomic-field-comment-missing/input/example.sql
  - badcase/039-atomic-field-comment-length/expected.md
  - badcase/039-atomic-field-comment-length/input/example.sql
  - badcase/040-atomic-fullwidth-char/expected.md
  - badcase/040-atomic-fullwidth-char/input/example.sql
  - badcase/041-atomic-comment-trailing-comma/expected.md
  - badcase/041-atomic-comment-trailing-comma/input/example.sql
  - badcase/042-atomic-forbidden-type/expected.md
  - badcase/042-atomic-forbidden-type/input/example.sql
  - badcase/043-atomic-varchar-length/expected.md
  - badcase/043-atomic-varchar-length/input/example.sql
  - badcase/044-atomic-char-length/expected.md
  - badcase/044-atomic-char-length/input/example.sql
  - badcase/045-atomic-index-name-length/expected.md
  - badcase/045-atomic-index-name-length/input/example.sql
  - badcase/046-atomic-unique-index-name/expected.md
  - badcase/046-atomic-unique-index-name/input/example.sql
  - badcase/047-atomic-normal-index-name/expected.md
  - badcase/047-atomic-normal-index-name/input/example.sql
  - badcase/048-atomic-unique-hint/expected.md
  - badcase/048-atomic-unique-hint/input/example.sql
  - badcase/049-atomic-index-on-id/expected.md
  - badcase/049-atomic-index-on-id/input/example.sql
  - badcase/050-atomic-index-count/expected.md
  - badcase/050-atomic-index-count/input/example.sql
  - badcase/051-atomic-composite-index-width/expected.md
  - badcase/051-atomic-composite-index-width/input/example.sql
  - badcase/052-boundary-table-name-length/expected.md
  - badcase/052-boundary-table-name-length/input/example.sql
  - badcase/053-boundary-table-comment-length/expected.md
  - badcase/053-boundary-table-comment-length/input/example.sql
  - badcase/054-boundary-field-count/expected.md
  - badcase/054-boundary-field-count/input/example.sql
  - badcase/055-boundary-field-name-length/expected.md
  - badcase/055-boundary-field-name-length/input/example.sql
  - badcase/056-boundary-field-comment-length/expected.md
  - badcase/056-boundary-field-comment-length/input/example.sql
  - badcase/057-boundary-comment-trailing-comma/expected.md
  - badcase/057-boundary-comment-trailing-comma/input/example.sql
  - badcase/058-boundary-forbidden-type/expected.md
  - badcase/058-boundary-forbidden-type/input/example.sql
  - badcase/059-boundary-varchar-length/expected.md
  - badcase/059-boundary-varchar-length/input/example.sql
  - badcase/060-boundary-char-length/expected.md
  - badcase/060-boundary-char-length/input/example.sql
  - badcase/061-boundary-index-name-length/expected.md
  - badcase/061-boundary-index-name-length/input/example.sql
  - badcase/062-boundary-index-count/expected.md
  - badcase/062-boundary-index-count/input/example.sql
  - badcase/063-boundary-composite-index-width/expected.md
  - badcase/063-boundary-composite-index-width/input/example.sql
  - badcase/064-combo-type-len-fullwidth/expected.md
  - badcase/064-combo-type-len-fullwidth/input/example.sql
  - badcase/065-combo-name-length-start-chars/expected.md
  - badcase/065-combo-name-length-start-chars/input/example.sql
  - badcase/066-combo-index-three/expected.md
  - badcase/066-combo-index-three/input/example.sql
  - badcase/067-combo-field-length-start-chars/expected.md
  - badcase/067-combo-field-length-start-chars/input/example.sql
  - badcase/068-combo-lengths/expected.md
  - badcase/068-combo-lengths/input/example.sql
  - badcase/069-combo-clauses/expected.md
  - badcase/069-combo-clauses/input/example.sql
  - badcase/070-clean-basic/expected.md
  - badcase/070-clean-basic/input/example.sql
  - badcase/071-clean-log-table/expected.md
  - badcase/071-clean-log-table/input/example.sql
  - badcase/072-clean-index-heavy/expected.md
  - badcase/072-clean-index-heavy/input/example.sql
  - badcase/073-atomic-table-name-chars/expected.md
  - badcase/073-atomic-table-name-chars/input/example.sql
  - badcase/074-atomic-field-name-chars/expected.md
  - badcase/074-atomic-field-name-chars/input/example.sql
  - badcase/075-atomic-del-flag-required/expected.md
  - badcase/075-atomic-del-flag-required/input/example.sql
  - ddl-manual-rules.md
  - eval/007-clean/expected.md
  - eval/007-clean/input/example.sql
  - eval/008-real/expected.md
  - eval/008-real/input/example.sql
  - scripts/ddl_check.py
  - scripts/gen_cases.py
  - scripts/pytest.ini
  - scripts/sql_check.py
  - scripts/tests/__init__.py
  - scripts/tests/test_ddl_check.py
  - scripts/tests/test_gen_cases.py
  - scripts/tests/test_sql_check.py
  - sql-manual-rules.md
  - test/ddl-202607071777.sql
  - test/ddl-202607071777审查报告.md
  - test/test_mapper.xml
  - test/test_po.java
---

# MySQL DDL 设计与审查

## 审查工作流

严格按以下步骤执行。

### 第 1 步：运行检查脚本

脚本位于本 SKILL.md 同级的 `scripts/` 子目录，以本文件所在路径为基准定位：

```bash
# DDL 审查（.sql 建表语句）
python3 scripts/ddl_check.py [--format json]

# MyBatis SQL 审查（*Mapper.xml + @TableName PO 类，Java 项目）
python3 scripts/sql_check.py [--format json]
```

- 退出码：`0`=通过，`1`=有强制问题，`2`=运行错误
- `sql_check.py` 自动扫描 mapper XML（解析 `<if>`/`<where>`/`<foreach>` 等动态标签和 `<include>` 引用）以及 MyBatis-Plus `@TableName` 注解的 PO 类（检查表名/字段命名规范、必含字段）
- `ddl_check.py` 内置缩写字典（`scripts/abbreviations.py`），对**字段名/表名/索引名**做长写法 → 标准缩写的反向检查（**强制级别：公司数据治理要求**），扩展字典仅修改 `abbreviations.py`；缩写 value 与 MySQL/Python 保留字冲突的 key 跳过反向检查（避免违规循环）
- `ddl_check.py` 强制检查**索引名包含全部字段名**（规则 `索引名未包含全部字段`）：索引名称由所包含字段的全名称按 `<prefix>_<field1>_<field2>...` 拼接而成（prefix 为普通索引 `ix` / 唯一索引 `uk`），字段名不允许任何缩写（如把 `mch_id` 缩写为 `mch`）
- `ddl_check.py` 检查注释格式：R2（取值范围 `[k-v,...]`）+ R3（补充信息 `()` 不能与主标题完全相同）

#### 本版本新增 MANDATORY 规则清单

下列规则在本 PR（v2 升级）中由 RECOMMENDED 升级为 MANDATORY（CI 拦截），已在 `ddl_check.py` 中强制启用：

| 规则 | 触发场景 |
|---|---|
| `必含字段定义不一致` | 必含字段（id/creator_id/create_time/last_updater_id/last_update_time）在各表间名称/类型不一致 |
| `必含字段注释不一致` | 同上，注释不一致（空白不敏感比较） |
| `索引缩写未规范化` | 索引主体分词命中 `LONG_TO_SHORT` 反向映射 |
| `索引名未包含全部字段` | 索引名未按 `<prefix>_<field1>_<field2>...` 拼接完整字段名（prefix 为普通索引 `ix` / 唯一索引 `uk`，含 `_id` 后缀） |
| `注释取值范围格式` | 注释中 `[k-v]` 段格式不符 |
| `补充信息冗余` | 注释 `(...)` 补充信息与主标题完全相同 |
| `补充信息为空` | 注释出现空括号 `()` |

注：日志/流水表豁免 `last_updater_id/last_update_time` 必含项；必含字段名与规范字段名（`del_flag`）在字段名与索引列缩写检查中豁免；缩写 value 与 MySQL/Python 保留字冲突的 key 跳过反向检查（避免违规循环）。

### 第 2 步：AI 复核语义层

脚本能做的是**确定性检查**（正则、字典查表）。涉及语义判断的规则**不写入脚本**，由 ZCode agent 在审查对话中执行：

| 规则 | 检查方式 |
|---|---|
| 字段名拼写合规 | 脚本：字典查表 |
| 必含字段存在性 | 脚本：必含字段表 |
| 注释类型/长度/全角 | 脚本：正则 |
| 注释取值范围 `[k-v]` 格式 | 脚本：正则 |
| 补充信息 `()` 与主标题完全相同 | 脚本：字符串比对 |
| **R1：注释主标题 = 字段英文名直译** | **ZCode agent 复核** |
| **R3：补充信息 `()` 与主标题语义重复** | **ZCode agent 复核** |

**为什么 AI 复核放在 agent 而非脚本里**：
- ddl-guard 作为 ZCode 技能，AI 能力在 agent 这层就存在，不需要脚本自己调 LLM
- 脚本通过 subprocess 调 LLM 绕过了 agent 的 context，结果还得让 agent 再解析，链路冗余
- R1/R3 是语义判断，由 reviewer（人或 agent）基于规范文档判断更合适

具体语义复核规则详见 [`ddl-manual-rules.md`](ddl-manual-rules.md) 的"注释格式规范"章节，agent 在审查 DDL 时应主动对照该章节执行语义层校验。

### 第 3 步：读取待审查文件

脚本自动扫描全部文件后，按以下原则读取：

- 小文件直接 `Read` 全文
- 大文件先用 `Read` 带 `limit` 参数看头部（DDL 的 CREATE TABLE 语句通常集中在文件前半部分）
- 仅问题密集且需要上下文判断的文件才扩大读取范围

> 优先使用 `codebase-memory-mcp` 的 `search_graph` / `get_code_snippet` 定位 SQL 文件和 PO 类。
> 优先使用 `smart_outline` 查看结构。

### 第 4 步：补充人工判断

读取以下文档，逐表核对脚本无法覆盖的规则：

- DDL 审查 → [`ddl-manual-rules.md`](ddl-manual-rules.md)
- SQL 审查 → [`sql-manual-rules.md`](sql-manual-rules.md)

### 第 5 步：输出报告

将脚本自动检查结果与人工判断合并，输出完整审查报告；按
[`../../steering/review-report-standards.md`](../../steering/review-report-standards.md)
五段式输出（含证据边界段）。目录说明（badcase/ 回归语料、eval/ 评估集、
test/ 审查样例）见 [README](README.md)。

## 设计新表

读取 [`../../steering/database-design-specification.md`](../../steering/database-design-specification.md) 后按规范生成 DDL，再用上述脚本自检。

#!/usr/bin/env python3
"""
DDL 规范检查脚本
用法:
  python3 ddl_check.py <file_or_dir> [--spec <spec_file>] [--format text|json]

检查规则参照 steering/database-design-specification.md
退出码: 0=通过(无强制问题), 1=有强制问题, 2=运行错误
"""

import argparse
import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# guard 共享库（Severity / MYSQL_RESERVED / 文件发现 / 报告骨架）
_SHARED = Path(__file__).resolve().parent.parent.parent / "_shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# 缩略词规范模块（同目录 abbreviations.py）
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from guard_lib import (  # noqa: E402
    MYSQL_RESERVED,
    Severity,
    find_files,
    run_gate,
)
from abbreviations import (  # noqa: E402
    LONG_TO_SHORT,
    iter_abbrev_violations,
    strip_index_prefix,
)
from guard_lib import format_report_json as guard_report_json  # noqa: E402


@dataclass
class Issue:
    table: str
    severity: Severity
    rule: str
    location: str
    description: str
    suggestion: str = ""


@dataclass
class TableInfo:
    name: str
    raw_name: str
    name_line: int
    comment: Optional[str] = None
    comment_line: Optional[int] = None
    fields: list = field(default_factory=list)
    indexes: list = field(default_factory=list)
    full_text: str = ""
    is_create_table: bool = True


@dataclass
class FieldInfo:
    name: str
    raw_definition: str
    line: int
    type: str = ""
    comment: Optional[str] = None
    is_primary_key: bool = False


@dataclass
class IndexInfo:
    name: str
    raw_definition: str
    line: int
    columns: list = field(default_factory=list)
    is_unique: bool = False


# ── 禁用类型 ────────────────────────────────────────────────────────────
FORBIDDEN_TYPES = {
    "text": "禁止使用 TEXT 类型，改用 varchar 或拆表存储",
    "longtext": "禁止使用 LONGTEXT 类型，改用 varchar 或拆表存储",
    "mediumtext": "禁止使用 MEDIUMTEXT 类型，改用 varchar 或拆表存储",
    "tinytext": "禁止使用 TINYTEXT 类型，改用 varchar",
    "blob": "禁止使用 BLOB 类型，静态资源应使用文件系统，数据库仅存 URL",
    "longblob": "禁止使用 LONGBLOB 类型，静态资源应使用文件系统",
    "mediumblob": "禁止使用 MEDIUMBLOB 类型，静态资源应使用文件系统",
    "tinyblob": "禁止使用 TINYBLOB 类型，静态资源应使用文件系统",
    "json": "禁止使用 JSON 类型，改用 varchar 存储序列化字符串",
    "enum": "禁止使用 ENUM 类型，改用 tinyint 配合注释枚举值",
    "set": "禁止使用 SET 类型，改用 tinyint 或关联表",
    "timestamp": "禁止使用 TIMESTAMP 类型(timestamp 范围仅1970-2038)，改用 datetime",
    "float": "禁止使用 FLOAT 类型，改用 decimal 或 bigint",
    "double": "禁止使用 DOUBLE 类型，改用 decimal",
    "real": "禁止使用 REAL 类型(等同 double)，改用 decimal",
    "clob": "禁止使用 CLOB 类型，改用 varchar 或拆表存储",
    "lob": "禁止使用 LOB 类型，改用 varchar 或拆表存储",
}

# ── 必含字段 ────────────────────────────────────────────────────────────
REQUIRED_FIELDS = {
    "id": {"type_pattern": r"^(int|bigint)(\s*\(\s*\d+\s*\))?$", "desc": "主键id"},
    "creator_id": {"type_pattern": r"^varchar\s*\(\s*36\s*\)$", "desc": "创建人id"},
    "create_time": {"type_pattern": r"^datetime$", "desc": "创建时间"},
    "last_updater_id": {"type_pattern": r"^varchar\s*\(\s*36\s*\)$", "desc": "最后更新人id"},
    "last_update_time": {"type_pattern": r"^datetime$", "desc": "最后更新时间"},
}

# ── 缩写检查豁免字段名（公司规范字段名）────────────────────────────────
# 这些字段名虽然命中缩写字典的某些分词，但作为项目规范字段名保留长写法
# （避免 del_flag→del 等拆词副作用引起的误报）
ABBREVIATION_EXEMPT_FIELDS = {
    "del_flag",       # 逻辑删除标志（标准字段名）
}

# ── 泛化字段名（缺乏主体区分的单一名词，应加前缀）──────────────────────
GENERIC_FIELD_NAMES = {
    "name", "code", "status", "type", "title", "content", "value", "remark", "count",
}

# ── 缩写字典：未规范化写法 → 标准缩写（与规范附录同步）──────────────────
# 数据源：本目录 abbreviations.py（LONG_TO_SHORT）。如需扩展仅修改该模块。
# 注意：标准缩写不得为 MySQL 保留字（如 desc），否则建议本身即触发强制违规
ABBREVIATION_DICT = LONG_TO_SHORT

# ── 日志/流水表标识（表名含此子串者豁免更新人字段）─────────────────────
LOG_TABLE_TAGS = ("_log", "_flow", "_journal")


def _is_log_table(name: str) -> bool:
    """判断是否为日志/流水类表（表名词边界匹配，防 _logistics/_flowmeter 类子串误伤）。"""
    low = name.lower()
    return any(re.search(f"{tag}(_|$)", low) for tag in LOG_TABLE_TAGS)


def _check_abbreviation(name: str, owner: str, issues: list, kind: str = "字段"):
    """检测名称分词是否命中缩写字典的未规范化写法。

    数据源：abbreviations.LONG_TO_SHORT（长写法 → 标准缩写）。

    【强制级别：公司数据治理要求】命中即报 Severity.MANDATORY，ddl_check.py
    退出码 1（CI 拦截）。已用标准缩写时不触发（value 不在反向检查范围）。

    豁免：必含字段名（id/creator_id/create_time/last_updater_id/last_update_time）
    是公司基线硬性要求，字段名长度与写法已固化，缩写规则对此类字段名不做强制收敛。
    其他规范字段名（如 del_flag）同样豁免。
    """
    if name.lower() in REQUIRED_FIELDS or name.lower() in ABBREVIATION_EXEMPT_FIELDS:
        return  # 必含字段名 / 规范字段名豁免缩写检查
    issues.extend(Issue(
        table=owner, severity=Severity.MANDATORY, rule="缩写未规范化",
        location=f"{kind}:{name}", description=f"{kind} '{name}' 含未规范化写法 '{part}'",
        suggestion=f"改用标准缩写 '{part}' → '{std}'",
    ) for part, std in iter_abbrev_violations(name))


def _index_col_name(col: str) -> str:
    """提取索引列定义中的列标识符，忽略前缀长度（`name(10)`）与 `DESC` 等修饰。"""
    m = re.match(r"`?([A-Za-z0-9_]+)`?", col.strip())
    return m[1].lower() if m else col.lower()


def _check_index_abbreviation(table: TableInfo, idx: IndexInfo, issues: list):
    """索引缩写检查：对索引引用的每个列名做反向命中检查。

    【强制级别：公司数据治理要求】命中即报 Severity.MANDATORY。

    逐列检查（而非拆索引名分词）：组合索引（如 ix_mch_id_last_update_time）
    的必含字段列（如 last_update_time 含 `update` 分词）逐列豁免，避免与
    `索引名未包含全部字段`（要求索引名按完整字段名拼接）两条强制规则互相矛盾。

    豁免：列名属于必含字段名（REQUIRED_FIELDS）或规范字段名
    （ABBREVIATION_EXEMPT_FIELDS，如 del_flag）。索引名与列名的一致性由
    check_index_contains_columns 负责，此处只检查列名本身的缩写。
    """
    exempt_set = set(REQUIRED_FIELDS.keys()) | ABBREVIATION_EXEMPT_FIELDS
    seen: set[tuple[str, str]] = set()
    for col in idx.columns:
        col_low = _index_col_name(col)
        if col_low in exempt_set:
            continue  # 必含字段 / 规范字段逐列豁免（支持组合索引）
        for part, std in iter_abbrev_violations(col_low):
            if (part, std) in seen:
                continue  # 多列命中同一违规时去重
            seen.add((part, std))
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="索引缩写未规范化",
                location=f"表:{table.name} 索引:{idx.name}",
                description=f"索引 '{idx.name}' 列 '{col_low}' 分词 '{part}' 含未规范化写法",
                suggestion=f"改用标准缩写 '{part}' → '{std}'，并同步索引名",
            ))


# ── 全角字符范围检测 ────────────────────────────────────────────────────
FULLWIDTH_RE = re.compile(r"[\uff00-\uffef\u3000-\u303f\u2018-\u201f\u2026\u00b7]")


def strip_sql_comments(text: str) -> str:
    """Remove `--` and `#` line comments and `/* */` block comments (quote-aware:
    comment markers inside quoted strings are preserved), keeping line structure
    for line number tracking."""
    # 先剥 /* */ 块注释（引号外），内容替换为空格、保留换行，维持行号不变
    out = []
    i = 0
    n = len(text)
    in_quote = False
    quote_char = None
    while i < n:
        ch = text[i]
        if in_quote:
            out.append(ch)
            if ch == quote_char and (i == 0 or text[i - 1] != "\\"):
                in_quote = False
            i += 1
        elif ch in ("'", '"', "`"):
            in_quote = True
            quote_char = ch
            out.append(ch)
            i += 1
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                end = n
                block = text[i:]
                i = n
            else:
                block = text[i:end + 2]
                i = end + 2
            # 换行保留，其余替换为空格
            out.append(re.sub(r"[^\n]", " ", block))
        else:
            out.append(ch)
            i += 1
    text = "".join(out)

    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # Remove -- comment (but not inside quotes)
        result = []
        in_quote = False
        quote_char = None
        i = 0
        while i < len(line):
            ch = line[i]
            if in_quote:
                result.append(ch)
                if ch == quote_char and (i == 0 or line[i - 1] != "\\"):
                    in_quote = False
                i += 1
            else:
                if ch in ("'", '"', "`"):
                    in_quote = True
                    quote_char = ch
                    result.append(ch)
                    i += 1
                elif ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                    break  # rest is comment
                elif ch == "#":
                    break  # MySQL 行注释（# 起至行尾）同样剥除
                else:
                    result.append(ch)
                    i += 1
        cleaned.append("".join(result))
    return "\n".join(cleaned)


_CREATE_TABLE_RE = re.compile(
    r"(?i)create\s+(?:temporary\s+)?\s*table\s+(?:if\s+not\s+exists\s+)?"
    r"(?:`?([^`\s()]+)`?\.)?`?([^`\s()]+)`?"
)


def extract_tables(raw_text: str) -> list:
    """Parse CREATE TABLE statements from SQL text."""
    lines = raw_text.split("\n")
    tables = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if m := _CREATE_TABLE_RE.match(line):
            table_name = m[2] or m[1]
            table = TableInfo(
                name=table_name,
                raw_name=table_name,
                name_line=i + 1,
                full_text="",
                is_create_table=True,
            )
            # 表体起点：表名后可能跨行才出现 '('（CREATE TABLE t\n(...)）。
            # 有界扫描：'(' 须在下一条语句终止符 ';' 或下一条 CREATE TABLE
            # 之前出现，否则按无表体的不完整语句丢弃（如 CREATE TABLE t;），
            # 避免向后吞并后续语句的括号内容。
            start = None
            scan = i
            while scan < len(lines):
                seg = lines[scan]
                if "(" in seg:
                    start = scan
                    break
                if ";" in seg or (scan > i and _CREATE_TABLE_RE.match(seg.strip())):
                    break  # 先遇语句边界：该 CREATE TABLE 无表体，丢弃
                scan += 1
            if start is None:
                i += 1  # 无表体 '('（异常语句），跳过
                continue
            paren_depth = 0
            opened = False
            full_lines = []
            j = start
            while j < len(lines):
                seg = lines[j]
                full_lines.append(seg)
                if "(" in seg:
                    opened = True
                paren_depth += seg.count("(") - seg.count(")")
                j += 1
                if opened and paren_depth <= 0:
                    break  # 表体 '(' 已闭合（含首行即闭合的单行建表）


            full_text = "\n".join(full_lines)
            table.full_text = full_text

            # Extract table comment
            # 注：`.` 默认不跨行，与原 (?:.*?)*? 语义等价但无嵌套量词（免疫 ReDoS）
            table_comment_match = re.search(
                r"(?i)\)\s*.*?comment\s*=?\s*'([^']*)'", full_text
            )
            # Parse fields and indexes from within parens
            _parse_table_body(table, full_lines)

            # Find table comment
            table.comment_line = j  # approximate
            tables.append(table)
            i = j
        else:
            i += 1
    return tables


def _parse_table_body(table: TableInfo, body_lines: list):
    """Parse field and index definitions from CREATE TABLE body lines."""
    # Skip first line (CREATE TABLE ...) and process until closing paren
    base_line = table.name_line

    for idx, raw_line in enumerate(body_lines):
        line_no = base_line + idx
        line = raw_line.strip().rstrip(",")

        # Skip the CREATE TABLE line
        if idx == 0:
            continue

        # Skip closing paren or options after it
        if line.startswith(")") or line.startswith("ENGINE") or line.startswith("DEFAULT"):
            continue

        if not line:
            continue

        # 字段续行（跨行字段定义的属性行）：NOT NULL / NULL / DEFAULT /
        # AUTO_INCREMENT / COMMENT 开头均非新字段，跳过避免垃圾字段
        if re.match(r"(?i)^(not\s+null|null|auto_increment|comment)\b", line):
            continue

        # Check if this is a constraint/index definition
        if re.match(r"(?i)^(primary\s+key|unique\s+key|unique\s+index|unique\s+constraint)\s*", line):
            if idx_info := _parse_index_line(line, line_no):
                table.indexes.append(idx_info)
            continue

        if re.match(r"(?i)^(key|index)\s+", line):
            if idx_info := _parse_index_line(line, line_no):
                table.indexes.append(idx_info)
            continue

        if re.match(r"(?i)^(constraint|foreign\s+key|check|fulltext|spatial)\s+", line):
            continue

        if field_info := _parse_field_line(line, line_no):
            table.fields.append(field_info)


def _parse_index_line(line: str, line_no: int) -> Optional[IndexInfo]:
    """Parse an index definition line."""
    if pk_match := re.match(
        r"(?i)primary\s+key\s*(?:\(\s*([^)]+)\s*\))?", line
    ):
        cols_str = pk_match[1] or ""
        cols = [c.strip().strip("`") for c in cols_str.split(",") if c.strip()]
        return IndexInfo(
            name="PRIMARY",
            raw_definition=line,
            line=line_no,
            columns=cols,
            is_unique=False,
        )

    if uk_match := re.match(
        r"(?i)unique\s+(?:key|index)\s+`?([^`\s()]+)`?\s*\(\s*([^)]+)\s*\)",
        line,
    ):
        name = uk_match[1]
        cols_str = uk_match[2]
        cols = [c.strip().strip("`") for c in cols_str.split(",") if c.strip()]
        return IndexInfo(
            name=name,
            raw_definition=line,
            line=line_no,
            columns=cols,
            is_unique=True,
        )

    if uc_match := re.match(
        r"(?i)unique\s+constraint\s+`?([^`\s()]+)`?\s*\(\s*([^)]+)\s*\)", line
    ):
        name = uc_match[1]
        cols_str = uc_match[2]
        cols = [c.strip().strip("`") for c in cols_str.split(",") if c.strip()]
        return IndexInfo(
            name=name,
            raw_definition=line,
            line=line_no,
            columns=cols,
            is_unique=True,
        )

    if k_match := re.match(
        r"(?i)(?:key|index)\s+`?([^`\s()]+)`?\s*\(\s*([^)]+)\s*\)", line
    ):
        name = k_match[1]
        cols_str = k_match[2]
        cols = [c.strip().strip("`") for c in cols_str.split(",") if c.strip()]
        return IndexInfo(
            name=name,
            raw_definition=line,
            line=line_no,
            columns=cols,
            is_unique=False,
        )

    return None


def _parse_field_line(line: str, line_no: int) -> Optional[FieldInfo]:
    """Parse a field definition line."""
    # Match: `field_name` type(...) ...
    m = re.match(r"`?([^`\s()]+)`?\s+(.+)", line)
    if not m:
        return None

    name = m[1]
    rest = m[2]

    # Extract type (first word + optional length/precision)
    type_match = re.match(
        r"(\w+(?:\s*\([^)]+\))?|int\s*\(\s*\d+\s*\)|bigint\s*\(\s*\d+\s*\)|"
        r"tinyint\s*\(\s*\d+\s*\)|smallint\s*\(\s*\d+\s*\)|mediumint\s*\(\s*\d+\s*\)|"
        r"varchar\s*\(\s*\d+\s*\)|char\s*\(\s*\d+\s*\)|decimal\s*\(\s*\d+\s*,\s*\d+\s*\)|"
        r"datetime|date|time)",
        rest,
        re.IGNORECASE,
    )

    field_type = (
        type_match[0].strip()
        if type_match
        else rest.split()[0] if rest.split() else ""
    )

    # Extract comment
    comment_match = re.search(r"comment\s+'((?:[^'\\]|\\.)*)'", rest, re.IGNORECASE)
    comment = comment_match[1] if comment_match else None

    # Check if primary key
    is_pk = bool(re.search(r"(?i)primary\s+key", rest))

    return FieldInfo(
        name=name,
        raw_definition=line,
        line=line_no,
        type=field_type,
        comment=comment,
        is_primary_key=is_pk,
    )


def extract_table_comment(full_text: str) -> Optional[str]:
    """Extract table-level COMMENT from the full CREATE TABLE text.
    The table comment appears AFTER the final closing paren of the CREATE TABLE.
    """
    last_paren = full_text.rfind(")")
    if last_paren == -1:
        return None
    suffix = full_text[last_paren:]
    if m := re.search(
        r"comment\s*=?\s*'((?:[^'\\]|\\.)*)'", suffix, re.IGNORECASE
    ):
        return m[1]
    return None


# ── 规则检查函数 ────────────────────────────────────────────────────────


def check_forbidden_clauses(text: str, issues: list, file_path: str):
    """Check for forbidden clauses in raw DDL text."""
    lines = text.split("\n")
    for i, line in enumerate(lines, 1):
        upper = line.upper()
        if re.search(r"CHARACTER\s+SET\s*=", upper, re.IGNORECASE) or re.search(
            r"CHARSET\s*=", upper, re.IGNORECASE
        ):
            issues.append(Issue(
                table="(文件级)", severity=Severity.MANDATORY, rule="去除字符集子句",
                location=f"{file_path}:{i}", description="包含 CHARACTER SET / CHARSET 子句",
                suggestion="提交审核的 DDL 须去除 CHARACTER SET 子句",
            ))
        if re.search(r"COLLATE\s*=", upper, re.IGNORECASE) or re.search(
            r"COLLATE\s+\w+", upper, re.IGNORECASE
        ):
            issues.append(Issue(
                table="(文件级)", severity=Severity.MANDATORY, rule="去除字符序子句",
                location=f"{file_path}:{i}", description="包含 COLLATE 子句",
                suggestion="提交审核的 DDL 须去除 COLLATE 子句",
            ))
        if re.search(r"AUTO_INCREMENT\s*=\s*\d+", upper):
            issues.append(Issue(
                table="(文件级)", severity=Severity.MANDATORY, rule="去除 auto_increment 子句",
                location=f"{file_path}:{i}", description="包含 auto_increment=N 子句",
                suggestion="提交审核的 DDL 须去除 auto_increment=N 子句",
            ))
        if re.search(r"ENGINE\s*=", upper):
            issues.append(Issue(
                table="(文件级)", severity=Severity.MANDATORY, rule="去除 engine 子句",
                location=f"{file_path}:{i}", description="包含 ENGINE= 子句",
                suggestion="提交审核的 DDL 须去除 ENGINE 子句",
            ))
        if re.search(r"ROW_FORMAT\s*=", upper):
            issues.append(Issue(
                table="(文件级)", severity=Severity.MANDATORY, rule="去除 row_format 子句",
                location=f"{file_path}:{i}", description="包含 ROW_FORMAT= 子句",
                suggestion="提交审核的 DDL 须去除 ROW_FORMAT 子句",
            ))


# 禁用语句全文扫描（steering/database-design-specification.md 第七节）：
# 触发器、存储过程【强制】不得使用；自定义函数【推荐】不使用（确需时须经技术管理委员会
# 评审）。视图同为【推荐】但不在脚本拦截范围，维持人工审查（ddl-manual-rules.md）。
# 允许 MySQL 可选 DEFINER 子句（CREATE DEFINER=... TRIGGER 形态不得绕过检查）。
_FORBIDDEN_STATEMENT_RE = re.compile(
    r"(?i)\bcreate\s+(?:or\s+replace\s+)?"
    r"(?:definer\s*=\s*(?:current_user(?:\s*\(\s*\))?"
    r"|(?:'[^']*'|`[^`]*`|[^\s@]+)(?:\s*@\s*(?:'[^']*'|`[^`]*`|[^\s@]+))?)\s+)?"
    r"(trigger|procedure|function)\b")

# 引号字面量区间提取（保持行号/列号不变）：COMMENT 'migration note: CREATE TRIGGER ...'
# 这类解释性字符串不得误报为禁用语句——匹配仍跑原始文本（DEFINER='u'@'%' 值内含
# 引号，先掩码会破坏 DEFINER 子句匹配），命中起点落在字面量区间内则跳过
_SQL_LITERAL_RE = re.compile(r"'(?:[^']|'')*'|\"[^\"]*\"|`[^`]*`")

_FORBIDDEN_STATEMENTS = {
    "trigger": (
        Severity.MANDATORY, "禁用触发器", "使用了触发器(CREATE TRIGGER)",
        "不得使用触发器；历史事实类数据的删除保护（如超期不可变的交易事件禁删）"
        "为规范允许的例外，需人工判定",
    ),
    "procedure": (
        Severity.MANDATORY, "禁用存储过程", "使用了存储过程(CREATE PROCEDURE)",
        "不得使用存储过程",
    ),
    "function": (
        Severity.RECOMMENDED, "自定义函数", "使用了自定义函数(CREATE FUNCTION)",
        "不使用自定义函数，确需使用时须经技术管理委员会评审",
    ),
}


def check_forbidden_statements(text: str, issues: list, file_path: str):
    """全文级检测禁用语句（不依赖 CREATE TABLE 解析，大小写不敏感）。

    引号字面量内的解释性短语（COMMENT '... CREATE TRIGGER ...'）不报：
    命中起点落在字符串字面量区间内即跳过；匹配跑原始文本以兼容
    DEFINER='u'@'%' 等含引号的合法子句形态。
    """
    literal_spans = [m.span() for m in _SQL_LITERAL_RE.finditer(text)]

    def _in_literal(pos: int) -> bool:
        return any(s <= pos < e for s, e in literal_spans)

    for m in _FORBIDDEN_STATEMENT_RE.finditer(text):
        if _in_literal(m.start()):
            continue
        severity, rule, description, suggestion = _FORBIDDEN_STATEMENTS[m[1].lower()]
        line = text.count("\n", 0, m.start()) + 1
        issues.append(Issue(
            table="(文件级)", severity=severity, rule=rule,
            location=f"{file_path}:{line}", description=description,
            suggestion=suggestion,
        ))


def _find_bad_dash_dash(line: str) -> bool:
    """检测行内是否存在「字符串外、-- 后缺空格」的注释标记。

    引号感知：跳过 COMMENT '...--...' 字符串内的 --；仅当字符串外的
    -- 后紧跟非空白、非连字符字符时判定为违规（如 '--xxx' / 'code --xxx'）。
    """
    in_quote = False
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "'" and (i == 0 or line[i - 1] != "\\"):
            in_quote = not in_quote
        elif not in_quote and ch == "-" and i + 1 < n and line[i + 1] == "-":
            after = line[i + 2] if i + 2 < n else ""
            return after not in ("", " ", "\t", "-")
        i += 1
    return False


def check_comment_style(text: str, issues: list, file_path: str):
    """Check comment style (# or /* */ instead of -- ).

    检查两种注释风格问题：
    1. 使用 # 注释（应改为 --）
    2. 使用 -- 注释但 -- 后没有空格（不规范，应改为 -- 空格）
    """
    lines = text.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        # 检查 # 注释（排除 shebang #!）
        if stripped.startswith("#") and not stripped.startswith("#!"):
            issues.append(Issue(
                table="(文件级)", severity=Severity.MANDATORY, rule="注释符号",
                location=f"{file_path}:{i}", description="使用了 # 注释",
                suggestion="注释统一使用 '-- '(注意 -- 后有一个空格)",
            ))
        # 检查 -- 注释但 -- 后没有空格（引号感知，排除 COMMENT '...--...' 内的 --）
        if _find_bad_dash_dash(stripped):
            issues.append(Issue(
                table="(文件级)", severity=Severity.MANDATORY, rule="注释格式",
                location=f"{file_path}:{i}", description="-- 注释后缺少空格（应为 '-- '）",
                suggestion="-- 后必须跟一个空格，如 '-- 注释内容'，否则可能被误解析",
            ))


def check_partition(text: str, issues: list, file_path: str):
    """Check for partition usage."""
    if re.search(r"(?i)\bpartition\s+by\b", text):
        lines = text.split("\n")
        for i, line in enumerate(lines, 1):
            if re.search(r"(?i)\bpartition\s+by\b", line):
                issues.append(Issue(
                    table="(文件级)", severity=Severity.MANDATORY, rule="禁止分区表",
                    location=f"{file_path}:{i}", description="使用了分区表(PARTITION BY)",
                    suggestion="不得使用分区表",
                ))
                break


def check_change_column(text: str, issues: list, file_path: str):
    """Check for CHANGE COLUMN usage."""
    if re.search(r"(?i)\bchange\s+column\b", text):
        lines = text.split("\n")
        for i, line in enumerate(lines, 1):
            if re.search(r"(?i)\bchange\s+column\b", line):
                issues.append(Issue(
                    table="(文件级)", severity=Severity.MANDATORY, rule="禁止 CHANGE COLUMN",
                    location=f"{file_path}:{i}", description="使用了 CHANGE COLUMN 修改字段",
                    suggestion="使用 MODIFY 或 RENAME 语句分别修改属性和字段名",
                ))


def check_table_name(table: TableInfo, issues: list):
    """Check table naming conventions."""
    name = table.name

    # Length
    if len(name) > 30:
        issues.append(Issue(
            table=name, severity=Severity.MANDATORY, rule="表名长度",
            location=f"表名:{name}", description=f"表名长度 {len(name)} 超过 30",
            suggestion="表名长度不得超过 30，过长时可缩写",
        ))

    # Must start with letter
    if not re.match(r"^[a-z]", name):
        issues.append(Issue(
            table=name, severity=Severity.MANDATORY, rule="表名开头",
            location=f"表名:{name}", description="表名必须以小写英文字母开头",
            suggestion="表名必须以英文字母开头",
        ))

    # Only lowercase letters, underscores, digits
    if not re.match(r"^[a-z0-9_]+$", name):
        issues.append(Issue(
            table=name, severity=Severity.MANDATORY, rule="表名字符",
            location=f"表名:{name}", description="表名必须使用小写英文字母、下划线及数字",
            suggestion="表名仅使用小写字母、下划线和数字",
        ))

    # No consecutive underscores
    if "__" in name:
        issues.append(Issue(
            table=name, severity=Severity.MANDATORY, rule="连续下划线",
            location=f"表名:{name}", description="表名中不得出现连续下划线",
            suggestion="移除连续下划线",
        ))

    # Reserved word
    if name.lower() in MYSQL_RESERVED:
        issues.append(Issue(
            table=name, severity=Severity.MANDATORY, rule="保留字",
            location=f"表名:{name}", description=f"'{name}' 是 MySQL 保留字",
            suggestion="避免使用数据库保留字作为表名",
        ))

    # 缩写字典：未规范化写法提示（NAM002）
    _check_abbreviation(name, name, issues, kind="表名")


def check_table_comment(table: TableInfo, issues: list):
    """Check table comment."""
    comment = extract_table_comment(table.full_text)
    table.comment = comment

    if not comment:
        issues.append(Issue(
            table=table.name, severity=Severity.MANDATORY, rule="表注释缺失",
            location=f"表:{table.name}", description="表缺少注释",
            suggestion="每个表必须有注释，且长度不超过 64",
        ))
    else:
        if len(comment) > 64:
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="表注释长度",
                location=f"表:{table.name}", description=f"表注释长度 {len(comment)} 超过 64",
                suggestion="表注释长度不得超过 64",
            ))
        # Check special characters: allow CJK, basic Latin, digits, common punctuation
        _allowed = re.compile(
            r"[\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
            r"\(\)\[\]\u3001\u3002\uff0c\uff1b\uff1a\uff01\uff1f"
            r"\u201c\u201d\u2018\u2019\-_/.,:;!?@+#&]+"
        )
        if stripped := _allowed.sub("", comment).strip():
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="表注释特殊字符",
                location=f"表:{table.name}", description=f"表注释含特殊字符: {comment}",
                suggestion="表注释中不得出现特殊字符",
            ))


def check_required_fields(table: TableInfo, issues: list):
    """Check for required system fields.

    强制要求：每个表必须包含 id/creator_id/create_time/last_updater_id/
    last_update_time 五个字段，且这四个非主键字段的**注释必须与规范完全一致**
    （creator_id → 创建人id、create_time → 创建时间、last_updater_id →
    最后更新人id、last_update_time → 最后更新时间）。日志/流水表仅强制
    id 与 create_time，豁免更新人字段。
    """
    field_by_name = {f.name.lower(): f for f in table.fields}

    # 日志/流水表仅强制 id 与 create_time，豁免更新人字段
    required = {"id", "create_time"} if _is_log_table(table.name) else set(REQUIRED_FIELDS.keys())
    for req_name in required:
        if req_name not in field_by_name:
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="必含字段缺失",
                location=f"表:{table.name}", description=f"缺少必含字段 '{req_name}'",
                suggestion=f"新表必须包含字段: {req_name}",
            ))
            continue
        # 已存在的字段：检查类型 + 注释是否完全一致
        f = field_by_name[req_name]
        spec = REQUIRED_FIELDS[req_name]
        # 类型正则（type_pattern 自带 ^...$ 锚点,fullmatch 做完整字符串校验,
        # 避免 `int` 误匹配 `point`、`datetime` 误匹配 `default` 等子串污染）
        if not re.fullmatch(spec["type_pattern"], f.type or "", re.IGNORECASE):
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="必含字段定义不一致",
                location=f"表:{table.name} 字段:{req_name}",
                description=f"必含字段 '{req_name}' 类型 '{f.type}' 与规范不符",
                suggestion=f"应匹配类型正则 '{spec['type_pattern']}'（示例：varchar(36) / datetime）",
            ))
        # 注释必须完全一致（空白不敏感：内部多余空格容错，对中文注释友好）
        expected_comment = spec["desc"]
        actual_comment = re.sub(r"\s+", "", f.comment or "")
        expected_normalized = re.sub(r"\s+", "", expected_comment)
        if actual_comment != expected_normalized:
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="必含字段注释不一致",
                location=f"表:{table.name} 字段:{req_name}",
                description=f"必含字段 '{req_name}' 注释为 '{f.comment}'，与规范不符",
                suggestion=f"必须改为规范注释 '{expected_comment}'",
            ))


def check_field_count(table: TableInfo, issues: list):
    """Check field count."""
    if len(table.fields) > 40:
        issues.append(Issue(
            table=table.name, severity=Severity.MANDATORY, rule="字段数量",
            location=f"表:{table.name}", description=f"字段数量 {len(table.fields)} 超过 40",
            suggestion="单表字段数不超过 40，超出需重构或经技术委员会评审",
        ))


def check_field_name(field: FieldInfo, table_name: str, issues: list):
    """Check field naming conventions."""
    name = field.name

    # Length
    if len(name) > 30:
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="字段名长度",
            location=f"表:{table_name} 字段:{name}", description=f"字段名长度 {len(name)} 超过 30",
            suggestion="字段名长度不得超过 30",
        ))

    # Must start with letter
    if not re.match(r"^[a-z]", name):
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="字段名开头",
            location=f"表:{table_name} 字段:{name}", description="字段名必须以英文字母开头",
            suggestion="字段名必须以英文字母开头",
        ))

    # Only lowercase letters, underscores, digits
    if not re.match(r"^[a-z0-9_]+$", name):
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="字段名字符",
            location=f"表:{table_name} 字段:{name}", description="字段名必须使用小写字母、下划线及数字",
            suggestion="字段名仅使用小写字母、下划线和数字",
        ))

    # No consecutive underscores
    if "__" in name:
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="连续下划线",
            location=f"表:{table_name} 字段:{name}", description="字段名中不得出现连续下划线",
            suggestion="移除连续下划线",
        ))

    # Reserved word
    if name.lower() in MYSQL_RESERVED:
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="保留字",
            location=f"表:{table_name} 字段:{name}", description=f"'{name}' 是 MySQL 保留字",
            suggestion="避免使用数据库保留字作为字段名",
        ))

    # 泛化单一名词需加主体前缀（COL018）
    if name.lower() in GENERIC_FIELD_NAMES and name.lower() not in MYSQL_RESERVED:
        issues.append(Issue(
            table=table_name, severity=Severity.RECOMMENDED, rule="泛化字段名",
            location=f"表:{table_name} 字段:{name}", description=f"字段名 '{name}' 为泛化单一名词，缺乏主体区分",
            suggestion="加所属主体前缀，如 merchant_remark / order_status",
        ))

    # 缩写字典：未规范化写法提示（NAM002）
    _check_abbreviation(name, table_name, issues, kind="字段")


def check_field_comment(field: FieldInfo, table_name: str, issues: list):
    """Check field comment."""
    if not field.comment:
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="字段注释缺失",
            location=f"表:{table_name} 字段:{field.name}", description="字段缺少注释",
            suggestion="所有字段必须有注释",
        ))
        return

    comment = field.comment

    # Length
    if len(comment) > 128:
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="字段注释长度",
            location=f"表:{table_name} 字段:{field.name}", description=f"字段注释长度 {len(comment)} 超过 128",
            suggestion="字段注释长度不超过 128",
        ))

    if fw_matches := FULLWIDTH_RE.findall(comment):
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="全角字符",
            location=f"表:{table_name} 字段:{field.name}",
            description=f"字段注释含全角字符: {''.join(set(fw_matches))}",
            suggestion="注释中不应包含全角字符，中文标点改英文标点",
        ))

    # 补充信息闭合后不得用逗号追加（COL032）
    if re.search(r"\([^()]*\)[,，]", comment):
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="注释补充信息格式",
            location=f"表:{table_name} 字段:{field.name}",
            description="补充信息闭合圆括号后用逗号追加，应全部放入圆括号",
            suggestion="格式：中文名(补充信息)[枚举信息]，如 '父参数id(0=根,支持嵌套)'",
        ))

    # R2: 取值范围 [k-v,...] 格式（COL033）——所有 [..] 段逐一校验
    kv_pattern = re.compile(
        r"^[\w\u4e00-\u9fff]+[-=][^\[\],()\s]+(?:\s*,\s*[\w\u4e00-\u9fff]+[-=][^\[\],()\s]+)*\s*$"
    )
    for bracket_match in re.finditer(r"\[([^\[\]]*)\]", comment):
        bracket_content = bracket_match.group(1).strip()
        if not bracket_content:
            continue
        # 排除字符集：不允许多个 k-v 间无逗号（空格代替）、value 含括号或空白
        if not kv_pattern.match(bracket_content):
            issues.append(Issue(
                table=table_name, severity=Severity.MANDATORY, rule="注释取值范围格式",
                location=f"表:{table_name} 字段:{field.name}",
                description=f"取值范围 '[{bracket_content}]' 不符合 k-v 格式",
                suggestion="格式：[k1-v1,k2-v2,...]，如 [10-待支付,20-已支付,30-已完成]",
            ))

    # R3: 补充信息 () 语法粗略检查（COL034）——所有 (..) 段逐一校验
    # 补充信息不能与字段名主体重复（粗略判断：若括号内包含字段名的完整翻译视为冗余）
    # 精确语义判断（"订单编号(订单号)"中"订单号"是"订单编号"的同义重复）由 AI 兜底
    main_text = comment.split("(", 1)[0].strip().split("[", 1)[0].strip()
    for paren_match in re.finditer(r"\(([^()]*)\)", comment):
        paren_content = paren_match.group(1).strip()
        if not paren_content:  # 补充信息不能为空
            issues.append(Issue(
                table=table_name, severity=Severity.MANDATORY, rule="补充信息为空",
                location=f"表:{table_name} 字段:{field.name}",
                description="补充信息圆括号内为空",
                suggestion="补充信息应包含必要的额外说明，如 '父参数id(0=根)'",
            ))
        elif main_text and paren_content == main_text:
            issues.append(Issue(
                table=table_name, severity=Severity.MANDATORY, rule="补充信息冗余",
                location=f"表:{table_name} 字段:{field.name}",
                description=f"补充信息 '({paren_content})' 与主标题 '{main_text}' 完全相同",
                suggestion="补充信息应为必要的额外说明，不应与主标题重复",
            ))


def check_field_type(field: FieldInfo, table_name: str, issues: list):
    """Check for forbidden field types."""
    type_lower = field.type.lower().strip()

    # Extract base type name
    base_type_match = re.match(r"(\w+)", type_lower)
    if not base_type_match:
        return
    base_type = base_type_match[1]

    if base_type in FORBIDDEN_TYPES:
        issues.append(Issue(
            table=table_name, severity=Severity.MANDATORY, rule="禁用类型",
            location=f"表:{table_name} 字段:{field.name}", 
            description=f"字段 {field.name} 使用了禁用类型 {field.type}",
            suggestion=FORBIDDEN_TYPES[base_type],
        ))


def check_varchar_length(field: FieldInfo, table_name: str, issues: list):
    """Check varchar/char length limits."""
    type_lower = field.type.lower()
    vm = re.match(r"varchar\s*\(\s*(\d+)\s*\)", type_lower)
    cm = re.match(r"char\s*\(\s*(\d+)\s*\)", type_lower)

    if vm:
        length = int(vm[1])
        if length > 500:
            issues.append(Issue(
                table=table_name, severity=Severity.RECOMMENDED, rule="varchar长度",
                location=f"表:{table_name} 字段:{field.name}", 
                description=f"varchar({length}) 长度超过 500",
                suggestion="varchar 长度不宜超过 500",
            ))
    elif cm:
        length = int(cm[1])
        if length > 20:
            issues.append(Issue(
                table=table_name, severity=Severity.RECOMMENDED, rule="char长度",
                location=f"表:{table_name} 字段:{field.name}", 
                description=f"char({length}) 长度超过 20",
                suggestion="char 长度建议最大 20",
            ))


def check_del_flag_required(table: TableInfo, issues: list):
    """业务表必含逻辑删除字段（日志/流水表豁免）——database-design-specification §逻辑删除字段【强制】。"""
    if _is_log_table(table.name):
        return
    field_names = {f.name.lower() for f in table.fields}
    if not field_names & {"del_flag", "delete_flag", "is_deleted", "is_del", "deleted"}:
        issues.append(Issue(
            table=table.name, severity=Severity.MANDATORY, rule="逻辑删除字段缺失",
            location=f"表:{table.name}", description="业务表缺少逻辑删除字段（del_flag 及其等价别名均无）",
            suggestion="新增 del_flag tinyint NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]'（日志/流水表豁免）",
        ))


def check_del_flag(table: TableInfo, issues: list):
    """Check del_flag field naming and comment."""
    for f in table.fields:
        if f.name.lower() in ("del_flag", "delete_flag", "is_deleted", "is_del", "deleted"):
            if f.name.lower() != "del_flag":
                issues.append(Issue(
                    table=table.name, severity=Severity.MANDATORY, rule="逻辑删除字段名",
                    location=f"表:{table.name} 字段:{f.name}", 
                    description=f"逻辑删除字段名 '{f.name}' 不符合规范",
                    suggestion="逻辑删除字段统一使用 del_flag",
                ))
            if f.comment and "删除标志[0-否,1-是]" not in f.comment:
                issues.append(Issue(
                    table=table.name, severity=Severity.MANDATORY, rule="逻辑删除字段注释",
                    location=f"表:{table.name} 字段:{f.name}", 
                    description=f"逻辑删除字段注释 '{f.comment}' 不符合规范",
                    suggestion="注释统一为: 删除标志[0-否,1-是]",
                ))
            return


def check_index_naming(table: TableInfo, issues: list):
    """Check index naming conventions."""
    for idx in table.indexes:
        if idx.name == "PRIMARY":
            continue

        # Length
        if len(idx.name) > 64:
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="索引名长度",
                location=f"表:{table.name} 索引:{idx.name}",
                description=f"索引名长度 {len(idx.name)} 超过 64",
                suggestion="索引名长度不超过 64",
            ))

        # Unique index should start with uk_
        if idx.is_unique and not idx.name.lower().startswith("uk_"):
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="唯一索引命名",
                location=f"表:{table.name} 索引:{idx.name}",
                description=f"唯一索引 '{idx.name}' 未以 uk_ 开头",
                suggestion="唯一索引命名规则: uk_字段列表",
            ))

        # Regular index should start with ix_
        if not idx.is_unique and not idx.name.lower().startswith("ix_"):
            issues.append(Issue(
                table=table.name, severity=Severity.MANDATORY, rule="普通索引命名",
                location=f"表:{table.name} 索引:{idx.name}",
                description=f"普通索引 '{idx.name}' 未以 ix_ 开头",
                suggestion="普通索引命名规则: ix_字段列表",
            ))

        # 索引主体分词缩写（强制级：公司数据治理要求）
        _check_index_abbreviation(table, idx, issues)


def check_index_contains_columns(table: TableInfo, issues: list):
    """索引名主体必须等于其列标识符按声明顺序的全名拼接，不允许缩写。

    例：`ix_mch_id_msg_type_status (mch_id, msg_type, status)` 合法；
    `ix_mch_msg_type_status (mch_id, msg_type, status)` 违规——`mch_id` 被
    缩写为 `mch`。比较为精确拼接（顺序与 token 集合都必须一致）；列标识符
    提取时忽略前缀长度（`name(10)`）与 `ASC`/`DESC` 等索引修饰。
    """
    for idx in table.indexes:
        if idx.name == "PRIMARY":
            continue

        body = strip_index_prefix(idx.name)
        expected = "_".join(_index_col_name(c) for c in idx.columns)
        if body != expected:
            issues.append(Issue(
                table=table.name,
                severity=Severity.MANDATORY,
                rule="索引名未包含全部字段",
                location=f"表:{table.name} 索引:{idx.name}",
                description=(
                    f"索引 '{idx.name}' 主体 '{body}' 不等于列顺序拼接 '{expected}'"
                ),
                suggestion="索引名由字段全名称按 <prefix>_<field1>_<field2>... 顺序拼接而成（prefix 为 ix/uk），不允许缩写、调换顺序或含额外 token",
            ))


def check_unique_hint(table: TableInfo, issues: list):
    """启发式提示：字段注释含「唯一」却未被任何 UNIQUE 索引覆盖。"""
    unique_cols = set()
    for idx in table.indexes:
        if idx.is_unique:
            unique_cols.update(c.lower() for c in idx.columns)
    for f in table.fields:
        if f.comment and "唯一" in f.comment and f.name.lower() not in unique_cols:
            issues.append(Issue(
                table=table.name, severity=Severity.RECOMMENDED, rule="建议唯一索引",
                location=f"表:{table.name} 字段:{f.name}",
                description=f"字段 {f.name} 注释标注「唯一」但未建唯一索引",
                suggestion="若业务唯一，改 UNIQUE KEY uk_字段名",
            ))


def check_index_on_id(table: TableInfo, issues: list):
    """Check for redundant indexes on id field."""
    id_indexes = []
    for idx in table.indexes:
        if idx.name == "PRIMARY":
            continue
        if "id" in [c.lower() for c in idx.columns]:
            id_indexes.append(idx)

    issues.extend(
        Issue(
            table=table.name,
            severity=Severity.MANDATORY,
            rule="id重复索引",
            location=f"表:{table.name} 索引:{idx.name}",
            description=f"id 字段已有主键索引，索引 '{idx.name}' 中包含 id",
            suggestion="id 字段已有主键索引，不再建普通索引或参与联合索引",
        )
        for idx in id_indexes
    )


def check_index_count(table: TableInfo, issues: list):
    """Check index count."""
    non_pk_indexes = [i for i in table.indexes if i.name != "PRIMARY"]
    if len(non_pk_indexes) > 5:
        issues.append(Issue(
            table=table.name, severity=Severity.RECOMMENDED, rule="索引数量",
            location=f"表:{table.name}", 
            description=f"索引数量 {len(non_pk_indexes)} 超过 5",
            suggestion="单表索引个数建议最多 5 个",
        ))

    issues.extend(
        Issue(
            table=table.name,
            severity=Severity.RECOMMENDED,
            rule="联合索引字段数",
            location=f"表:{table.name} 索引:{idx.name}",
            description=f"联合索引 '{idx.name}' 包含 {len(idx.columns)} 个字段，超过 5",
            suggestion="索引中包含的字段个数建议最多 5 个",
        )
        for idx in non_pk_indexes
        if len(idx.columns) > 5
    )


def check_foreign_key(table: TableInfo, issues: list):
    """Check for foreign key constraints."""
    if re.search(r"(?i)\bforeign\s+key\b", table.full_text):
        issues.append(Issue(
            table=table.name, severity=Severity.MANDATORY, rule="外键约束",
            location=f"表:{table.name}", description="使用了外键约束(FOREIGN KEY)",
            suggestion="不得使用外键约束",
        ))


def check_primary_key_int(table: TableInfo, issues: list):
    """Check that primary key id is integer type."""
    for f in table.fields:
        if f.name.lower() == "id":
            type_lower = f.type.lower()
            if not re.search(r"(int|bigint)", type_lower):
                issues.append(Issue(
                    table=table.name, severity=Severity.MANDATORY, rule="主键类型",
                    location=f"表:{table.name} 字段:id", 
                    description=f"主键 id 类型为 {f.type}，应为整型",
                    suggestion="主键 id 字段类型必须为整型(int/bigint)",
                ))


# ── 主检查流程 ──────────────────────────────────────────────────────────


def check_file(file_path: str) -> list:
    """Run all checks on a single file, return list of Issue."""
    issues = []

    with open(file_path, "r", encoding="utf-8") as fh:
        raw_text = fh.read()

    # File-level checks on comment-stripped text（注释中的关键字不参与检查）
    stripped_text = strip_sql_comments(raw_text)
    check_forbidden_clauses(stripped_text, issues, file_path)
    check_forbidden_statements(stripped_text, issues, file_path)
    check_comment_style(raw_text, issues, file_path)
    check_partition(stripped_text, issues, file_path)
    check_change_column(stripped_text, issues, file_path)

    # Parse tables
    tables = extract_tables(stripped_text)

    if not tables:
        issues.append(Issue(
            table="(文件级)", severity=Severity.MANDATORY, rule="无建表语句",
            location=file_path, description="文件中未找到 CREATE TABLE 语句",
            suggestion="DDL 文件应包含 CREATE TABLE 语句",
        ))

    for table in tables:
        check_table_name(table, issues)
        check_table_comment(table, issues)
        check_required_fields(table, issues)
        check_field_count(table, issues)
        check_primary_key_int(table, issues)
        check_del_flag_required(table, issues)
        check_del_flag(table, issues)
        check_foreign_key(table, issues)

        for field in table.fields:
            check_field_name(field, table.name, issues)
            check_field_comment(field, table.name, issues)
            check_field_type(field, table.name, issues)
            check_varchar_length(field, table.name, issues)

        check_index_naming(table, issues)
        check_index_contains_columns(table, issues)
        check_unique_hint(table, issues)
        check_index_on_id(table, issues)
        check_index_count(table, issues)

    return issues


def format_report_text(file_path: str, issues: list) -> str:
    """Format issues as readable text report."""
    if not issues:
        return f"✓ {file_path} — 检查通过，无规范问题\n"

    mandatory = [i for i in issues if i.severity == Severity.MANDATORY]
    recommended = [i for i in issues if i.severity == Severity.RECOMMENDED]

    lines = [
        f"{'=' * 60}",
        f"DDL 审查报告: {file_path}",
        f"{'=' * 60}",
        f"  【强制】问题: {len(mandatory)} 项",
        f"  【推荐】问题: {len(recommended)} 项",
        "",
    ]

    # Group by table
    tables = sorted({i.table for i in issues})
    for table_name in tables:
        table_issues = [i for i in issues if i.table == table_name]
        lines.append(f"  ── {table_name} ──")
        for idx, issue in enumerate(table_issues, 1):
            severity_tag = f"[{issue.severity.value}]"
            lines.extend([
                f"    {idx}. {severity_tag} {issue.rule}",
                f"       位置: {issue.location}",
                f"       问题: {issue.description}",
            ])
            if issue.suggestion:
                lines.append(f"       建议: {issue.suggestion}")
        lines.append("")

    return "\n".join(lines)


def format_report_json(file_path: str, issues: list) -> str:
    """Format issues as JSON（骨架在 guard_lib.format_report_json）."""
    return guard_report_json(file_path, issues, lambda i: {
        "table": i.table,
        "severity": i.severity.value,
        "rule": i.rule,
        "location": i.location,
        "description": i.description,
        "suggestion": i.suggestion,
    })


def main():
    parser = argparse.ArgumentParser(
        description="DDL 规范检查脚本 - 检查 MySQL DDL 文件是否符合数据库设计开发规范"
    )
    parser.add_argument("path", nargs="?", default=".", help="SQL 文件或目录路径（默认当前目录）")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    args = parser.parse_args()

    sql_files = find_files(args.path, lambda f: f.endswith(".sql"))
    if not sql_files:
        print("未找到 .sql 文件", file=sys.stderr)
        return 2

    return run_gate(
        [(f, check_file) for f in sql_files],
        args.format, format_report_text, format_report_json,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

#!/usr/bin/env python3
"""ddl-guard badcase 自动生成器（四层）+ 真实回流管道。

四层：
  - 正·原子   每条脚本规则一个违规模板（可归因：失败即知哪条规则）
  - 正·组合   2-3 规则同 case（合并检出 / 评分交互 / 模板副作用消解）
  - 反·全规范 完全合规 DDL（放行，precision 基线）
  - 反·近边界 每规则「恰好合规」变体（阈值边界，防误拦——GEPA 防过度拦截关键维度）

质量门禁（生成即验证）：
  每个候选 case 生成后跑 ddl_check.py，要求「实际检出规则集 == 标注规则集」：
  - expected 永远与脚本实际检出同源（模板与实现零漂移）
  - 模板副作用（一个违规变换意外触发其他规则）由门禁拦截并丢弃
  - 人工规则行不预拟（005/006 虚标教训）——生成的 case 不写「人工补充规则」行，
    人工语义规则走真实 LLM 报告回流

用法:
  python3 gen_cases.py generate [--out <dir>] [--dry-run] [--verbose]
  python3 gen_cases.py ingest --dir <真实SQL目录> [--out <dir>] [--dry-run]

说明:
  - 生成 case 写入 badcase/ 或 eval/ 下 NNN-<id>/（input/ + expected.md），
    自动纳入 badcase_runner 回归与 GEPA 评估集（evo.py 扫描 badcase/ + eval/）
  - 编号从 010 起（001-006 badcase / 007-008 eval 已占用，避免混淆）
  - ingest 把真实 DDL 匿名化后入库：脚本检出行自动写 expected，人工行留空
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DDL_CHECK = _SCRIPT_DIR / "ddl_check.py"

# ── 基础合规表（所有模板的基座，必须零检出）──────────────────────────────
# 字段行固定宽度对齐（替换操作依赖精确子串）。
BASE_TABLE = """-- 订单信息表
CREATE TABLE t_order_info (
    id              bigint(20)     NOT NULL COMMENT '主键id',
    order_no        varchar(36)    NOT NULL COMMENT '订单编号',
    creator_id      varchar(36)    NOT NULL COMMENT '创建人id',
    create_time     datetime       NOT NULL COMMENT '创建时间',
    last_updater_id varchar(36)    NOT NULL COMMENT '最后更新人id',
    last_update_time datetime      NOT NULL COMMENT '最后更新时间',
    del_flag        tinyint(4)     NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',
    order_status    varchar(10)    NOT NULL COMMENT '订单状态',
    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_no (order_no),
    KEY ix_order_status (order_status)
) COMMENT = '订单信息表';
"""


def _ops(sql: str, ops: list) -> str:
    """顺序应用 (old, new) 替换对；old 必须命中且唯一。"""
    for old, new in ops:
        if old not in sql:
            raise ValueError(f"模板片段未命中: {old!r}")
        sql = sql.replace(old, new)
    return sql


def _v(ops):
    return lambda: _ops(BASE_TABLE, ops)


def _b(ops):
    return lambda: _ops(BASE_TABLE, ops)


# ── 规则模板库：rule → (id, 中文标题, violation, boundary) ───────────────
# violation 触发目标规则；boundary 恰好不触发（放行型近边界）。boundary=None
# 表示与 BASE 相同（目标规则的合规形态即 BASE 本身）。
# 每条规则一个条目，结构：(rule, id, title, violation_ops, boundary_ops|None)
TEMPLATES = [
    # ── 文件级子句 ────────────────────────────────────────────────────
    ("去除字符集子句", "charset-clause", "字符集子句",
     [(") COMMENT = '订单信息表';", ") COMMENT = '订单信息表' CHARACTER SET=utf8mb4;")], None),
    ("去除字符序子句", "collate-clause", "字符序子句",
     [(") COMMENT = '订单信息表';", ") COMMENT = '订单信息表' COLLATE=utf8mb4_unicode_ci;")], None),
    ("去除 auto_increment 子句", "auto-increment-clause", "auto_increment 子句",
     [(") COMMENT = '订单信息表';", ") COMMENT = '订单信息表' AUTO_INCREMENT=1000;")], None),
    ("去除 engine 子句", "engine-clause", "engine 子句",
     [(") COMMENT = '订单信息表';", ") ENGINE=InnoDB COMMENT = '订单信息表';")], None),
    ("去除 row_format 子句", "row-format-clause", "row_format 子句",
     [(") COMMENT = '订单信息表';", ") ROW_FORMAT=DYNAMIC COMMENT = '订单信息表';")], None),
    ("禁止分区表", "partition", "分区表",
     [(") COMMENT = '订单信息表';", ") PARTITION BY RANGE (id) COMMENT = '订单信息表';")], None),
    # ── 注释风格（文件级）──────────────────────────────────────────────
    ("注释符号", "hash-comment", "# 注释",
     [("-- 订单信息表", "# 订单信息表")], None),
    ("注释格式", "dash-comment-space", "-- 注释缺空格",
     [("-- 订单信息表", "--订单信息表")], None),
    # ── 表级命名 ──────────────────────────────────────────────────────
    ("表名长度", "table-name-length", "表名长度",
     [("t_order_info", "t_order_info_abcdefghijklmnopqrst")],
     [("t_order_info", "t_order_info_abcdefghijklmno")]),
    ("表名字符", "table-name-chars", "表名字符",
     [("t_order_info", "t-order-info")], None),
    ("表名开头", "table-name-start", "表名开头",
     [("t_order_info", "9order_info")], None),
    ("连续下划线", "table-name-double-underscore", "表名连续下划线",
     [("t_order_info", "t_order__info")], None),
    ("保留字", "table-name-reserved", "表名保留字",
     [("t_order_info", "desc")], None),
    ("缩写未规范化", "table-name-abbrev", "表名缩写未规范化",
     [("t_order_info", "t_direction_info")], None),
    # ── 表注释 ────────────────────────────────────────────────────────
    ("表注释缺失", "table-comment-missing", "表注释缺失",
     [(") COMMENT = '订单信息表';", ");")], None),
    ("表注释长度", "table-comment-length", "表注释长度",
     [(") COMMENT = '订单信息表';", f") COMMENT = '{'表' * 65}';")],
     [(") COMMENT = '订单信息表';", f") COMMENT = '{'表' * 64}';")]),
    ("表注释特殊字符", "table-comment-special", "表注释特殊字符",
     [("'订单信息表'", "'订单信息表$'")], None),
    # ── 结构 ──────────────────────────────────────────────────────────
    ("必含字段缺失", "required-fields", "必含字段缺失",
     [("    creator_id      varchar(36)    NOT NULL COMMENT '创建人id',\n", "")], None),
    ("字段数量", "field-count", "字段数量",
     [("    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',\n",
       "".join(f"    f{i:02d}            varchar(20)    NOT NULL COMMENT '字段{i:02d}',\n" for i in range(1, 34)))],
     [("    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',\n",
       "".join(f"    f{i:02d}            varchar(20)    NOT NULL COMMENT '字段{i:02d}',\n" for i in range(1, 33)))]),
    ("主键类型", "primary-key-type", "主键类型",
     # id 改 varchar 后必然同时触发必含字段定义不一致（id 须为 int/bigint）
     [("    id              bigint(20)", "    id              varchar(36)")],
     None, ["必含字段定义不一致"]),
    ("逻辑删除字段名", "del-flag-name", "逻辑删除字段名",
     [("    del_flag        tinyint(4)", "    is_deleted      tinyint(4)")], None),
    ("逻辑删除字段注释", "del-flag-comment", "逻辑删除字段注释",
     [("'删除标志[0-否,1-是]'", "'是否删除[0-否,1-是]'")], None),
    ("外键约束", "foreign-key", "外键约束",
     [("KEY ix_order_status (order_status)\n) COMMENT = '订单信息表';",
       "KEY ix_order_status (order_status),\n    CONSTRAINT fk_order_buyer FOREIGN KEY (buyer_id) REFERENCES t_user (id)\n) COMMENT = '订单信息表';")], None),
    # ── 字段命名 ──────────────────────────────────────────────────────
    ("字段名字符", "field-name-chars", "字段名字符",
     [("    order_status    varchar(10)", "    order-status    varchar(10)")], None),
    ("字段名长度", "field-name-length", "字段名长度",
     [("order_status", "order_status_" + "a" * 18),  # 31 字符
      ("COMMENT '订单状态'", "COMMENT '订单状态字段'")],
     [("order_status", "order_status_" + "a" * 17),  # 30 字符
      ("COMMENT '订单状态'", "COMMENT '订单状态字段'")]),
    # 注：「字段名字符」不可达——字段正则 \w+ 截断非法字符（解析为截断名）
    ("字段名开头", "field-name-start", "字段名开头",
     [("order_status", "9rder_status"), ("COMMENT '订单状态'", "COMMENT '订单状态字段'")], None),
    ("连续下划线", "field-name-double-underscore", "字段连续下划线",
     [("order_status", "order__status"), ("COMMENT '订单状态'", "COMMENT '订单状态字段'")], None),
    ("保留字", "field-name-reserved", "字段保留字",
     [("order_status", "desc"), ("COMMENT '订单状态'", "COMMENT '描述字段'")], None),
    ("泛化字段名", "generic-field-name", "泛化字段名",
     [("    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',\n",
       "    status          varchar(10)    NOT NULL COMMENT '状态',\n    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',\n")], None),
    ("缩写未规范化", "field-name-abbrev", "字段缩写未规范化",
     [("buyer_id", "direction"), ("COMMENT '买家id'", "COMMENT '方向'")], None),
    # ── 字段注释 ──────────────────────────────────────────────────────
    ("字段注释缺失", "field-comment-missing", "字段注释缺失",
     [("    order_no        varchar(36)    NOT NULL COMMENT '订单编号',",
       "    order_no        varchar(36)    NOT NULL,")], None),
    ("字段注释长度", "field-comment-length", "字段注释长度",
     [("'订单编号'", f"'{'号' * 129}'")],
     [("'订单编号'", f"'{'号' * 128}'")]),
    ("全角字符", "fullwidth-char", "全角字符",
     [("'订单编号'", "'订单编号（订单号）'")], None),
    ("注释补充信息格式", "comment-trailing-comma", "注释补充信息格式",
     [("'订单编号'", "'订单编号(订单号),补充'")],
     [("'订单编号'", "'订单编号(订单号)'")]),
    # ── 类型 ──────────────────────────────────────────────────────────
    ("禁用类型", "forbidden-type", "禁用类型",
     [("order_status    varchar(10)    NOT NULL COMMENT '订单状态',",
       "data1           text           NOT NULL COMMENT '数据1',")],
     [("order_status    varchar(10)    NOT NULL COMMENT '订单状态',",
       "data1           varchar(100)   NOT NULL COMMENT '数据1',")]),
    ("varchar长度", "varchar-length", "varchar 长度",
     [("    order_no        varchar(36)", "    order_no        varchar(501)")],
     [("    order_no        varchar(36)", "    order_no        varchar(500)")]),
    ("char长度", "char-length", "char 长度",
     [("    order_status    varchar(10)", "    order_code      char(21)")],
     [("    order_status    varchar(10)", "    order_code      char(20)")]),
    # ── 索引 ──────────────────────────────────────────────────────────
    ("索引名长度", "index-name-length", "索引名长度",
     # 65 字符随机名无法满足「索引名含全部字段」，附带期望一并声明
     [("KEY ix_order_status (order_status)",
       "KEY ix_" + "a" * 62 + " (order_status)")],
     # 边界：恰好 64 字符的合法拼接名（ix_ + 61 字符 body）
     [("KEY ix_order_status (order_status)",
       "KEY ix_last_update_time_order_status_create_time_creator_id_buyer_id "
       "(last_update_time, order_status, create_time, creator_id, buyer_id)")],
     ["索引名未包含全部字段"]),
    ("唯一索引命名", "unique-index-name", "唯一索引命名",
     # 无前缀名恰好等于列拼接，仅违反 uk_ 前缀要求
     [("UNIQUE KEY uk_order_no (order_no)", "UNIQUE KEY order_no (order_no)")], None),
    ("普通索引命名", "normal-index-name", "普通索引命名",
     # 无前缀名恰好等于列拼接，仅违反 ix_ 前缀要求
     [("KEY ix_order_status (order_status)", "KEY order_status (order_status)")], None),
    ("建议唯一索引", "unique-hint", "建议唯一索引",
     [("COMMENT '订单状态'", "COMMENT '订单状态唯一'")], None),
    ("id重复索引", "index-on-id", "id 重复索引",
     [("KEY ix_order_status (order_status)",
       "KEY ix_order_status (order_status),\n    KEY ix_id (id)")], None),
    ("索引数量", "index-count", "索引数量",
     [("KEY ix_order_status (order_status)",
       "KEY ix_order_status (order_status),\n    KEY ix_buyer_id (buyer_id),\n    KEY ix_create_time (create_time),\n    KEY ix_order_status_buyer_id (order_status, buyer_id),\n    KEY ix_buyer_id_create_time (buyer_id, create_time)")],
     [("KEY ix_order_status (order_status)",
       "KEY ix_order_status (order_status),\n    KEY ix_buyer_id (buyer_id),\n    KEY ix_create_time (create_time),\n    KEY ix_order_status_buyer_id (order_status, buyer_id)")]),
    ("联合索引字段数", "composite-index-width", "联合索引字段数",
     # 违规：6 列拼接名 59 字符（避免含 id 触发 id重复索引）
     [("    order_status    varchar(10)    NOT NULL COMMENT '订单状态',",
       "    order_status    varchar(10)    NOT NULL COMMENT '订单状态',\n"
       "    biz_no          varchar(20)    NOT NULL COMMENT '业务编号',"),
      ("KEY ix_order_status (order_status)",
       "KEY ix_biz_no_order_no_buyer_id_del_flag_creator_id_create_time "
       "(biz_no, order_no, buyer_id, del_flag, creator_id, create_time)")],
     # 边界：5 列拼接名 63 字符
     [("KEY ix_order_status (order_status)",
       "KEY ix_order_status_buyer_id_create_time_last_update_time_del_flag "
       "(order_status, buyer_id, create_time, last_update_time, del_flag)")]),
]

# ── 组合 case：2-3 规则同 case（同一表/文件内同时触发）───────────────────
# 结构：(id, title, 期望规则集, ops 列表（顺序应用，可跨行）)
COMBO_TEMPLATES = [
    ("combo-type-len-fullwidth", "类型/长度/全角组合",
     ["禁用类型", "varchar长度", "全角字符"],
     [("order_status    varchar(10)    NOT NULL COMMENT '订单状态',",
       "data1           text           NOT NULL COMMENT '数据1',"),
      ("    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',",
       "    order_no2       varchar(501)   NOT NULL COMMENT '订单编号2（补充）',"),
      ("    order_no        varchar(36)    NOT NULL COMMENT '订单编号',",
       "    order_no        varchar(36)    NOT NULL COMMENT '订单编号（订单号）',")]),
    ("combo-name-length-start-chars", "表名长度/开头/字符组合",
     ["表名长度", "表名开头", "表注释缺失"],
     [("t_order_info", "9order_info_abcdefghijklmnopqrstuvw"),   # 数字开头 + 31 字符
      (") COMMENT = '订单信息表';", ");")]),                      # 注释缺失叠加
    ("combo-index-three", "索引命名三连",
     ["唯一索引命名", "普通索引命名", "id重复索引"],
     [("UNIQUE KEY uk_order_no (order_no)", "UNIQUE KEY order_no (order_no)"),
      ("KEY ix_order_status (order_status)",
       "KEY order_status (order_status),\n    KEY ix_id (id)")]),
    ("combo-field-length-start-chars", "字段名长度/开头/字符组合",
     ["字段名长度", "字段名开头"],
     [("order_status", "9rder_status_abcdefghijklmnopqrstuv"),
      ("COMMENT '订单状态'", "COMMENT '订单状态字段'")]),
    ("combo-lengths", "长度三连",
     ["表注释长度", "varchar长度", "必含字段缺失"],
     [(") COMMENT = '订单信息表';",
       f") COMMENT = '{'表' * 65}';"),
      ("    order_no        varchar(36)", "    order_no        varchar(501)"),
      ("    creator_id      varchar(36)    NOT NULL COMMENT '创建人id',\n", "")]),
    ("combo-clauses", "文件级子句三连",
     ["去除 engine 子句", "去除 row_format 子句", "去除字符集子句"],
     [(") COMMENT = '订单信息表';",
       ") ENGINE=InnoDB ROW_FORMAT=DYNAMIC CHARACTER SET=utf8mb4 COMMENT = '订单信息表';")]),
]

# ── 放行型：全规范 clean 变体（expected 空）──────────────────────────────
CLEAN_TEMPLATES = [
    ("clean-basic", "全规范基础表",
     [("-- 订单信息表", "-- 用户联系表"),
      ("t_order_info", "t_user_contact"),
      ("    order_no        varchar(36)    NOT NULL COMMENT '订单编号',",
       "    user_name       varchar(50)    NOT NULL COMMENT '用户名称',"),
      ("    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',",
       "    contact_phone   varchar(20)    NULL COMMENT '联系电话',"),
      ("'订单信息表'", "'用户联系表'"),
      ("UNIQUE KEY uk_order_no (order_no)", "UNIQUE KEY uk_user_name (user_name)")]),
    ("clean-log-table", "全规范日志表（豁免更新人字段）",
     [("-- 订单信息表", "-- 操作日志表"),
      ("t_order_info", "t_operator_log"),
      ("    order_no        varchar(36)    NOT NULL COMMENT '订单编号',",
       "    operator_type   varchar(20)    NOT NULL COMMENT '操作类型',"),
      ("    order_status    varchar(10)    NOT NULL COMMENT '订单状态',",
       "    operator_status varchar(20)    NOT NULL COMMENT '操作状态',"),
      ("    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',",
       "    operator_id     bigint(20)     NOT NULL COMMENT '操作人id',"),
      ("    last_updater_id varchar(36)    NOT NULL COMMENT '最后更新人id',\n", ""),
      ("    last_update_time datetime      NOT NULL COMMENT '最后更新时间',\n", ""),
      ("UNIQUE KEY uk_order_no (order_no)", "UNIQUE KEY uk_creator_id (creator_id)"),
      ("KEY ix_order_status (order_status)", "KEY ix_operator_status (operator_status)"),
      ("'订单信息表'", "'操作日志表'")]),
    ("clean-index-heavy", "全规范索引密集表",
     [("-- 订单信息表", "-- 订单商品表"),
      ("t_order_info", "t_order_goods"),
      ("    order_no        varchar(36)    NOT NULL COMMENT '订单编号',",
       "    order_no        varchar(36)    NOT NULL COMMENT '订单编号',\n"
       "    goods_id        bigint(20)     NOT NULL COMMENT '商品id',\n"
       "    goods_quantity  int(11)        NOT NULL COMMENT '商品数量',\n"
       "    goods_price     decimal(10,2)  NOT NULL COMMENT '商品单价',"),
      ("UNIQUE KEY uk_order_no (order_no)",
       "UNIQUE KEY uk_order_no_goods_id (order_no, goods_id)"),
      ("KEY ix_order_status (order_status)",
       "KEY ix_goods_quantity (goods_quantity),\n    KEY ix_goods_price (goods_price),\n    KEY ix_goods_id_order_no (goods_id, order_no)"),
      ("'订单信息表'", "'订单商品表'")]),
]


def run_ddl_check(input_dir: Path) -> list:
    """跑 ddl_check.py 返回 rule 列表（有序去重）。"""
    proc = subprocess.run(
        [sys.executable, str(_DDL_CHECK), str(input_dir), "--format", "json"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ddl_check 失败 rc={proc.returncode}: {proc.stderr[:300]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ddl_check 输出非 JSON: {e}") from e
    rules = []
    for f in data:
        for issue in f.get("issues", []):
            r = issue.get("rule", "")
            if r and r not in rules:
                rules.append(r)
    return rules


def render_expected(kind: str, title: str, rules: list, src: str) -> str:
    """渲染 expected.md。rules 为空 → 放行型。"""
    if rules:
        return (f"# ddl-guard badcase — {title}\n\n"
                f"check: ddl_check.py\n\n"
                f"## 说明\n\n"
                f"自动生成（gen_cases.py {kind}）：{title}。生成即验证：实际检出 == "
                f"标注（与 ddl_check.py 同源，模板副作用由门禁拦截）。\n\n"
                f"来源: {src}\n\n"
                f"## 预期检查输出\n\n"
                f"- 脚本自动检出：{'、'.join(rules)}\n")
    return (f"# ddl-guard eval — {title}（放行型）\n\n"
            f"check: ddl_check.py\n\n"
            f"## 说明\n\n"
            f"自动生成（gen_cases.py {kind}）放行案例：预期零检出。\n\n"
            f"来源: {src}\n\n"
            f"## 预期检查输出\n\n"
            f"（本 case 无脚本自动检出项——生成即验证：ddl_check.py 检出为空）\n")


def next_case_number(out_dir: Path, start: int = 10) -> int:
    """扫描目录取最大编号 + 1，从 start 起。"""
    if not out_dir.is_dir():
        return start
    nums = []
    for p in out_dir.iterdir():
        if p.is_dir():
            if m := re.match(r"^(\d{3})-", p.name):
                nums.append(int(m[1]))
    return max([start - 1] + nums) + 1


def write_case(out_dir: Path, number: int, cid: str, title: str,
               sql: str, rules: list, kind: str, src: str) -> Path:
    case_dir = out_dir / f"{number:03d}-{cid}"
    input_dir = case_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / "example.sql").write_text(sql, encoding="utf-8")
    (case_dir / "expected.md").write_text(
        render_expected(kind, title, rules, src), encoding="utf-8")
    return case_dir


def generate(out_dir: Path, dry_run: bool, verbose: bool) -> tuple:
    """生成四层 case。返回 (generated, rejected, rejected_detail)。"""
    generated = rejected = 0
    rejected_detail = []
    # 幂等：已存在的 case 按 cid 复用编号（覆盖重写），新 cid 分配新编号
    existing = {}
    if out_dir.is_dir():
        for _d in out_dir.iterdir():
            _mm = re.match(r"^(\d{3})-(.+)$", _d.name)
            if _mm and _d.is_dir():
                existing[_mm[2]] = int(_mm[1])
    number = max(existing.values(), default=9) + 1


    def _emit(cid, title, sql, want_rules, kind, src):
        nonlocal generated, rejected, number
        if dry_run:
            # dry-run：只验证门禁（临时目录），不落盘
            with tempfile.TemporaryDirectory() as td:
                tmp_in = Path(td) / "input"
                tmp_in.mkdir()
                (tmp_in / "example.sql").write_text(sql, encoding="utf-8")
                actual = run_ddl_check(tmp_in)
            ok = set(actual) == set(want_rules)
        else:
            # 直接写入目标目录再验证（验证通过保留，失败则删除）
            # cid 已存在 → 复用原编号（幂等覆盖）；新 cid → 递增分配
            n = existing.get(cid, number)
            case_dir = write_case(out_dir, n, cid, title, sql, want_rules,
                                  kind, src)
            if cid not in existing:
                number += 1
                existing[cid] = n
            actual = run_ddl_check(case_dir / "input")
            ok = set(actual) == set(want_rules)
            if not ok:
                shutil.rmtree(case_dir)
        if ok:
            generated += 1
            if verbose:
                print(f"  ✓ {cid} -> {want_rules or '（放行）'}")
        else:
            rejected += 1
            rejected_detail.append(
                f"  ✗ {cid}: 期望 {want_rules or '∅'} 实际 {actual}")
        return ok

    # 正·原子：每条规则的 violation；可选第 6 元素声明违规模板附带触发的规则
    for tpl in TEMPLATES:
        rule, cid, title, v_ops, b_ops, *extra = tpl
        extra = extra[0] if extra else []
        _emit(f"atomic-{cid}", f"违规-{title}",
              _v(v_ops)(), [rule, *extra], "正·原子", f"规则「{rule}」违规模板")
    # 反·近边界：boundary 与 BASE 不同者
    for tpl in TEMPLATES:
        rule, cid, title, v_ops, b_ops, *_ = tpl
        if b_ops:
            _emit(f"boundary-{cid}", f"近边界合规-{title}",
                  _b(b_ops)(), [], "反·近边界", f"规则「{rule}」边界模板")
    # 正·组合
    for cid, title, want_rules, ops in COMBO_TEMPLATES:
        sql = _ops(BASE_TABLE, ops)
        _emit(cid, f"组合-{title}", sql, want_rules, "正·组合", "组合模板")
    # 反·全规范
    for cid, title, ops in CLEAN_TEMPLATES:
        _emit(cid, f"全规范-{title}", _ops(BASE_TABLE, ops), [],
              "反·全规范", "全规范模板")

    return generated, rejected, rejected_detail


# ── ingest：真实 DDL 回流（匿名化 → 脚本检出行自动写 expected）──────────

def anonymize_sql(sql: str) -> str:
    """匿名化表名/字段名（保结构：类型/注释/索引不动）。

    表名 -> t_anon_<n>；字段名 -> f_anon_<n>。命名类真实缺陷（如拼音表名）
    会被匿名化覆盖——脚本类缺陷（类型/注释/索引/结构）完整保留。
    """
    field_seen = {}
    table_seen = {}
    reserved = {"id", "creator_id", "create_time",
                "last_updater_id", "last_update_time", "del_flag"}

    def _anon_field(m):
        name = m.group(1)
        if name.lower() in reserved:
            return name  # 规范必含字段保留原名（匿名化会破坏必含语义）
        if name not in field_seen:
            field_seen[name] = f"f_anon_{len(field_seen) + 1}"
        return field_seen[name]

    def _anon_table(name):
        if name not in table_seen:
            table_seen[name] = f"t_anon_{len(table_seen) + 1}"
        return table_seen[name]

    # 字段名（CREATE TABLE 体内：行首标识符 + 类型）
    sql = re.sub(
        r"^\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+(bigint|int|varchar|char|datetime|"
        r"decimal|tinyint|text|blob|json|enum|set|timestamp|float|double)\b",
        lambda m: m.group(0).replace(m.group(1), _anon_field(m)),
        sql, flags=re.M)
    # 表名
    sql = re.sub(r"CREATE\s+TABLE\s+([a-zA-Z_][a-zA-Z0-9_]*)",
                 lambda m: f"CREATE TABLE {_anon_table(m.group(1))}", sql,
                 flags=re.I)
    return sql


def ingest(directory: Path, out_dir: Path, dry_run: bool, verbose: bool) -> tuple:
    """真实 DDL 回流：匿名化 → 脚本检出 → expected 自动写。"""
    generated = rejected = 0
    number = next_case_number(out_dir)
    for sql_file in sorted(directory.glob("*.sql")):
        sql = anonymize_sql(sql_file.read_text(encoding="utf-8"))
        cid = f"real-{sql_file.stem[:20]}"
        title = f"真实回流-{sql_file.stem}"
        with tempfile.TemporaryDirectory() as td:
            tmp_in = Path(td) / "input"
            tmp_in.mkdir()
            (tmp_in / "example.sql").write_text(sql, encoding="utf-8")
            try:
                rules = run_ddl_check(tmp_in)
            except RuntimeError as e:
                rejected += 1
                if verbose:
                    print(f"  ✗ {cid}: {e}")
                continue
        if dry_run:
            generated += 1
            if verbose:
                print(f"  ✓ {cid} -> {rules or '（放行）'}")
            continue
        case_dir = write_case(out_dir, number, cid, title, sql, rules,
                              "真实回流", f"{sql_file.name}（匿名化）")
        number += 1
        generated += 1
        if verbose:
            print(f"  ✓ {cid} -> {case_dir.name} ({rules or '放行'})")
    return generated, rejected, []


def main() -> int:
    parser = argparse.ArgumentParser(description="ddl-guard badcase 自动生成器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="生成四层案例")
    g.add_argument("--out", default=str(_SCRIPT_DIR.parent / "badcase"))
    g.add_argument("--dry-run", action="store_true", help="只验证门禁不落盘")
    g.add_argument("--verbose", "-v", action="store_true")
    i = sub.add_parser("ingest", help="真实 DDL 回流")
    i.add_argument("--dir", required=True, help="真实 SQL 目录")
    i.add_argument("--out", default=str(_SCRIPT_DIR.parent / "eval"))
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out)
    if args.cmd == "generate":
        generated, rejected, detail = generate(out_dir, args.dry_run, args.verbose)
    else:
        generated, rejected, detail = ingest(
            Path(args.dir), out_dir, args.dry_run, args.verbose)
    print(f"\n生成 {generated} 个 case，门禁拒绝 {rejected} 个")
    if detail and args.verbose:
        print("\n拒绝明细（模板副作用或标注失配，需修模板）：")
        for d in detail:
            print(d)
    return 0 if rejected == 0 else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

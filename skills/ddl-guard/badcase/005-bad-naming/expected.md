# ddl-guard badcase 005 — 命名不规范

check: ddl_check.py + 人工判断（ddl-manual-rules.md）

## 违规语句

### 表 1: t_danpai_jixiao
| 问题 | 说明 |
|---|---|
| 表名使用拼音 | `danpai_jixiao` 是拼音，应改为英文 `single_rank_effect` |
| 字段名使用拼音 | `chanpin_mingcheng` 是拼音，应改为英文 `product_name` |
| 字段名使用拼音 | `jiage` 是拼音，应改为英文 `price` |

### 表 2: t_user_orders
| 问题 | 说明 |
|---|---|
| 表名使用名词复数 | `user_orders` 使用了复数形式，应改为 `user_order` |

### 表 3: t_order_business_rel
| 问题 | 说明 |
|---|---|
| 表名使用泛化词 | `business_rel` 含「关联」「业务」等无信息量词汇，应改为更精确的名称如 `order_business_mapping` |
| 字段名使用泛化词 | `business_type` 含「业务」，`associate_id` 含「关联」，应改为更精确的名称 |

### 表 4: t_product_info
| 问题 | 说明 |
|---|---|
| 字段名使用拼音 | `chanpin_mingcheng` 是拼音，应改为 `product_name` |
| 字段名使用拼音 | `jiage` 是拼音，应改为 `price` |

### 表 5: t_order_detail
| 问题 | 说明 |
|---|---|
| 字段名使用泛化词 | `business_type` 含「业务」，`associate_id` 含「关联」，应改为更精确的名称 |

### 表 6: t_config
| 问题 | 说明 |
|---|---|
| 字段名含义不清 | `field1`, `field2`, `data1`, `data2` 含义不清，必须有明确正常用途 |

### 表 7: t_order_process_log
| 问题 | 说明 |
|---|---|
| 表名围绕非核心主体 | `order_process_log` 围绕「处理日志」设计，核心主体应为 `order`，建议改为 `order_log` 或 `order_event` |

## 人工判断规则（脚本无法覆盖）

根据 `ddl-manual-rules.md`，以下规则需人工逐表核对：

1. **属性级别命名**：字段名细化到属性级别，不得用主体名当属性名
2. **内容视角命名**：以存储内容含义命名，而非业务功能/逻辑视角
3. **泛化词**：避免「关联」「业务」等无信息量词汇
4. **方/人区分**：专指人用 `er`（中文名「xx人」），含机构用 `pty`（中文名「xx方」）
5. **拼音**：表名/字段名使用英文单词/缩写，禁止拼音或拼音缩写
6. **表名主体**：表名围绕核心主体设计

## 预期检查输出

本例违规均为命名语义类（结构规范本例无违规）。

- 脚本自动检出：禁用类型、缩写未规范化、必含字段注释不一致
- 人工补充：命名语义（拼音、泛化词、复数、核心主体）
- 人工补充规则：拼音、泛化词、复数形式|复数、表名主体|核心主体、内容视角命名|存储内容含义|业务功能视角|含义不清
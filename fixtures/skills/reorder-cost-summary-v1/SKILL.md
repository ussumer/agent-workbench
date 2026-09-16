---
name: reorder-cost-summary
description: 把库存预警与已抓取的供应商报价汇总成补货数量、金额合计和 Markdown 表格。当用户要求补齐库存并估算采购金额时使用。
---

# 补货成本汇总

把「哪些物料需要补货」与「向哪家供应商以什么价格补货」两份事实合成为一张可核对的表，
并给出两位小数的金额合计。本技能**只做汇总**，不发起任何订单写操作。

## 输入

一个 JSON 文件，结构如下：

```json
{
  "currency": "CNY",
  "lines": [
    {
      "part_id": "P001",
      "supplier_id": "S002",
      "quantity": 42,
      "unit_price": "24.00",
      "currency": "CNY",
      "source_url": "http://localhost:8088/suppliers/S002/quotes"
    }
  ]
}
```

| 字段 | 约束 |
|---|---|
| `part_id` | `P` + 3 位数字 |
| `supplier_id` | `S` + 3 位数字 |
| `quantity` | 整数，1..10000 |
| `unit_price` | 两位小数金额**字符串**，0.01..999999.99 |
| `currency` | 目前只支持 `CNY` |
| `source_url` | 报价来源地址，必须保留以便追溯 |

## 输出

写入 `--out-dir`：

- `summary.json`：`lines`（含每行 `amount`）、`total_amount`、`currency`、`source_urls`
- `report.md`：Markdown 表格，含每行金额与合计

金额一律两位小数字符串，使用十进制运算，不做浮点近似，也不四舍五入弥补错误输入。

## 失败条件

出现以下任一情况即返回非零退出码，并在 stderr 说明原因，**不产出半成品结果**：

- 输入不是合法 JSON，或缺少 `lines`
- `quantity` 不是整数，或超出 1..10000
- `unit_price` 不是两位小数金额字符串
- 同一行的 `currency` 与顶层 `currency` 不一致（当前没有汇率服务，不做换算）
- `lines` 为空

## 示例

```bash
python scripts/summarise.py --input examples/input.json --out-dir out
```

`examples/input.json`（P001 42×24.00 + P004 30×17.50）的合计必须是 `1533.00`。

---
name: supplier-price-urls
description: 供应商 ID 到演示报价页 URL 的映射。需要知道某家供应商的报价从哪里抓取时使用。
---

# 供应商报价地址

演示环境的供应商与报价页对应关系。这是**数据**，不要向用户描述成真实商业数据。

## 映射

| supplier_id | 名称 | 报价页 |
|---|---|---|
| S001 | 华东金属 | `http://localhost:8088/suppliers/S001/quotes` |
| S002 | 南方五金 | `http://localhost:8088/suppliers/S002/quotes` |
| S003 | 北方配件（已停用） | `http://localhost:8088/suppliers/S003/quotes` |
| S004 | 中原电子 | `http://localhost:8088/suppliers/S004/quotes` |

沙箱内访问宿主时把主机名换成 `host.docker.internal`，端口不变。
服务对外地址以 `FIXTURES_BASE_URL` 配置为准，上表是本地默认值。

## 使用步骤

1. 用 `supplier_id` 查出 URL；查不到就报「未知供应商」，**不要**猜一个相近的 ID。
2. 按 `web-scraper` 的规则抓取并解析。
3. 在结论里保留实际使用的 URL，便于追溯。

## 输入

一个或多个 `supplier_id`。

## 输出

`supplier_id → url` 的映射，以及未能解析的 ID 列表。

## 失败条件

- ID 不在表中 —— 列为未知，不要回落到其它供应商。
- 页面返回非 200 —— 报该供应商不可用，保留 URL 与状态码。
- **S003 已停用**：它不参与补货比价，也不能下单。比价时用
  `build_report.py --skip-supplier S003` 显式排除，并在结论里说明排除了哪一家——
  把停用供应商算进"最低价"会给出一个无法执行的推荐。
- 某个活跃供应商抓取失败 —— 不要静默跳过。少一家参与比价时，
  剩下几家算出的"最低价"可能已经不是最低价，必须向用户点明缺了谁。

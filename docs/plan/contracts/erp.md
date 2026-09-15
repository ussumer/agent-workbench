# Java ERP 契约 v1

本文件定义补齐的业务系统，不声称还原讲师未知源码。基础路径 `/api/erp/v1`。JSON 字段统一 snake_case；UTF-8；时间 ISO 8601 UTC。ID 为字符串，金额用两位小数字符串，数量为正整数。

## 对象和约束

| 对象 | 字段 | 约束 |
|---|---|---|
| Supplier | supplier_id、name、active、contact | supplier_id 唯一；停用供应商不可新下单 |
| Part | part_id、sku、name、unit、active | sku 唯一；本 Demo 单位 piece，停用物料不可下单 |
| SupplierPart | supplier_id、part_id、catalog_price、currency、lead_days | 复合唯一；只允许供应商提供的物料；currency=CNY |
| Inventory | part_id、on_hand、warning_threshold、target_stock | 均为非负整数；target_stock >= warning_threshold |
| Order | order_id、order_no、owner_user_id、supplier_id、currency、total_amount、version、created_at、updated_at、note | owner 来自可信服务头；版本从 1 起；金额服务端计算 |
| OrderLine | order_id、line_no、part_id、quantity、unit_price、line_amount | 一单最多 20 行；同物料不重复；数量 1..10000；单价 0.01..999999.99 |
| Operation | owner_user_id、operation_id、payload_hash、operation_type、resource_id、response_json | owner+operation_id 唯一；和订单同事务提交 |

不创建 pending/approved 订单状态：审批是 Agent 工具执行前的待办，未批准时 ERP 不应有订单。已创建订单只支持查询和经再次审批后的修改；本轮不定义收货、取消、付款状态。

数据库外键、唯一键、非负检查和应用层校验同时落实。Java BigDecimal 计算；不从模型接收 total_amount。拒绝超过两位小数，不做隐式四舍五入。订单创建/修改不改变 Inventory。

## 固定 seed-v1

全量存于 `fixtures/seed-v1.json`，Java 初始化和测试预期都引用这个文件。先采用下表最小集，可新增但不能改写固定基线。

| 供应商 | 名称 | 活跃 |
|---|---|---|
| S001 | 华东配件 | true |
| S002 | 远航配件 | true |
| S003 | 停业示例供应商 | false |

| 物料 | SKU / 名称 | 库存 / 预警 / 目标 | S001 目录价 | S002 目录价 |
|---|---|---|---|---|
| P001 | BRAKE-01 / 刹车片 | 8 / 20 / 50 | 25.50 | 24.00 |
| P002 | FILTER-01 / 空气滤芯 | 40 / 15 / 50 | 12.00 | 13.00 |
| P003 | CHAIN-01 / 传动链条 | 5 / 10 / 20 | 68.00 | 无供货关系 |
| P004 | SPARK-01 / 火花塞 | 0 / 10 / 30 | 18.00 | 17.50 |

再加停用 P005 和 S003-P001 关系用于拒绝用例。库存预警严格 `on_hand < warning_threshold`；建议补货 `max(target_stock-on_hand,0)`。预警精确集合 P001/P003/P004，建议数量 42/15/30。

基础订单集为空；演示创建 P001×50×25.50，总额 `1275.00`；修改为 60 件，总额 `1530.00`。多行测试 P001×2×25.50 + P002×3×12.00，总额 `87.00`。

## 通用 HTTP

成功：`{"data": ..., "request_id":"..."}`。集合 data 为 `{"items":[],"total":0,"page":1,"page_size":20}`。page>=1，page_size=1..100，排序固定 ID 升序；订单 created_at 降序、order_id 作为次序。

失败：`{"error":{"code":"PART_NOT_FOUND","message":"...","details":{}},"request_id":"..."}`。

| 状态 | code 示例 | 客户端行为 |
|---|---|---|
| 400 | INVALID_ARGUMENT | 修正输入，不盲目重试 |
| 401/403 | UNAUTHORIZED / FORBIDDEN | 停止调用，不让模型换身份 |
| 404 | PART_NOT_FOUND / ORDER_NOT_FOUND | 告知不存在；其他用户订单同样 404 |
| 409 | VERSION_CONFLICT / IDEMPOTENCY_CONFLICT | 读最新版本或报告冲突；不能自动复用旧审批 |
| 422 | INACTIVE_SUPPLIER / UNSUPPORTED_PART | 业务不允许 |
| 503 | DATABASE_UNAVAILABLE | 显示依赖失败 |

## 端点

| 方法和相对路径 | 输入 | 输出 |
|---|---|---|
| GET /suppliers | q?、active?、page、page_size | Supplier 分页 |
| GET /parts/{part_id} | 路径 ID | Part 和可用供应商列表 |
| GET /parts | q?、page、page_size | 物料分页，名称/SKU 子串搜索 |
| GET /suppliers/{supplier_id}/parts | page、page_size | Part + SupplierPart 分页 |
| GET /inventory/warnings | page、page_size | 物料、库存字段、suggested_quantity |
| GET /orders | order_id?、supplier_id?、page、page_size | 当前 owner 的订单，含 lines |
| POST /orders | 下方创建请求 | 201 完整 Order |
| PUT /orders/{order_id} | 下方修改请求 | 200 完整 Order |

创建请求：

```json
{"supplier_id":"S001","currency":"CNY","lines":[{"part_id":"P001","quantity":50,"unit_price":"25.50"}],"note":"演示采购"}
```

修改为整单替换，禁止模糊 patch：

```json
{"expected_version":1,"supplier_id":"S001","currency":"CNY","lines":[{"part_id":"P001","quantity":60,"unit_price":"25.50"}],"note":"调整数量"}
```

修改需显示 before/after 并重新审批。提交的单价可以来自新抓取报价，不要求等于目录价，但必须显示在审批中；当前不做自动价格谈判或税费/运费计算。

## 事务和重放

网关通过内部 service token、`X-Actor-Id`、`X-Operation-Id` 调 Java；不暴露给浏览器或沙箱。Java 信任已验证的服务身份，不信任请求体 owner。

1. 校验 token、actor、结构和业务关系。
2. payload_hash 为 HTTP 请求体原始 UTF-8 字节的 SHA-256；网关从冻结的审批 payload_bytes 发送，重试字节不变。请求类型和目标资源另存并比较。
3. 在单个事务中占有 Operation 唯一键、创建/修改订单和行、保存成功响应。并发同 key 冲突者事务回滚后读取已提交记录；未提交结果返回可重试冲突，不能再执行一次业务写。
4. 同操作 ID、同 actor、相同类型/目标/hash 返回原成功结果，标记响应头 `Idempotent-Replayed: true`。同 ID 不同内容返回 409。
5. 修改用 `WHERE owner_user_id=? AND order_id=? AND version=?` 条件更新并加一；更新 0 行时区分不存在与版本冲突。
6. 响应丢失后用原 operation_id 重发，不能生成新 key。批准相同内容的下一次独立采购是新 operation_id，可以创建新订单。

## 重置和持久化

seed 初始化仅在空库执行。显式 reset 命令只处理项目专用 Demo 数据库并要求 `--confirm-demo-reset`；不得启动时清库。reset 先停运行，清业务订单/操作记录并恢复 seed，Agent/Mongo 演示数据同步重置以免旧审批作用到新库。配置独立测试库，严禁测试清理开发库。

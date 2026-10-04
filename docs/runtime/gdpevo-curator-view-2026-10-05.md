# Curator 训练视图修复（T60）

已修复训练材料丢失原题、scoped压缩丢失详细反馈的问题。v2训练包保留公开task、全部尝试、完整feedback（diagnostics、arithmetic_audit、scope_note等），重复政策只保留一份并用rule_ids关联。Actor可见共同system/environment来自真实请求；grade及最终诊断标为Curator侧资料，修正Actor只获得前一尝试反馈。不提供test输出或私有答案。

| 组 | 原题 | 真实尝试 | 完整请求bytes |
| --- | --- | --- | --- |
| quotes | 5 | 6 | 45065 |
| packages | 5 | 10 | 59168 |
| kits | 5 | 10 | 61716 |
| revisions | 5 | 9 | 51282 |

产物：[curator-inputs](../../artifacts/tasks/T60/20261004T184707Z/curator-inputs/report.json)，四个request.json与manifest同目录。prepare没有模型调用，不生成技能或修改生产指针。这是训练输入修复，尚未测量学习收益。

来源核对：已完成闭环BASE的training-records、training材料和repair请求均核对其已有manifest。最初train实验在Curator失败时中止，没有总manifest；保留这一缺口，原始request依据当时真实网关记录的wire SHA256核对（包括thinking disabled），初次响应文本与已冻结BASE记录比对。修正请求的原始输入、上一输出和反馈与对应材料逐项一致。没有补造历史manifest。

版本边界：`scoped_curator_input`默认v2；历史T58 verifier显式用v1重建当时实际输入。原请求、旧技能和评分不改写；v1信息不足的结果没有变成v2结果。未知版本、test、重复train、缺原题、原始request被篡改均拒绝。

验收：[receipt](../../artifacts/tasks/T60/20261004T184707Z/receipt.json)，25项unit/受影响协议检查通过，一次真实历史材料prepare通过。首轮`artifacts/tasks/T60/20261004T184535Z/receipt.json`为failed：错误假设最初中止的实验存在总manifest；后续改用实际逐调用wire证据，失败记录保留。新版包manifest已逐文件核对。

下一主线：保持本次修复，建立v3同题OpenSandbox计算能力诊断与操作级技能演化。先区分完整规则/正确训练答案条件下的执行能力，再决定演化轮数；不重跑旧T59、不用旧开发test倒选或生成技能、不恢复T38/T51。整体“开修!记得git好”目标未完成，生产订单合同仍缺v3 adapter。

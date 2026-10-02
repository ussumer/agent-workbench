---
name: skill-management
description: 创建或下载、校验、发布并分配技能包的完整流程。当用户想新增一个技能、安装资源站上的技能包，或调整某个技能适用的 Agent 范围时使用。
---

# 技能管理

把一个技能从「来源」推进到「可被某个 Agent 使用」，中途任何一步失败都不得发布。
完整状态机是 `draft -> validating -> validated -> persisted -> assigned`，
失败分别进入 `validation_failed` / `persistence_failed`，恢复失败独立为 `restore_failed`。

## 何时使用

- 用户要求新增、修改、安装技能
- 用户要求改变某个技能适用的范围（main / procurement-analyst / procurement-order）
- 需要核对某个技能当前发布的版本与来源

## 步骤

1. **取来源**
   - `source_type=generated`：在**当前 owner 的沙箱** staging 目录里由模型生成文件。
   - `source_type=package`：从资源站下载已批准的 ZIP 到 staging 目录。
   - 两种来源都记录：来源标识、ZIP 或目录的 SHA-256、创建时间。
2. **校验**（对应状态 `validating`）
   - 压缩包 ≤ 2 MiB，展开 ≤ 10 MiB，文件数 ≤ 100。
   - 拒绝符号链接与路径穿越（任何 `..` 或绝对路径）。
   - 必须有 `SKILL.md`，frontmatter 至少含 `name`、`description`；slug 符合
     `^[a-z0-9]+(-[a-z0-9]+)*$` 且长度 ≤ 64。
   - 若声明了脚本入口，该文件必须存在。
3. **沙箱内冒烟**（仍在 `validating`）
   - 用固定输入跑一次，再用错误输入跑一次，记录 exit code、stdout/stderr、输出文件 hash。
   - 冒烟失败把错误反馈给模型修复，**最多 2 次**，每次留记录；仍失败则**不发布**。
     失败**不会中断对话**：退出码与输出在工具返回的 `error.details` 里，你改完文件
     **再调一次 `assign_skill`** 就是下一次修复。没改文件就重试不会有不同结果。

   **脚本约定**（生成技能时要遵守，否则冒烟跑不起来）：
   - 入口写 `scripts/xxx.py`，并在 SKILL.md 里出现这个路径——工具按它定位入口。
   - 命令行接受 `--input <JSON 文件>` 与 `--out-dir <输出目录>`。
   - **正常输入退出码为 0，坏输入必须非 0**。对无效输入返回 0 会被判定为冒烟失败：
     一个把乱数据当有效数据算下去的脚本，比一个在示例上就崩的脚本更危险。
   - 产物（至少一个文件）写进 `--out-dir`；如果产物里是补货汇总，
     带 `total_amount` 字段，工具会把它的值记进冒烟记录供核对。
4. **持久化**（对应状态 `persisted`）
   - 由服务端分配不可变 `version`，写入完整文件内容与校验和。
   - 全部写完必须**读回校验**，通过后才允许把 manifest 标为完整。
5. **分配**（对应状态 `assigned`）
   - 条件更新 `owner + scope + slug` 的当前版本指针（MongoDB 条件更新，不是先读后写）。
   - 只有 manifest 完整的版本可被发现；半写版本不是有效技能，会被清理。
6. **同步执行副本**
   - 把该版本复制到沙箱 `/skills/users/{scope}/{slug}/`，并更新 `skills_revision`。
   - 框架按 revision 在下一次调用刷新；**不承诺当前 graph run 内即时发现**。
   - 恢复时会逐文件核对 manifest 里的 sha256；对不上的版本**不会被写入**，
     沙箱里已有的副本也保留不动。

## 输入

`assign_skill(source_type, source, slug, target_scope)`

| 参数 | 约束 |
|---|---|
| `source_type` | `generated` 或 `package` |
| `source` | 沙箱内生成目录，或资源站上已批准的 URL |
| `slug` | 见上面的 slug 规则 |
| `target_scope` | `main` / `procurement-analyst` / `procurement-order` |

## 输出

`skill_id`、`version`、`status`、`validation_artifact_id`。

## 失败条件

- `target_scope` 未知 —— **拒绝**，不要退回成「全 Agent 可用」。
- 未指定 `target_scope` —— 通过补充信息请求询问用户，**不得默认为所有 Agent**。
- 校验或冒烟失败且修复 2 次仍失败 —— 不发布，返回失败原因与已留存的记录。
- 相同 owner/scope/slug 且内容相同 —— 返回**原版本**，不新建版本。
- 内容有变化 —— 生成新版本，旧版本保留。

## 注意

- 删除某个 scope 的 assignment 不影响其它 scope。
- 清理未引用的 staging 与旧副本不影响当前版本。
- 不要把任何真实 API 密钥写进技能文件。

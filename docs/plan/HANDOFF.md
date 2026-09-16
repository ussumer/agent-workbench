# 当前交接

## 状态

- 2026-09-15：两轮访谈十项选择已确认。
- 2026-09-16：方案 v1.0 成稿并整体审阅通过。
- 2026-09-16：**T00 完成（gate passed）**，T01-T24 仍 pending。仓库已是 Git 仓库。

## T00 完成情况

- 任务：T00 建立工程、依赖锁与验收入口（R01）。
- 结论：`python scripts/gate.py T00` 返回 0，三个 required check 全部 passed。
- receipt：`artifacts/tasks/T00/20260916T003019Z/receipt.json`
  （acceptance 24 tests / 0 failed / 0 skipped；java-build 与 frontend-build exit=0）。
- 依赖组合写入 `docs/runtime/versions.md`，状态为 **resolved**（T06 实测后才可标 verified）。

### 本包改动（关键阅读入口）

| 文件 | 作用 |
|---|---|
| `scripts/gate.py` | 任务 gate：参数数组执行、超时、JUnit 解析、attempt 目录、receipt 与证据 hash、缺配置阻塞 |
| `scripts/lib/config.py` | 密钥脱敏、能力检查、JDK/Node 路径解析与 PATH 组装 |
| `scripts/doctor.py` | 环境诊断，只打印配置项名称，不打印值 |
| `scripts/gate_capabilities.json` | 需要凭据的任务能力映射（T06/T15/T23） |
| `tests/acceptance/test_t00.py` | gate 自身的正/负向验收（临时仓库驱动，不递归调用本仓库 gate） |
| `tests/conftest.py` | 仅容忍本机 pytest 临时目录 symlink 收尾错误 |
| `pyproject.toml`、`uv.lock` | Python 依赖与锁（107 包） |
| `erp/pom.xml`、`erp/mvnw(.cmd)`、`.mvn/wrapper/` | Java 21 + Spring Boot 3.5.6 骨架，Maven 3.9.9 已固定 SHA-256 |
| `frontend/` | Vue 3 + TS + Vite 骨架，`npm run build` 通过 |
| `infra/compose.yml` | 端口/网络/持久卷单一配置；MongoDB 7.0.43 按 digest 固定 |
| `.env.example` | 配置项模板，不含真实凭据 |

### 环境要点（本机，不是方案变更）

- `JAVA_HOME` 原值指向文件而非目录；脚本按 `bin/javac` 重新解析到 `D:\Android_Studio\jbr`。
- `.venv` 必须用具体解释器路径创建（uv 的 3.12 junction 在本机触发 WinError 448）。
- 前端用 node v24.13.0（Vite 8 要求 `^20.19.0`）；nvm 活动链接为空，脚本自动解析版本目录。
- `./mvnw` 在 Windows 解析为 `mvnw.cmd`；receipt 记录 manifest 原 argv，实际命令见 `executed_argv` 与日志首行。

详见 `docs/runtime/versions.md` 的“环境说明”。

### 尚未验证 / 已知空缺

- 所有版本仅 **resolved**：未与真实模型、Mongo、OpenSandbox、智谱、ModelScope 联通。
- 全部 7 项能力当前为 MISSING（无 `.env`）；T06/T15/T23 在补齐凭据前会返回 blocked。
- Java/Vue 只有最小健康检查，业务能力从 T02/T14 开始；不得把骨架当作业务能力。
- `lucide-vue-next` 已被 npm 标记 deprecated（建议 `@lucide/vue`）；按方案保留，替换需先讨论。

## 下一步

按编号继续，下一条命令：

```bash
python scripts/plan_guard.py packet T01
```

T01（固定业务种子与契约测试数据，R02）依赖 T00 已完成。之后 T02、T03 依次推进；
T06（真实模型）与 T07（Mongo）依赖 T00，可在缺少凭据时返回 blocked 并继续其他链。

## 后续交接模板

每次任务完成/暂停时替换当前现场，保留必要历史链接：当前 task/status；已完成步骤；文件入口；
真实检查命令和 receipt；未过断言及根因；服务 PID/端口或容器；下一条命令；是否需用户配置/决策。
不要填“基本完成”而缺少证据。

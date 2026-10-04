# Part 05 状态持久化与沙箱恢复

## 学习目标

区分各类数据的事实源，解释为什么换沙箱容器可以恢复技能，却不能保证临时文件仍在。

## 机制解释

同一项目使用多个存储不是为了堆技术：ERP保存订单业务事实；Mongo checkpointer保存图状态和中断位置；LangGraph Store保存偏好和技能文件；应用Mongo集合保存展示消息、会话归属、运行及审批记录。后端实例、运行队列和容器内文件又有自己的生命周期。

| 数据 | 主要事实源 | 不应混淆 |
|---|---|---|
| 订单和操作结果 | ERP数据库 | 模型回复不能证明订单成功 |
| 图消息与中断位置 | checkpointer | 展示历史不能直接充当恢复checkpoint |
| 偏好和技能持久文件 | Store | 沙箱内技能是可恢复执行副本 |
| 展示消息和run状态 | 应用Mongo集合 | 内存registry只管理当前进程活动任务 |
| 临时执行文件 | 沙箱容器 | 换容器可能丢失，不能冒称已恢复 |

用户身份由服务端请求上下文建立，模型参数不决定owner。UserScopedStore检查namespace和key，沙箱manager按owner选择稳定proxy，下载也按artifact归属控制。固定demo账号只演示隔离，不是生产认证系统。

稳定proxy的意义是图持有的句柄不变，内部backend可以替换。容器故障时manager重建、准备工作区、恢复持久技能，再更新proxy；缓存图不需要把旧backend绑给另一用户。相关生命周期锁处理同owner恢复竞争，数据库登记用于重连。当前是单worker Demo，不能直接宣称多实例分布式租约完备。

容器执行提供隔离，不能因为沙箱不可用就退回宿主运行模型生成的脚本。执行隔离也不是所有安全问题的自动答案；这里重点理解代码路由和归属。

## 代码阅读

[web_config.py](../../src/api_view/web_config.py)：看MongoResources分别构建哪些资源。[repository.py](../../src/agent/persistence/repository.py)：看thread/run/display message。再看 [scoped_store.py](../../src/agent/persistence/scoped_store.py) 的_guard，以及 [namespaces.py](../../src/agent/persistence/namespaces.py) 的用户布局。

沙箱部分先读 [sandbox_proxy.py](../../src/agent/backends/sandbox_proxy.py) 的replace_backend与execute，再读 [sandbox_manager.py](../../src/agent/backends/sandbox_manager.py) 的get_or_create、ensure_healthy和recover；最后读 [user_skills_restore.py](../../src/agent/middlewares/user_skills_restore.py)。暂时跳过SDK网络细节和容器池完整维护算法。

## 具体案例

容器A被删除。manager为同owner建立容器B，从Store恢复已持久化技能。原图仍调用同一个proxy，后续执行进入B。A里没有持久化的临时HTML不会自动回来；展示历史仍在Mongo。另一个owner不能读取你的偏好和产件。

证据读 [test_recovery.py](../../tests/integration/test_recovery.py)、[test_isolation.py](../../tests/integration/test_isolation.py)、[T33](../../tests/acceptance/test_t33.py)。不同数据库warm池标记隔离是后续修正，不能推广成所有多实例清理都已解决。

## 自检

1. 删掉容器，哪些内容能恢复，哪些不能保证？
2. proxy稳定与容器稳定有什么区别？
3. checkpointer、Store、应用集合为什么不是一个概念？
4. 用户隔离靠提示词还是服务端作用域检查？

核对要点：持久化技能与偏好可读回，临时文件不保证；句柄与内部backend分离；图续跑、长期数据、产品展示不同职责；代码控制owner边界。

## 面试表达

“我们区分业务事实、图状态和长期记忆。沙箱只是执行环境，持久技能在Store中；稳定proxy允许容器替换后继续使用原句柄，并按用户作用域恢复副本。”

## 确认

画出四种状态各自的存储位置，并描述一次容器重建。能够指出一个不能承诺恢复的东西，再确认本章。

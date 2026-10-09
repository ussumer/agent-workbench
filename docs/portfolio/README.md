# 实习作品集初稿

这组稿件服务于面试演示和简历，不是项目技术报告。数字只引用已经留有请求、响应、评分器和源码身份的运行；没有把组件测试写成模型能力，也没有把当前结果写成生产学习收益。

## 文件

- [主项目演示稿](main-harness-demo.md)：8–10 分钟，展示困难采购任务上的 baseline、反馈、技能候选、验证和冻结测试。
- [真实证据回放 Demo](evidence-replay.html)：可切换 fixed / skills / dynamic，查看同一题的输入、Actor 输出、judge、bank 和 computation trace。
- [副项目演示稿](course-backend-demo.md)：5 分钟，展示优惠券发放的 Redis、MQ、MySQL 幂等链路和故障恢复。
- [简历与口述稿](resume-and-talk.md)：简历项目条目、30 秒介绍、3 分钟展开和常见追问。

## 使用口径

主项目当前最强的可引用结果来自 T64 compute attempt：同一 v3 困难采购任务集，20 train、20 validation、20 held-out test，每个 test arm 重复 3 次。dynamic 的 held-out 均分为 0.888462，fixed 为 0.758974；skills 为 0.876923。它说明冻结后的技能和选择机制在这个任务集上出现了明显的总体提升信号。它不证明统计显著性，也不代表已经改变生产 assignment。

副项目当前最适合展示优惠券链路。课程、学习、积分、交易和支付模块可以作为代码范围说明，现场不要把没有做端到端演练的模块说成已经验收。

## Demo 运行

在仓库根目录执行 `python3 -m http.server 8765 --directory docs/portfolio`，然后打开 `http://localhost:8765/evidence-replay.html`。页面是只读历史回放，不发起模型调用；数据快照由 T64 attempt 生成，见 `data/t64-replay.json`。

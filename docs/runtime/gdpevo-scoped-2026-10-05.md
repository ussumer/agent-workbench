# GDPevo v3 按业务组拆分技能对照

复用T57 train首次输出/一次失败修正与fixed控制。quotes/packages/kits/revisions四个真实Curator各读取本组五train规则和原始尝试，训练技能只服务对应业务组，未安装生产bank。

|测量|fixed|scoped|
|---|---|---|
|train全对|4/20|8/20|
|train均分|0.796154|0.853846|
|开发test全对|0/20|1/20|
|开发test均分|0.607692|0.761538|

train两题退化，select_candidate拒绝晋升；test不参与选择。44新调用，本轮保守2.123559元，累计36.926127/50元，actual cost未知。真实证据 `/mnt/c/dev/rsi-eval/procurement_eval/runs/planning-session-20261005/attempt-gdpevo-v3-scoped-20261005`；gate `artifacts/tasks/T58/20261004T170438Z/receipt.json`。

仅一次无工具JSON评估、静态按group加载；未证明统计显著、动态技能编排、OpenSandbox v3运行或生产ERP成功。后续须实现多步工具Actor并补三重复和监督条件对照。原旧失败、候选和fixed源码不覆盖。

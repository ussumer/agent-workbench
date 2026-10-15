# T67 cont4 零调用端口预检失败与修复

cont4实际源码d7c7dfb，run正常退出failed。真实doctor已READY，Java/MCP/OpenSandbox/Mongo/隔离/清理通过；正式run启动控制环境时裸socket.bind报EADDRINUSE，未进入技能生成，0skills/0rows/0modelcalls，新增模型费用0，gateway已排空且pending0。独立cont4-failed-audit.json核对原6份run manifest哈希；原result/manifest均不改。

事后ss无listener、Docker无该端口发布；原doctor控制容器与两网络清理通过。真实TCP复现：服务端主动关闭形成TIME_WAIT，裸bind失败，与现场时序一致；SO_REUSEADDR后可探测空闲，活跃listener（也设置SO_REUSEADDR）仍被拒。源码仅在preflight设置SO_REUSEADDR，不使用SO_REUSEPORT、不杀监听者。control-port-red.xml真实1failed1passed；green完整控制组件12passed0failed0skipped。独立定点review无重要发现，结论限定实际Linux/WSL。

真实锁定控制镜像同18125端口连续两次启动/健康/清理通过：scratch/runtime/control-port-live1/result.json，modelcalls0。此为有新证据的单次修复；没有重复启动原cont4，没有后台循环，旧失败保留。下一次独立cont5按原3fresh+30完整协议及新freeze继续；若同端口或同类故障再现，立刻停下讨论，不继续补丁重跑。T68不恢复。

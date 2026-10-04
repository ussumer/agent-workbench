# 自进化论文：本地原文

三篇论文按已接受实施规格引用的版本固定保存。PDF 为下载原文，HTML 为 arXiv 原始网页，TXT 从 PDF 提取，未翻译或改写论文内容。不声明这些固定版本是最新版本。

| 论文 | 固定版本 | 本地 PDF | 可检索全文 |
| --- | --- | --- | --- |
| Prime Agent: A Self-Improving RLM Harness | 2608.23552v1 | [PDF](prime-agent/2608.23552v1.pdf) | [TXT](prime-agent/2608.23552v1.txt) |
| TRACE: A Self-Evolving Skill Bank for Consistent, Limit-Aware LLM Agents | 2608.22793v2 | [PDF](trace/2608.22793v2.pdf) | [TXT](trace/2608.22793v2.txt) |
| GDPevo: Evaluating Agent Self-Evolution on Real Business Tasks | 2608.03764v1 | [PDF](gdpevo/2608.03764v1.pdf) | [TXT](gdpevo/2608.03764v1.txt) |

## 阅读入口

- Prime Agent：§2.2 状态层级、§2.3 RLM/REPL、§2.4 递归通信、§2.5 Continual Harness、§2.6 恢复与资源核算；§3.5 包括持久交互和策略更新案例。
- TRACE：§2.1 初始技能库抽象、§2.2 轨迹对照演化与 Algorithm 1、§2.3 逐轮技能编排与 Algorithm 2；之后阅读实验与一致性指标。
- GDPevo：§3.3 规则组合设计、§3.4 校准与独立复核、§4.1 监督条件和评测协议；附录 F/G 讨论迁移案例、信息隔离与评分。

上述入口是阅读导航，不能当作已完整复现论文的证明。项目接入范围见 [实施规格](../../runtime/self-evolution-spec-2026-10-03.md)，阅读与核查边界见 [研究记录](../../runtime/self-evolution-research-2026-10-02.md)。

## 文件与来源校验

[manifest.json](manifest.json) 记录原始下载地址、获取时间、页数、文件大小和 SHA256。已校验 PDF 文件头、PDF 可解析及非空文字提取。

TXT 使用 `pdftotext -layout -enc UTF-8` 提取，公式、表格和阅读顺序可能失真，引用时以 PDF 为准。HTML 保留远程资源引用，没有打包图片、脚本和样式；完整离线阅读优先使用 PDF。

本地 HTML：[Prime](prime-agent/2608.23552v1.html)、[TRACE](trace/2608.22793v2.html)、[GDPevo](gdpevo/2608.03764v1.html)。

原文来源：[Prime arXiv](https://arxiv.org/abs/2608.23552v1)、[TRACE arXiv](https://arxiv.org/abs/2608.22793v2)、[GDPevo arXiv](https://arxiv.org/abs/2608.03764v1)。作者和许可按原文保留。

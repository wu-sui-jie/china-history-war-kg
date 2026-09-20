# docs/changes —— 变更与审核过程记录

**定位：历史过程记录，不是现行事实源。** 这里放的是各阶段的变更总结、全项目审核报告、
整改工作单与复核裁定，用来回答"某个决定/改动当时是怎么来的、依据是什么"。

日常开发**不需要读**这里的任何文档。需要查现行事实（版本、测试数、门禁状态、数据计数）时看
[current-status.md](../current-status.md)；需要看功能怎么用、契约是什么时看
[README.md](../README.md)、[data-contract.md](../data-contract.md) 与 [features/](../features/)。
这里只在**审计追溯、复盘"为什么这么改"、或接手历史遗留项**时才用。

阅读顺序：同轮次内先「审核/工作单」再「整改/复核」，每份文档首部的日期与"文档类型"行会说明它的性质。

## 一、RAGv2–v5 阶段变更（2026-09-04 ~ 09-13）

| 文档 | 类型 | 内容 |
| --- | --- | --- |
| [20260904-ragv2-review-fix-requirements.md](20260904-ragv2-review-fix-requirements.md) | 需求分析 | RAGv2 第三方复核修订的需求口径 |
| [20260904-ragv2-review-fix-summary.md](20260904-ragv2-review-fix-summary.md) | 变更总结 | RAGv2 第三方复核修订的落地结果 |
| [20260904-ragv3-requirements.md](20260904-ragv3-requirements.md) | 需求分析 | RAGv3（前端 F01/F07）的需求 |
| [20260904-ragv3-summary.md](20260904-ragv3-summary.md) | 变更总结 | RAGv3 前端实现结论 |
| [20260904-ragv4-summary.md](20260904-ragv4-summary.md) | 变更总结 | RAGv4（F10 评测）落地 |
| [20260904-extra-review-fix-summary.md](20260904-extra-review-fix-summary.md) | 复核整改 | 2026-09-04 第二轮外部复核的修复 |
| [20260913-ragv4-review-summary.md](20260913-ragv4-review-summary.md) | 评审总结 | 题库审核 + 答案评分 + 可复现性修复 |
| [20260913-ragv5-summary.md](20260913-ragv5-summary.md) | 变更总结 | RAGv5（F08 演示 + 真实模型/向量 + 部署）落地 |

阶段本身的说明在 [../RAG_v1/](../RAG_v1/)（开发说明、规划说明、阶段总结），与本目录互补：
`RAG_v1/` 讲"这个阶段做了什么、怎么用"，`changes/` 讲"这一轮改了什么、依据是什么"。

## 二、第四轮全项目审核链路（2026-09-15）

| 文档 | 类型 | 内容 |
| --- | --- | --- |
| [20260915-round1-full-audit-report.md](20260915-round1-full-audit-report.md) | 审核工作单 | 第一轮全项目审核：11 个模块 9 个属实、2 个有缺口；22 条问题 |
| [20260915-full-audit-fix-summary.md](20260915-full-audit-fix-summary.md) | 整改总结 | 第一轮问题的改动清单与理由 |
| [20260915-round2-audit-recheck.md](20260915-round2-audit-recheck.md) | 复查裁定 | 第二轮复查：22/22 处置属实，无强制修改项 |
| [20260915-round3-audit-and-remediation-plan.md](20260915-round3-audit-and-remediation-plan.md) | 审核工作单 | 第三轮审核：新发现问题的解决方向 |
| [20260915-round4-full-review-analysis.md](20260915-round4-full-review-analysis.md) | 复核报告 | 第四轮全项目复核（三路只读）与优化建议 |
| [20260915-round4-review-fix-summary.md](20260915-round4-review-fix-summary.md) | 整改记录 | 第四轮问题的代码整改与验证证据 |
| [20260915-round4-review-fix-summary-2.md](20260915-round4-review-fix-summary-2.md) | 整改记录 | 第四轮后续：发布阻断与正确性问题收口 |
| [20260915-round4-review-audit-and-next-optimization.md](20260915-round4-review-audit-and-next-optimization.md) | 复核工作单 | 第四轮整改复核 + 后续优化工作单 |

## 三、第四轮遗留项整改（2026-09-16）

| 文档 | 类型 | 内容 |
| --- | --- | --- |
| [20260916-round4-review-remediation-work-order.md](20260916-round4-review-remediation-work-order.md) | 整改工作单 | 第四轮遗留问题的整改要求 |
| [20260916-round4-review-remediation-summary.md](20260916-round4-review-remediation-summary.md) | 整改总结 | 遗留项落地结果与回归门禁实测值 |

## 四、第五轮（2026-09-16）

| 文档 | 类型 | 内容 |
| --- | --- | --- |
| [20260916-round5-remediation-review-and-full-project-audit.md](20260916-round5-remediation-review-and-full-project-audit.md) | 复核 + 增量审核 | 13 项重裁定 + R5-1…R5-7 新增问题 |
| [20260916-round5-remediation-change-note.md](20260916-round5-remediation-change-note.md) | 整改修改说明 | 依据第五轮要求改了哪些文件、如何验证 |
| [20260916-round5-review-remediation-summary.md](20260916-round5-review-remediation-summary.md) | 整改结论对照 | 每项的重裁定与完成判定 |

## 五、第六轮（2026-09-16）

| 文档 | 类型 | 内容 |
| --- | --- | --- |
| [20260916-round6-review-of-round5-remediation.md](20260916-round6-review-of-round5-remediation.md) | 独立复核 | 第五轮整改修改说明的核验 + 全项目增量清查 |
| [20260916-round6-remediation-change-note.md](20260916-round6-remediation-change-note.md) | 整改修改说明 | 依据第六轮复核的改动清单 |
| [20260916-round6-review-remediation-summary.md](20260916-round6-review-remediation-summary.md) | 整改结论对照 | 第六轮的完成判定 |

## 约定

- 本目录文档**只追加、不改写**：它们记录的是"当时的事实与裁定"，后续轮次若推翻前轮结论，
  在新文档里说明，而不是回改旧文档；
- 命名前缀是日期（`YYYYMMDD-`），轮次靠 `roundN` 与"文档类型"行区分；
- 新增整改/复核记录放这里，并在本索引追加一行。

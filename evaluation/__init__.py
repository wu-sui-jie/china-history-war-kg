"""F10 问答效果评测（RAGv4）。

离线评测包，从 RAG/ 根目录运行（依赖相对 import 项目包）：
    python -m evaluation.cli run    --bank data/eval/<version>/questions.jsonl
    python -m evaluation.cli report --run <run_dir> [--scores <scores.jsonl>]

职责划分：
- evaluation/bank.py   题库（黄金问答集）schema、校验、读写（含人工审核字段）；
- evaluation/chain.py  在进程内按配置复跑 RAGv2 问答链（F02→F03/F04→F05→F06），
                       产出结构化 trace（默认双通道与 sse.run_query 口径一致，另支持
                       单文本通道/关键词 AND/OR 等对比配置，绕过回答缓存）；
- evaluation/metrics.py 客观指标：实体命中、图谱命中、文本 top-k 覆盖、融合携带、
                       回答覆盖与四类失败归因；
- evaluation/grading.py 人工评分模板导出/读回（答案正确性 + 引用正确性，逐条复核）；
- evaluation/report.py 汇总报告：指标统计、双通道 vs 纯文本对比、失败样例与归因；
- evaluation/cli.py    命令行入口（run / report / check-bank）。

数据产物约定（延续 RAG 仓库"数据产物不入库"约定）：
- 题库（人工维护资产）提交 Git：data/eval/<version>/questions.jsonl + questions.meta.json；
- 运行痕迹 / 报告 / 评分中间件在 data/eval/<version>/runs/（已在 .gitignore 忽略）。
"""

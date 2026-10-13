# 上下文压缩与年份提示的控制变量试运行

> Archived on 13 October 2026. This document records its original stage; dates, budgets and pending work below are historical. Current scope and results: [frozen release](../frozen_release.md), [verification](../verification.md). Commands run from the repository root.

用户于2026-10-10授权：Tinker累计总上限提高到US$12，以下三组各一次；Snowflake继续只用试用额度。

| 组 | 字段覆盖反馈 | 上下文压缩 | 年份提示 |
|---|---|---|---|
| baseline | 开 | 关 | 关 |
| compact_only | 开 | 开 | 关 |
| condition_only | 开 | 关 | 开 |

三组使用同一冻结代码、Qwen/Qwen3.5-9B公开base、官方shipping、固定源内容和官方评分。每组独立warehouse状态，24模型轮、48工具调用、24576上下文、4096生成上限；temperature=1、top_p=1、top_k=-1。不训练、不自动重试，不人工补提交。

`--no-condition-feedback`仅关闭公开年份提示，保持字段覆盖检查；未传该选项时保留v0.3行为。`--seed 20261010`通过最小客户端适配器给第n次采样请求传入20261010+n，继续使用固定cookbook completer。三组使用同一seed序列；不同观察下输出不必相同，服务端也未保证跨版本逐token确定性。此选项只用于单rollout评估，避免相同seed使GRPO组失去独立采样。

上下文压缩组验证的是既有整套改动：6行预览、错误截断、紧凑JSON、轮数/提交提醒、额外预算提示和128生成预留。它没有分别拆开这些内部部件，结论只能归于这个组合。年份提示组仅切换年份检查，其他反馈与基线一致。

记录实际提交、完整任务是否正确、终局reward、正确列、结束原因、轮数、工具错误、输入/输出tokens、耗时及提示触发次数。只有显式提交后才按真实执行评分；未提交R0且正确列未知。保留所有失败，不从单样本断言总体成功率或显著提升。

私密预注册、预算批准、原始轨迹及最终对照报告位于`runs/controlled_ablation_20261010/`；原三次试运行保留。文档不含密钥或隐藏正确结果。

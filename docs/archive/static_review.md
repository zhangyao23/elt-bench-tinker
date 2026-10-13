# 真实运行前静态审查（2026-10-09）

> Archived on 13 October 2026. This document records its original stage; dates, budgets and pending work below are historical. Current scope and results: [frozen release](../frozen_release.md), [verification](../verification.md). Commands run from the repository root.

范围：初始提交f0f94d6的完整实现；从实现规范（Standards）和教授要求（Spec）两条独立审查线检查。模型采样与优化前完成修复、回归和实际destination预检。只读账号检查不计作官方rollout。

## Standards

1. P1：原recipe只限制单次调用，不能保证rollout、训练及重试合计预算。新增共享预算账本，在调用服务前原子预留保守费用；失败不自动返还，拒绝NaN/Infinity与隐式提高总额。
2. P1：固定cookbook默认FailFast在一条协程失败后可能先清理、兄弟轨迹仍继续。新增最小CompleteGroup strategy，等待所有已发出轨迹结束再传播错误，不引入自动重试；取消时等待已发出的同步数据库操作结束再清理。上游快照不修改。
3. P2：model_sql把受控ValueError误归为基础设施故障。保留为模型可修复反馈，并在SQL边界拒绝明确的重复输出alias。

Standards共3类问题；最严重的是费用合计门禁与并发清理。没有为代码smell进行无关重构。

## Spec

1. P1：普通Snowflake schema的future grants需要未授予loader的全局权限。改为WITH MANAGED ACCESS，让schema owner仅在自己的schema范围授予权限，保持agent角色受限。
2. P2：原recipe把未获得满分的有效官方rollout当作运行失败，教授原文没有100%分数门槛。分开记录rollout_completed、task_solved、pipeline_submitted和实际reward；基础设施无效轨迹仍失败。
3. P2：重复列名错误提前终止并排除训练组，违背“use execution feedback”。修复分类后，模型能修正alias继续执行。

Spec共3类问题；最严重的是Snowflake授权。与Standards重叠的SQL错误分类只修一次。

## 验证与限制

- 本地43项pytest通过，Ruff通过；新回归覆盖SQL修复、累计费用/NaN、兄弟任务排空、取消后的同步操作清理。
- Tinker Qwen/Qwen3.5-4B账户能力已只读验证，Snowflake公钥认证及SHOW元数据通过，warehouse仍可自动暂停。
- 原生五源重新核验内容通过。人工Snowflake预检另记录，不能当作模型驱动官方rollout。
- 每次执行重新查询实际结果；没有缓存最终评分。初始计划保守合计约US$6.13（1条rollout、最多3组x4条训练、24小时checkpoint存储上界），以运行时rate card与共享账本为准。
- 持久checkpoint设置24小时TTL；临时sampler由SDK管理。云真实采样、优势、优化应答与checkpoint证据尚待接续运行。
- Snowflake授权依据：[GRANT规则](https://docs.snowflake.com/en/sql-reference/sql/grant-privilege)；存储费率/TTL依据：[Tinker checkpoints](https://tinker-docs.thinkingmachines.ai/tinker/howto/checkpoints/)。

私人运行证据保存在Git忽略的runs目录。此次是运行正确性修复，没有声称新的训练算法、性能提升或泛化能力。

## 运行联调后的修复与结果

静态检查后，实时联调发现SDK0.32 Datum/TensorData已改为dataclass，原日志序列化假定Pydantic，在优化器调用前失败。修复显式序列化、添加roundtrip测试，并加入整组真实轨迹恢复及独占预算receipt。共同optimize_data入口复用固定cookbook，最终46项回归/Lint/构建通过。

Snowflake真实RBAC和人工pipeline预检通过；官方9B模型rollout真实提交，评分0.8125（13/16列）。四条真实采样产生不同执行奖励，保留全部正负轨迹，修复后一次真实优化及checkpoint保存确认。原始失败不删除、不改分；没有以本地测试或人工SQL替代后两项验收。

恢复入口补充金融故障门禁：训练请求发出前写exclusive attempt receipt；已有attempt但缺应答时不推断“尚未训练”，避免响应丢失导致重复计费。旧记录只允许明确的前置Datum序列化失败恢复，其他未知故障须先核验服务端。回归包含响应丢失时不重复发出请求。

# 一次训练后模型评估

日期：2026-10-13。按用户授权只运行一次，加载原已完成一次真实 Tinker 优化的 Qwen3.5-9B/rank16 checkpoint，不追加训练、不因低分重跑。

## 设置和评价标准

沿用共同基线的官方 shipping/Snowflake、固定任务/数据/官方 evaluator、renderer、24模型轮、48工具调用、24576上下文、4096生成上限、temperature=1/top_p=1/top_k=-1、seed20261010逐请求加1。字段覆盖反馈开，压缩和年份提示关。源码仅增加 checkpoint 评估入口，原环境、工具和 reward 规则不变。

完整成功要求显式提交、加载完整且空状态重放后全部输出匹配。未提交的 reward 为0，最终正确列数未知；不人工补提交。成本分别记录输入/生成 token，耗时按实际执行记录。参考是2026-10-10的一条历史共同基线，不是本次追加采样。

## 实际结果

| 指标 | 历史共同基线（原公开模型） | 本次训练后模型 |
|---|---:|---:|
| 有效运行 | 是 | 是 |
| 显式提交 | 是 | 否 |
| 完整成功 | 是，16/16 | 否，正确列数未知 |
| 终局 reward | 1 | 0 |
| 模型轮数 / 工具调用 | 10 / 19 | 24 / 30 |
| 动作错误 | 2 | 7 |
| 累计输入 token | 93,573 | 273,572 |
| 生成 token | 3,568 | 9,011 |
| rollout 耗时 | 91.66秒 | 177.12秒 |

本次结束原因是 `max_turns`。模型加载全部五源，成功执行两条转换，已创建 drivers/trucks；随后仍尝试查询和修改，未调用 submit_pipeline。七个错误均为实际 Snowflake SQL 编译错误，包括不存在/歧义字段、窗口函数嵌套聚合和非法 GROUP BY；最后两次重复 GROUP BY 错误。创建表不等于值正确，没有终局重放/模型比较，不能追认为若干正确列。

**这一条训练后样本未显示收益，表现比该历史基线差。** 每边只有一条；同 seed 不保证服务确定性，运行日期和基础设施条件也不同，因此不能推断总体成功率、RL导致退化或准确的性能变化比例。基线耗时包含终局重放评分，本次未提交没有同样阶段；两者时间仅作实际轨迹描述。

## 真实性、预算与清理

- 原训练已确认一次真实优化；本次加载其已保存训练状态，服务确认同一9B模型/rank16，随后导出临时采样权重。没有新 forward/backward、optim_step 或永久 checkpoint。
- 当前实际参数、task provenance和初始模型观察均与历史基线一致；运行代码 SHA 与请求前保存的摘要一致。任务、评分和凭据隔离未改变。
- 本次保守费用预留US$0.256278528，共享账本累计预留US$10.51623936≤批准US$12。按实际输入/生成 token和本次保存的公开单价、不计cache优惠估算US$0.089343477；不是账单，不含Snowflake试用额度消耗。
- 采样前发现 Windows WMI查询阻塞和WSL无响应。经用户分别授权重启项目发行版、整个WSL及修复WMI，恢复Winmgmt及原运行的TPHKLOAD依赖服务，没有重启Windows或删除发行版/磁盘。固定五源行数与内容全部核验通过后才采样；前置故障不计入模型样本。
- 88项完整本地测试、Ruff通过；修复后原生Python正常，checkpoint路径9项检查原生通过，训练超参数依赖导入恢复。临时Windows后备启动脚本仅用于准备阶段，正式rollout使用原生Python入口。
- 本次HTTP服务与三个项目容器已停止，四端口关闭，数据保留；rollout无warehouse cleanup错误记录。

## 复用入口和证据

在原 rollout 命令上添加 `--checkpoint-record <已完成训练的training.json>`。入口核对完成状态、优化步数、模型、rank及task provenance，加载已保存权重并验证云端模型/rank；不能与train或resume模式混用，不回退到公开模型。

本地私密证据位于 `runs/post_rl_eval_20261013/`：test_plan.json、checkpoint_preflight.log、source_preflight.json、evaluation_attempt.json、rollout/、result.json、comparison.json、control_audit.json、failure_analysis.json和cleanup.json。凭据、checkpoint地址、原始采样轨迹及系统修复脚本不随Git发布。

三项教授最低运行证据仍保留。后续若要判断RL收益，应在冻结环境内预先规定更多更新前/后独立样本和费用上限；本次未启动该扩展。

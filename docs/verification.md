# Step 3–5 验证与边界

## 当前证据

原始固定 comparator 未修改；与材料源码的 SHA-256 完全相同。官方任务 data_model 与 5 个 schema 和固定 HF revision 一致：runs/version_audit.json。五个源数据 SHA/行数/类型/内容指纹及 driver Parquet 双来源一致性：runs/source_download_audit.json、runs/source_integrity_audit.json。

人工 SQL 的真实 DuckDB snapshot diagnostic：runs/shipping_snapshot_diagnostic/execution/grade.json，2 个模型的 16 个要求列 match，R=1；只作版本/数据兼容证据。后来已完成PG/Mongo/S3实际读取与完整本地链路验证，但人工SQL/DuckDB结果仍不能作为官方带凭据模型rollout。

本地测试覆盖真实 CSV/HTTP/Parquet、DuckDB load、SQL 错误修复、raw 内容核验、独立进程 replay、官方 comparator 的 extra-column/NULL/tolerance/row-order/duplicate 行为。2026-10-08 已进一步通过5个官方真实源读取、全链路DuckDB手工SQL诊断和独立进程replay；2模型/16要求列全部匹配v5 GT，reward=1。证据 runs/native_source_services.json、native_shipping_diagnostic/execution/grade.json、native_shipping_independent_replay/grade.json。它仍不是模型驱动的官方credentialed rollout。真实 Qwen3.5 tokenizer 和 cookbook renderer 测试使用 XML-style scripted tool actions，验证 token Env、action loss mask、logprobs 和 invalid group 排除；它没有采样模型或训练权重。

最终回归结果为46项通过；最新本地运行记录保存在Git忽略的runs目录，不从脚本存在推定已经运行成功。wheel clean install 与独立目录测试记录在 runs/clean-*。个人恢复状态TASK_STATE.md仅本地保存。

## Step 4：reward hacking

| 攻击/问题 | 防护与验证 |
|---|---|
| 读 hidden GT/凭据/任意文件或 URL | 没有 shell/file 工具；SQL AST 限制关系、函数和多语句；DuckDB external_access=false。SQL scan/跨 schema/pragma/getenv 拒绝测试 |
| 改 raw 表 | 模型只可 CREATE 指定 output/tmp_；提交前及 replay 后核验实际 raw 内容；人工 raw DELETE 不能拿到 reward=1 |
| 旧 grade/result CSV 冒充成功 | 每次重新执行并 query；旧 grade.json 污染测试仍得实际错误分数 |
| 未提交/漏加载/反复提交 | 显式 submit gate、完整源库存、terminal guard；重复 submit 拒绝 |
| NULL/重复行/额外列/重复列名 | raw canonical 包含类型/NULL/重复次数；输出 row-count 与重复 projection 检查；extra columns 仅诊断，遵守官方允许规则 |
| 跨 episode 污染 | 独立 DuckDB connection / 随机 Snowflake schemas；独立 episode isolation 测试 |
| 环境坏掉变成“模型失败” | valid=false/reward=null，整组排除；source missing/invalid group 测试 |
| 恒定 reward 或手工设 reward 冒充更新 | cookbook 组中心化；全相同跳过；训练 acknowledgement 仅在真实 futures 返回后写 |

Snowflake 已通过真实公钥认证、五源加载、模型转换、独立重放和 schema 清理；实际权限探针允许 raw SELECT/model CREATE，拒绝 raw UPDATE/CREATE。loader 与 agent role 分开，secondary roles 关闭；管理员必须保证 ELT_AGENT 不继承 loader/admin/生产权限。多个 episode 共用 ELT_AGENT，随机 schema 与 AST 关系限制共同隔离；数据库角色本身不能单独阻止访问该角色已获授权的其他 episode schema，因此不能宣称已实现每个 episode 独立角色隔离。没有观察到跨 episode 绕过；保留隐藏参考不能消除公开 benchmark 答案记忆或硬编码风险。

## Step 5：效率实测与简化审查

同一个极小 local fixture，各 10 次真实执行，tracemalloc 开启：

| 项目 | 复用 source fingerprint 前 | 后 |
|---|---:|---:|
| Episode 全流程中位数 | 0.14098 s | 0.12213 s |
| 评分中位数 | 0.07797 s | 0.06766 s |
| Python 分配峰值中位数 | 420143 B | 419793.5 B |

证据：runs/benchmark-before.json、runs/benchmark-after.json。优化只复用已计算的 source 内容指纹；actual warehouse 指纹、fresh source reader 和最终 query 仍实际执行。中位时间约下降 13%，但样本小且存在调度噪声，只能作为本地微型 fixture 结果；tracemalloc 不含 DuckDB/Arrow native 内存。尚无云端优化前后的同条件对照，也未单独测量 API sampler 吞吐。

真实运行耗时已进一步从保存轨迹核对：9B 基线总体 113.544 s，初始 run_load 20.099 s，query 累计 1.008 s，transform 累计 2.676 s，submit/grading 29.412 s。replay 23.209 s 包含在 grading 内；初始与 replay 两次加载累计 37.916 s、源抽取累计 0.572 s，不能把这些重叠项再次相加。4条并行训练采样的组耗时为 176.964 s；其余时间不能全部归因于模型采样。证据：runs/optimization_diagnosis_20261010.json。

按 Ponytail/code-simplification 做有范围审查：把 fixture 从 tests 移入安装包、去掉无效 dictionary wrapper、按职责拆分 dispatch/raw verification/model grading、保留统一 SQL boundary；没有改上游训练实现或 comparator。SQL 检查与网络调用次数未为“简化”而增加；source 缓存使用副本，模型不能写它。现有功能/回归均需通过最终检查。

## 已知限制与交付状态

- WSL已恢复默认Ubuntu-22.04-Recovered，Podman3.4.4运行本地源容器；5源均已验证。旧Ubuntu登记保留，避免注销误删同一磁盘。镜像按固定digest下载并核验，当前trusted本地容器未启用cgroup配额，正式扩大负载需合适资源限制。
- 三项运行验收已有真实证据。预算账本约束全部运行的保守token与24小时checkpoint存储预留，不是服务端实时账单硬限额；warehouse/tax另计，实际账单可能延迟。
- implementation design 已根据真实云运行更新，本地文档正在做提交前核对；正式 Google Doc、向教授提交和邮件尚未完成。私人通信、凭据、运行数据和操作员状态不上传实现仓库。
- 当前 SQL 接口会拒绝合法的 models 限定目标以及部分日期/窗口表达式，查询日期默认反馈为 epoch-ms；这些限制与模型 SQL/业务语义错误分开记录。后续修复与反馈研究需固定官方 reward，不把接口修复当作算法创新，也不从离线诊断推定成功率提升。

## 2026-10-09 真实云验收

| 验收 | 结果 | 私人证据路径（Git忽略） |
|---|---|---|
| credential-free integration | 通过，最终46项回归/Lint/构建通过 | runs/final_real_acceptance_tests_20261009.log |
| 官方credentialed rollout | 9B生成两模型并提交，Snowflake实际评分0.8125，13/16列；新进程重放一致 | runs/official_rollout_20261009_9b_01、runs/official_rollout_20261009_9b_replay |
| 真实RL优化 | 原4轨迹奖励[0,0.875,0,0.9375]，非零优势；真实fwd/bwd与optim_step确认一次，checkpoint服务端核验 | runs/tinker_step_20261009_9b_01、runs/tinker_step_20261009_9b_resume_01 |

实际查询SQL/结果CSV保存在replay的warehouse_results。原4B三条失败、9B基线部分错误以及SDK序列化失败均保留，不追认成满分。replay CLI对非满分pipeline返回exit1，但valid/reward一致，证明可重放，不代表任务完全正确。

SDK0.32 Datum/TensorData为dataclass；修复显式序列化后复用原整组实际轨迹和采样logprobs，创建同base/rank新初始learner进行importance_sampling，不宣称恢复了原client或重复采样。恢复receipt阻止同一预留重复更新。API确认返回9个训练logprob序列；optimizer额外metrics为空，未虚构梯度范数。服务端checkpoint大小567621237字节，最初24小时TTL；随后按用户选择延长7天，expires_at 为 2026-10-16T15:42:32.196595+00:00（北京时间2026-10-16 23:42）。本地 LoRA 备份不含优化器状态，不随云端TTL到期删除。

真实数据mask核验：39631采样action tokens全部被训练，观察tokens的advantage为零。一个真实更新证明训练链路执行成功，尚未比较更新前后准确率。型号、提示和token上限在调试中变化，结果不构成受控消融或算法性能结论。

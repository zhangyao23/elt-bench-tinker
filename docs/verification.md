# Step 3–5 验证与边界

## 当前证据

原始固定 comparator 未修改；与材料源码的 SHA-256 完全相同。官方任务 data_model 与 5 个 schema 和固定 HF revision 一致：runs/version_audit.json。五个源数据 SHA/行数/类型/内容指纹及 driver Parquet 双来源一致性：runs/source_download_audit.json、runs/source_integrity_audit.json。

人工 SQL 的真实 DuckDB snapshot diagnostic：runs/shipping_snapshot_diagnostic/execution/grade.json，2 个模型的 16 个要求列 match，R=1；只作版本/数据兼容证据。后来已完成PG/Mongo/S3实际读取与完整本地链路验证，但人工SQL/DuckDB结果仍不能作为官方带凭据模型rollout。

本地测试覆盖真实 CSV/HTTP/Parquet、DuckDB load、SQL 错误修复、raw 内容核验、独立进程 replay、官方 comparator 的 extra-column/NULL/tolerance/row-order/duplicate 行为。2026-10-08 已进一步通过5个官方真实源读取、全链路DuckDB手工SQL诊断和独立进程replay；2模型/16要求列全部匹配v5 GT，reward=1。证据 runs/native_source_services.json、native_shipping_diagnostic/execution/grade.json、native_shipping_independent_replay/grade.json。它仍不是模型驱动的官方credentialed rollout。真实 Qwen3.5 tokenizer 和 cookbook renderer 测试使用 XML-style scripted tool actions，验证 token Env、action loss mask、logprobs 和 invalid group 排除；它没有采样模型或训练权重。

既有回归结果为39项通过；最新本地运行记录保存在Git忽略的runs目录，不从脚本存在推定已经运行成功。wheel clean install 与独立目录测试记录在 runs/clean-*。个人恢复状态TASK_STATE.md仅本地保存。

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

Snowflake 的实际 RBAC、stage/类型行为和失败资源清理尚待账号验证，不能只凭 AST 单层检查宣称安全。raw role 单独于模型 role，secondary roles 关闭；管理员必须保证 ELT_AGENT 不继承 loader/admin/生产权限。保留隐藏参考不能消除公开 benchmark 答案记忆或硬编码风险；未把一个 task 的通过当泛化训练效果。

## Step 5：效率实测与简化审查

同一个极小 local fixture，各 10 次真实执行，tracemalloc 开启：

| 项目 | 复用 source fingerprint 前 | 后 |
|---|---:|---:|
| Episode 全流程中位数 | 0.14098 s | 0.12213 s |
| 评分中位数 | 0.07797 s | 0.06766 s |
| Python 分配峰值中位数 | 420143 B | 419793.5 B |

证据：runs/benchmark-before.json、runs/benchmark-after.json。优化只复用已计算的 source 内容指纹；actual warehouse 指纹、fresh source reader 和最终 query 仍实际执行。中位时间约下降 13%，但样本小且存在调度噪声，只能作为本地微型 fixture 结果；tracemalloc 不含 DuckDB/Arrow native 内存。没有测量 Snowflake/API sampler 的吞吐或端到端训练时间。

按 Ponytail/code-simplification 做有范围审查：把 fixture 从 tests 移入安装包、去掉无效 dictionary wrapper、按职责拆分 dispatch/raw verification/model grading、保留统一 SQL boundary；没有改上游训练实现或 comparator。SQL 检查与网络调用次数未为“简化”而增加；source 缓存使用副本，模型不能写它。现有功能/回归均需通过最终检查。

## 未解决资源

- WSL已恢复默认Ubuntu-22.04-Recovered，Podman3.4.4运行本地源容器；5源均已验证。旧Ubuntu登记保留，避免注销误删同一磁盘。镜像按固定digest下载并核验，当前trusted本地容器未启用cgroup配额，正式扩大负载需合适资源限制。
- Snowflake实际连接/权限、官方模型rollout与真实Tinker优化尚未通过验收。max-cost-usd 是保守 token 预估门禁，不是服务端实时账单硬上限，不含 warehouse/storage/tax；操作员应控制所有运行的累计费用。
- implementation design是待云验收后更新的草稿，尚未向教授提交或发送邮件。私人通信、凭据、运行数据和操作员状态不上传实现仓库。

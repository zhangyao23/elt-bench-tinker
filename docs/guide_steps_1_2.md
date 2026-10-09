# Step 1–2：任务、交互与奖励

依据：教授英文原文；ELT-Bench 2504.04808v2 §2.3、§2.4、§3.1；DeepSeekMath 2402.03300v3 §4.1。论文与代码都保留固定版本，本文是实现说明而非实验性能结论。

## Step 1：任务与状态

ELT 是先 Extract/Load，再 Transform。必须先把不同系统的数据统一装进 warehouse，SQL 才能跨源 join。单独生成正确 SQL 不保证加载完整；能连接数据库也不保证结果符合业务定义。

首个 task 为 shipping，源库存如下。数据库客户端与 URL 由操作员绑定，模型使用逻辑源名称，不接触真实凭据。

| 源表 | 官方来源 | 行数 | 主要用途 |
|---|---|---:|---|
| shipment | PostgreSQL | 960 | 重量、年份、司机、货车、客户与城市关系 |
| customer | MongoDB | 100 | 收货人、Autoware Inc 条件 |
| truck | REST API | 12 | 品牌、车型年份、总部规则 |
| city | LocalStack S3 CSV | 601 | 最少人口城市 |
| driver | Google Drive Parquet | 11 | 司机主体及姓名 |

trucks 的 8 列要求：truck_id、make、model_year、品牌到总部映射、运单数、最重运单收货人（重量并列按姓名升序）、超过 10000 磅比例、2017 与 2016 运单数差。drivers 的 8 列要求：driver_id、first_name、last_name、2017 运单数、送往最少人口城市的运单数、Autoware Inc 运单比例、是否运输超过所有运单平均重量 95% 的货物、首次运单重量（日期并列取重量较大）。涉及“比例”的具体尺度和无活动 NULL 必须以固定参考和业务说明核验，不能靠猜测报告成功。

公开 task 包含 schema/业务描述，隐藏包包含 GT、评测 SQL、sort keys、行数和哈希。模型工具只读取公开 task 和自己的数据库结果；不提供文件系统或任意 Python/shell 工具。

状态流：新 episode → 配置来源 → 抽取/加载 raw → 查询/转换 models → 修复 SQL 错误 → 显式提交 → 独立 replay → 查询与评分 → 清理。它不是 stateless：数据库表、成功 SQL 顺序、已加载集合和工具历史都影响后续动作。每条轨迹有独立数据库或 Snowflake schema；同条轨迹内工具顺序执行。

| 工具 | 输入 | 状态及反馈 |
|---|---|---|
| inspect_task | 无 | 返回公开规范，不改 warehouse |
| configure_load | table/source_kind/sync_mode | 记录允许源的 full_refresh 配置 |
| run_load | tables（空列表=全部已配置源） | 真实提取并替换 raw 表，返回行数/schema |
| query | 一个 SELECT | 只读取 raw/models，最多 30 行 |
| transform | 一个 CREATE TABLE/VIEW AS SELECT | 写 models，成功 SQL 加入 pipeline |
| submit_pipeline | 无 | 冻结 pipeline，重放并给终局 reward |

例：inspect_task → configure_load(shipment,postgres) 等 5 个源 → run_load([]) → query 检查年份和 NULL → transform(trucks) → 编译错误反馈 → 修正 transform → transform(drivers) → submit_pipeline。发生错误的 SQL 不进入提交物；成功但结果错误的 SQL 仍通过最终执行评分发现。聊天回复没有 submit 工具调用则不算提交。轮数/token/tool 上限导致未提交的有效轨迹得 0；服务故障使 episode 无效，整组不训练。

## 论文与当前版本的差异

2025 论文采用 Snowflake、Airbyte/Terraform 和 dbt，公开 config 含连接信息；本任务原文要求端到端 ELT 能力，没有强制 Airbyte。本实现采用 native clients、受控 SQL 和私密操作员配置，因此不声称复现论文代理的工具自由度或原始成绩。固定 elt_bench++ 源码已扩展 Snowflake/Databricks/Redshift 和 v5 GT。Snowflake 原始 layout 为 task database.AIRBYTE_SCHEMA；本实现将逻辑 raw/models 映射到私有隔离 schema，评测查询做对应映射。

论文/官方 Stage 1 仅以所有源表的行数评估加载；这里另外核验内容与重复行。论文允许额外输出列；官方 comparator 也按 GT 列逐列比较，因此官方 match 与更严格完整性诊断必须分别报告。sort keys 有 SQL 表达式（drivers 的 CAST）；官方 helper 不解析该表达式，会用其余列作确定性排序。本文保持 helper 行为，并真实执行官方 ORDER BY 查询。

## Step 2：reward

正确 pipeline 应可从空 warehouse 重放，从官方源取得全部数据，产生满足业务定义的 models。奖励来自执行结果，不来自模型自述、SQL 文本或预写 result CSV。

R = 0（未提交、遗漏加载、raw 完整性错误或重放失败的有效 pipeline）；通过加载完整性后，R = 正确 GT 列数 / 全部 GT 列数。每个输出必须具备正确行数、没有重复列名、没有遗漏必需列；单列比较沿用固定官方 numeric/string/NULL 容差。报告官方逐模型 match、全部模型成功、额外列/唯一键/内容诊断；RL partial reward 不等于官方 task 成功。

基础设施错误使用 valid=false、reward=null，整组丢弃，而非零分；否则模型无法区分错误代码与坏掉的数据库。cookbook 的浮点 StepResult 接口暂用 0 作占位，让同组其它轨迹结束；随后 group validator 必须排除整组，占位值不能进入训练。只有实际 source/warehouse 执行产生的有效轨迹参与训练。无额外调用奖励或 process bonus，避免模型通过重复 load/query 获利。过程反馈保留 SQL 错误、schema 和实际结果，以帮助修复。

DeepSeekMath 原始 GRPO 采用组均值与标准差归一化并包含 PPO/KL 设置；固定 cookbook 的 compute_advantages 仅减组均值。本 recipe 明确使用 cookbook 的组中心化优势和 importance_sampling loss，不宣称逐式复刻论文。每组使用同 task 的独立模型采样；全部奖励相同则优势全零，跳过更新并在有限批次后停止。使用 cookbook 数据处理保留采样 logprobs，prompt/tool tokens 的 loss mask 为 0。

正反例应覆盖：完全正确、部分错误、遗漏加载、NULL 不一致、重复/缺失行、额外列、顺序变化、旧文件污染、未提交、source/warehouse 故障和恒定奖励。离线脚本动作只能证明环境/接口，不能代替模型驱动官方 rollout 或 Tinker 更新。

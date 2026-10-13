# 中文操作说明

从仓库根目录执行下列命令。当前推荐配置与英文复现入口见[reviewer guide](reviewer_guide.md)，文档索引见[README](README.md)。历史计划与审查已归档；实际实验结果及冻结版本保持原样。

面向 ELT-Bench 的有状态工具环境、执行评分与 Tinker RL recipe。固定官方 shipping task 为首个目标，DuckDB 用于无需凭据测试，Snowflake 用于官方带凭据运行。原生源 clients 替代 Airbyte 控制面；该选择与教授任务原文相容，但不声称复现论文中的 Airbyte/Terraform/dbt 代理成绩。

## 当前验收边界

三项最低运行证据来自保留的v0.1基线；后续修复版另完成官方shipping满分rollout和独立重放。三组控制变量试运行代码为`0699e18`，结果文档为`ab4dc8c`；2026-10-13新增一次训练后checkpoint评估，达到24轮未提交，R0，未显示收益。新增入口与本地测试另行记录；接口、字段反馈及实验性压缩/年份提示见下文，真实对照见docs/ablation_results_20261010.md。

| 验收 | 状态 |
|---|---|
| 无凭据 integration | 冻结版本90项原生本地测试、Lint、wheel/sdist构建和源码外干净安装/独立重放通过 |
| 官方带凭据 rollout | Qwen3.5-9B在shipping/Snowflake实际提交；原基线13/16，后续共同配置一条16/16，独立进程重放一致；不代表稳定成功率或全benchmark完成 |
| 真实 Tinker RL optimization | 已完成一次非零优势真实更新，保存服务应答与checkpoint；详见docs/verification.md |

官方 shipping 快照的人工 SQL 本地诊断 reward=1、2 模型/16 列匹配 v5 GT。这是版本/数据诊断，使用 file snapshots 与人工 SQL，不是模型驱动的带凭据官方 rollout。原生 cookbook token 环境也已在本地测试；动作是脚本提供，没有使用模型采样或本地训练冒充 Tinker 优化。

## 最小本地安装和运行（Windows PowerShell）

需要 Python >=3.11、uv。当前验证 Python 3.13.9。无需 Tinker、Docker、GPU 或账号：

```powershell
uv venv .venv
uv pip sync --python .venv/Scripts/python.exe requirements-local.lock
uv pip install --python .venv/Scripts/python.exe -e .
.venv/Scripts/python.exe -m elt_tinker.local_integration --output runs/local_run_01
```

每次使用新的 output 目录。成功输出包含 correct/wrong 两个 pipeline、tool_trace、grade、新进程 independent_replay，以及 acceptance.json。它会真正执行 CSV extraction、DuckDB load、错误 SQL、修复、提交与评分。正确 reward=1、错误 total 列 reward=0.75。新进程 replay 只读取任务包和保存的 pipeline，不继承原 Episode。

v0.2可用 `--field-feedback` 在transform后查看公开字段覆盖和实际仓库类型；默认关闭。该检查增加一次 `LIMIT 0` 元数据查询，不读取hidden参考，也不改变终局reward。字段齐全不代表数值正确。工具反馈的 `created_models` 记录已成功创建的要求模型，工具调用余量不等于模型轮数或token余量。

v0.3增加默认关闭的 `--compact-feedback`：模型收到最多6行的明确预览和真实剩余模型轮数，完整30行/错误仍保存在tool_trace，实际展示视图保存在messages/trajectory。它将环境的提前终止预留缩为128 token，实际Tinker completer仍按生成上限和剩余context取最小值；不扩大上下文、预算或自动提交。公开年份条件提醒可与字段反馈分别开关；推荐配置使用`--no-condition-feedback`关闭年份提醒。该提醒只查公开任务：只查description与SQL literal，可能误提醒或漏报，不是值正确验证，也不改变reward。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_integration.py tests/test_reward_security.py -q
.venv/Scripts/ruff.exe check elt_tinker tests scripts infra
```

## 固定官方资料与源数据

私人材料与下载数据默认不加入 Git。已有材料快照会被只读复用；若缺少快照，prepare_official.py 会自动下载固定官方 commit 到 data/upstream。脚本只使用公开上游代码，不依赖教授邮件或原始 Google Doc。

```powershell
.venv/Scripts/python.exe scripts/prepare_official.py
.venv/Scripts/python.exe scripts/prepare_sources.py
.venv/Scripts/python.exe scripts/audit_sources.py
.venv/Scripts/python.exe scripts/diagnose_shipping.py
```

prepare_sources 使用标准库 zipfile + HTTP Range 从官方 4.27GB source archive 和 API archive 只提取 shipping；driver Parquet 从官方 URL 下载。无需完整 archive，不把旧 gt.zip 当 v5。任务/GT 使用 HF revision 52a48541547a4340e19e54386350740acca80146；所有已用源文件 SHA 和内容指纹留在 runs/source_*audit.json。

## 启动官方源服务

infra/compose.yaml 已锁定官方上游镜像的 registry digests，只绑定 127.0.0.1 的独立端口 58433/58717/58466，不挂载 Docker socket、不初始化其它任务。这里的口令是可公开的本地测试口令，不是云凭据。

```powershell
docker compose -f infra/compose.yaml up -d
# 另一个终端运行本项目的原始数据 API
.venv/Scripts/python.exe infra/source_api.py
$env:ELT_POSTGRES_PASSWORD = 'local_test_only'
$env:ELT_MONGODB_URI = 'mongodb://elt:local_test_only@127.0.0.1:58717/?authSource=admin&directConnection=true'
.venv/Scripts/python.exe scripts/seed_sources.py
```

seed_sources 只访问这些本地端口，把固定 shipment/customer/city 初始化后，通过 5 个 native readers 核验行数与内容指纹。当前已在恢复后的 WSL/Podman 上验证全部五源。完整本地诊断可运行 `.venv/Scripts/python.exe scripts/diagnose_native_shipping.py`；它使用人工 SQL 与 DuckDB，不是模型驱动官方 rollout。失败时保留日志，不视为零奖励。

收尾：在启动 API 的终端 Ctrl+C，然后 `docker compose -f infra/compose.yaml down`。每条 episode 的 destination 资源由环境 finally 清理；cleanup_errors.json 非空时必须人工检查残留。

Windows 上已有 WSL/Podman 时，也可使用 `scripts/source_services.py start`、`status`、`stop` 管理同一组项目容器；通过 `--distro` 选择自己的 WSL 发行版。本项目已用此路线验证五源读取。`stop` 保留容器数据，重启后先核验内容，缺失时再初始化。

## Cookbook 和真实 Tinker recipe

仅在需要 cookbook/训练时安装固定上游 ZIP（它的 VCS 构建需要显式版本）：

```powershell
$env:SETUPTOOLS_SCM_PRETEND_VERSION = '0.0.0+1c03a20'
uv pip install --python .venv/Scripts/python.exe -r requirements-training.lock
uv pip install --python .venv/Scripts/python.exe 'tinker-cookbook @ https://github.com/thinking-machines-lab/tinker-cookbook/archive/1c03a20fdda98156ccbec15728c0e2764b5a0bc3.zip'
Remove-Item Env:SETUPTOOLS_SCM_PRETEND_VERSION
.venv/Scripts/python.exe -m pytest tests/test_cookbook.py -q
```

requirements-training.lock 记录已验证的 SDK/客户端依赖；torch 为客户端数据处理，不做本地模型训练。所选模型的公开 tokenizer 会按 cookbook 路径下载；真实验收型号为 Qwen3.5-9B，首次测试需要联网。

真实验收使用Qwen/Qwen3.5-9B；4B三条尝试均未提交，失败记录保留。型号由操作员明确选择，recipe不自动fallback，以账号capabilities和预算为准。先由操作员配置 `TINKER_API_KEY`、`SNOWFLAKE_ACCOUNT/USER/DATABASE/WAREHOUSE/ROLE/AGENT_ROLE` 及 password 或 private_key_file。密钥只在私密环境/文件中设置，不粘贴聊天或提交 Git。参考 infra/snowflake_setup.sql 准备专用测试 DB 与两种角色，禁止 ELT_AGENT 继承 ELT_LOADER 的权限；环境关闭 secondary roles。

资源和预算明确后才运行下列命令，示例数字不是消费授权。两次使用同一个预算账本，max-cost-usd为合计预留上限：

```powershell
.venv/Scripts/python.exe -m elt_tinker.recipe --mode rollout --model Qwen/Qwen3.5-9B --public data/official/shipping/public --hidden data/official/shipping/hidden --destination snowflake --operator-config infra/source_config.example.json --output runs/official_rollout_01 --context-tokens 24576 --budget-ledger runs/acceptance_budget.json --max-cost-usd 10 --renderer qwen3_5_disable_thinking --generation-tokens 4096 --field-feedback --no-condition-feedback --seed 20261010
.venv/Scripts/python.exe -m elt_tinker.recipe --mode train --model Qwen/Qwen3.5-9B --public data/official/shipping/public --hidden data/official/shipping/hidden --destination snowflake --operator-config infra/source_config.example.json --output runs/tinker_step_01 --steps 1 --group-size 4 --max-batches 1 --max-turns 24 --context-tokens 24576 --budget-ledger runs/acceptance_budget.json --max-cost-usd 10 --renderer qwen3_5_disable_thinking --generation-tokens 4096 --field-feedback --no-condition-feedback
```

recipe 获取公开 rate card，按全部允许调用的最坏上下文/生成/训练token估计，并预留24小时checkpoint存储上界。在共享账本中原子预留；合计超过max-cost-usd时在创建ServiceClient前退出。失败预留不自动返还，不可通过更换账本规避已批准总额。这是保守预算，不是服务端实时账单硬限额；warehouse/tax单独核算。服务故障使整组排除，先等待已发出的轨迹结束再清理；恒定奖励跳过更新，有限批次耗尽后报告未完成。只在真实forward_backward和optim_step futures返回后写应答，刷新sampler并保存24小时TTL checkpoint。有效rollout和完整解题分别记录。

独立 replay：

真实recipe也支持 `--field-feedback`，该选项会记录在training.json并传入每个独立Episode。评估其作用时固定模型/数据/采样配置，仅在同一修复版间切换此选项；不要从旧4B与新9B调试结果推断它的效果。

`--no-condition-feedback` 可在保留字段覆盖反馈时单独关闭公开年份提示。单rollout评估可使用 `--seed 20261010`，每次采样传入seed加请求序号；训练模式拒绝此选项，以保留组内独立采样。上下文压缩/年份提示三组对照见 docs/archive/ablation_protocol.md。

```powershell
.venv/Scripts/python.exe -m elt_tinker.replay --public data/official/shipping/public --hidden data/official/shipping/hidden --pipeline runs/official_rollout_01/batch_0/episode_0/pipeline.json --operator-config infra/source_config.example.json --output runs/replay_01
```

SDK 0.32的Datum/TensorData使用dataclass；本项目显式保存数值数据、dtype和shape。若在优化器调用前失败，可使用 `--resume-from runs/tinker_step_01 --output runs/tinker_step_recovery_01` 与相同型号/任务/预算账本复用已保存的整组真实轨迹。无需重新采样；保留原采样logprobs，用importance_sampling校正新初始learner与采样policy的差异。独占恢复receipt禁止重复使用同一预算预留；已有optimizer应答时拒绝恢复。不要把此机制用于重复梯度更新或绕过费用上限。

## 设计与证据

完整阅读顺序见[文档索引](README.md)。当前设计见[implementation design](implementation_design.md)，安全与效率边界见[verification](verification.md)，历史修改与实验过程见[归档记录](archive/change_and_experiment_log.md)。

TASK_STATE.md 是仅本地保存的恢复记录，不随 Git 发布。实现仓库排除凭据、原始通信、材料快照、下载数据、运行日志和个人记录。Google Doc、公开访问及向教授提交由用户决定。

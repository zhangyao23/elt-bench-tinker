# ELT-Bench + Tinker RLVR starter

面向 ELT-Bench 的有状态工具环境、执行评分与 Tinker RL recipe。固定官方 shipping task 为首个目标，DuckDB 用于无需凭据测试，Snowflake 用于待运行的官方验收。原生源 clients 替代 Airbyte 控制面；该选择与教授任务原文相容，但不声称复现论文中的 Airbyte/Terraform/dbt 代理成绩。

## 当前验收边界

| 验收 | 状态 |
|---|---|
| 无凭据 integration | CSV/DuckDB/错误修复/独立进程重放已通过；见 docs/verification.md |
| 官方带凭据 rollout | 未运行；Snowflake 实际连接与权限仍待核验 |
| 真实 Tinker RL optimization | 未运行；recipe 已实现，尚无实际优化或 checkpoint 证据 |

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

requirements-training.lock 记录已验证的 SDK/客户端依赖；torch 为客户端数据处理，不做本地模型训练。Qwen3.5-4B 公开 tokenizer 会按 cookbook 路径下载；首次测试需要联网。

真实官方 rollout 默认建议 Qwen/Qwen3.5-4B，仍须以账号 capabilities 为准；不自动换模型。先由操作员配置 `TINKER_API_KEY`、`SNOWFLAKE_ACCOUNT/USER/DATABASE/WAREHOUSE/ROLE/AGENT_ROLE` 及 password 或 private_key_file。密钥只在私密环境/文件中设置，不粘贴聊天或提交 Git。参考 infra/snowflake_setup.sql 准备专用测试 DB 与两种角色，禁止 ELT_AGENT 继承 ELT_LOADER 的权限；环境关闭 secondary roles。

资源和预算明确后才运行下列命令，示例数字不是已获授权费用：

```powershell
.venv/Scripts/python.exe -m elt_tinker.recipe --mode rollout --model Qwen/Qwen3.5-4B --public data/official/shipping/public --hidden data/official/shipping/hidden --destination snowflake --operator-config infra/source_config.example.json --output runs/official_rollout_01 --max-cost-usd 1
.venv/Scripts/python.exe -m elt_tinker.recipe --mode train --model Qwen/Qwen3.5-4B --public data/official/shipping/public --hidden data/official/shipping/hidden --destination snowflake --operator-config infra/source_config.example.json --output runs/tinker_step_01 --steps 1 --group-size 4 --max-batches 3 --max-turns 12 --context-tokens 16384 --max-cost-usd 5
```

recipe 获取公开 rate card，对全部允许调用按最坏上下文/生成/训练 token 预算估计；超过 max-cost-usd 则在创建 Tinker ServiceClient 前退出。这是保守 token 预算，不是服务端账单硬限额，warehouse/storage/tax 单独核算。服务故障使整组排除；恒定奖励跳过更新，有限批次耗尽后报告失败。只在真实 forward_backward 和 optim_step futures 返回后写 optimization_acknowledgement.json，随后刷新 sampler 并保存 checkpoint。

独立 replay：

```powershell
.venv/Scripts/python.exe -m elt_tinker.replay --public data/official/shipping/public --hidden data/official/shipping/hidden --pipeline runs/official_rollout_01/batch_0/episode_0/pipeline.json --operator-config infra/source_config.example.json --output runs/replay_01
```

## 设计与证据

- docs/guide_steps_1_2.md：原理、官方 shipping、状态/工具、论文差异、reward 与 GRPO。
- docs/implementation_design.md：提交用简洁设计草稿。
- docs/verification.md：回归、安全边界、性能与待验证限制。
- TASK_STATE.md：仅本地保存的工作状态与恢复记录，不上传 GitHub；仓库验收说明见 docs/verification.md。
- THIRD_PARTY.md：固定版本来源、许可证与贡献边界。

仓库包含实现、测试与文档。凭据、原始通信、材料快照、下载数据、运行日志和个人状态记录均不纳入版本控制。Google Doc、公开仓库及向教授提交留待用户决定。

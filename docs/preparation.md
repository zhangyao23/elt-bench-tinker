# 接续真实验收：准备工作

先准备本地源服务，再接入 Snowflake 与 Tinker；身份核验不等于模型 rollout/优化。运行前明确可用额度和费用上限；本仓库不附带账号、凭据或消费授权。

## 本地运行路线

默认 WSL 已恢复为 Ubuntu-22.04-Recovered。源容器继续使用 PyRep-Builder-2204 中已有的 Podman 3.4.4（rootful）。只使用 elt-tinker-shipping-* 名称和 elt.project label，不停止或删除其它项目容器。源服务重启后先检查状态；只有数据缺失时才重新初始化，不需要每次都运行 seed_sources。

```powershell
.venv/Scripts/python.exe scripts/source_services.py start
.venv/Scripts/python.exe scripts/source_services.py status
# 单独终端提供官方 truck API
.venv/Scripts/python.exe infra/source_api.py
.venv/Scripts/python.exe scripts/seed_sources.py
```

source_services 使用固定 compose 镜像/端口/环境变量，通过 wsl.exe 调 Podman；不用安装 Docker Desktop。成功证据是 seed_sources 验证 5 个 native reader 的内容指纹，而不是仅看到端口打开。进度以 TASK_STATE.md 为准。

暂停本项目容器：`.venv/Scripts/python.exe scripts/source_services.py stop`。它只停止，不删除已初始化数据。API 在其终端 Ctrl+C。

## Snowflake：注册、专用角色与 key pair

1. 若无实验室账号，在 [Snowflake trial](https://signup.snowflake.com/) 创建试用。官方说明无需支付信息；注册后确认试用仍有效，不必升级付费账号。界面需要你完成邮箱验证和个人信息。
   若界面允许选择，使用 Standard Edition 即可；本任务只需要普通表、SQL、角色与Python加载。云平台可选AWS，区域可用US West (Oregon)，这属于本项目建议而非教授要求。无需另外注册AWS账号。按真实身份填写姓名、学校和Student；普通试用不要选成Cortex Code产品的单独试用。
2. 首次克隆后，执行 `.venv/Scripts/python.exe scripts/prepare_keypair.py` 在本地生成自己的RSA key pair和公钥注册SQL；已有私钥会保留。私钥留在Git忽略且限制访问的secrets目录，不上传到Snowflake。
3. 登录 Snowsight，进入 Projects → Workspaces → My Workspace → + Add New → SQL File，选择 ACCOUNTADMIN。先执行 `infra/snowflake_setup.sql`，再执行本地生成的 `secrets/snowflake_register_public_key.sql`。前者创建专用DB、两种角色和最小XSMALL warehouse（初始suspended，auto_suspend=60）；后者建立ELT_SDK service user并注册公钥、授予项目角色。若自己的工作目录已有 `secrets/snowflake_setup_all.sql`，可一次执行这个合并文件代替前述两步。此时无需手动resume，不上传 `snowflake_private_key.p8`。
4. 从 Snowsight 的 account details 复制用于 driver 的 account identifier（通常 organization-account）；SDK username 使用 ELT_SDK。在本地 PowerShell 执行下面的配置命令，仅输入 account/user，不发送到聊天。

```powershell
powershell -NoProfile -File scripts/configure_credentials.ps1 -Action Snowflake
```

Snowflake SDK 支持 key-pair authentication；自动化不依赖交互 MFA 或管理员密码。将角色授予 service user，不将 ELT_LOADER 授予 ELT_AGENT；后者只能获得每条 episode 的 raw 读取和 model 写入权限。

官方依据：[trial](https://docs.snowflake.com/en/user-guide/admin-trial-account)、[Workspaces](https://docs.snowflake.com/en/user-guide/ui-snowsight/workspaces)、[account identifier](https://docs.snowflake.com/en/user-guide/admin-account-identifier)、[key-pair](https://docs.snowflake.com/en/user-guide/key-pair-auth)。当前用未加密 PKCS8 私钥并保护目录 ACL；如需要迁移设备或加密私钥，须同步修改 SDK 配置，不能直接复制 DPAPI 文件到另一用户。

## Tinker：注册、额度与 API key

1. 打开 [Tinker console](https://tinker.thinkingmachines.ai/)，注册/登录。
2. 在 Billing 确认可用 credits/费用来源。没有明确费用上限前只准备账号和 API key，不启动采样或训练；是否存在赠送额度以你的账号界面为准。
   注册账号、可用访问权限和可用余额分别确认。若界面需要审批或显示最低充值，先记录其要求；文档未保证每个新账号自动获得赠送余额，也未假定最低充值金额。充值金额与运行上限不是同一件事，须由操作员确定。
3. 创建 API key，在本地 PowerShell 的隐藏输入框保存：

```powershell
powershell -NoProfile -File scripts/configure_credentials.ps1 -Action Tinker
```

key 作为 SecureString 写入 secrets/cloud_credentials.clixml，使用当前 Windows 用户的 DPAPI 加密。不要把它粘贴到聊天、Markdown、SQL Worksheet 或 Git。官方依据：[Tinker Quickstart](https://tinker-docs.thinkingmachines.ai/tinker/quickstart/)。

也可自行在本地创建 `secrets/tinker_api_key.txt`，唯一一行填写完整key。该文件是明文，由Git忽略；需自行限制其文件权限。`-Action Load` 会读取有效的一行key到当前进程，并优先于之前保存的Tinker配置；空文件或占位符 `PASTE_TINKER_API_KEY_HERE` 不会覆盖已有配置。文件内容不会打印到日志，也不会自动发起云调用。

## 检查准备结果

在当前 PowerShell 中加载配置，然后执行只读核验：

```powershell
. ./scripts/configure_credentials.ps1 -Action Load
.venv/Scripts/python.exe scripts/check_resources.py --cloud
```

注意 Load 必须 dot-source 到当前 PowerShell，不能用另一个 powershell 子进程；否则环境变量在子进程退出时丢失。check_resources 只检查 Tinker capabilities 和 Snowflake SHOW 元数据，不创建训练客户端、不采样、不优化、不唤醒 warehouse。它只保存 boolean/类型/模型能力，不输出密钥。

准备好后先确定 Tinker token 预算和 Snowflake 使用额度，再运行真实模型 rollout 与优化。$5 示例不是已授权费用；最低充值和试用余额按当前账号核验。

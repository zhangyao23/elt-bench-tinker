param(
    [ValidateSet('Tinker', 'Snowflake', 'Load', 'Status')]
    [string]$Action = 'Status'
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$credentialDir = Join-Path $projectRoot 'secrets'
$credentialFile = Join-Path $credentialDir 'cloud_credentials.clixml'
$settings = @{}
if (Test-Path -LiteralPath $credentialFile) {
    $settings = Import-Clixml -LiteralPath $credentialFile
}
if ($Action -eq 'Load') {
    $tinkerKeyFile = Join-Path $credentialDir 'tinker_api_key.txt'
    if (Test-Path -LiteralPath $tinkerKeyFile) {
        $tinkerKey = [IO.File]::ReadAllText($tinkerKeyFile).Trim()
        if ($tinkerKey -and $tinkerKey -ne 'PASTE_TINKER_API_KEY_HERE') {
            if ($tinkerKey -match '\s') {
                throw 'tinker_api_key.txt 只能填写一行完整 API key，不能包含空格或说明文字。'
            }
            $settings['TINKER_API_KEY'] = ConvertTo-SecureString -String $tinkerKey -AsPlainText -Force
        }
        $tinkerKey = $null
    }
}
if ($Action -eq 'Tinker') {
    $settings['TINKER_API_KEY'] = Read-Host '在本地输入 Tinker API key（输入不会显示）' -AsSecureString
}
if ($Action -eq 'Snowflake') {
    foreach ($field in @('ACCOUNT', 'USER')) {
        $settings["SNOWFLAKE_$field"] = (Read-Host "Snowflake $field").Trim()
    }
    if ($settings['SNOWFLAKE_ACCOUNT'] -notmatch '^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$') {
        throw 'ACCOUNT 应填写 Account identifier（通常组织名-账号名），不能填写邮箱、完整网址或说明文字。原配置未覆盖。'
    }
    $settings['SNOWFLAKE_DATABASE'] = 'ELT_SANDBOX'
    $settings['SNOWFLAKE_WAREHOUSE'] = 'ELT_TEST_WH'
    $settings['SNOWFLAKE_ROLE'] = 'ELT_LOADER'
    $settings['SNOWFLAKE_AGENT_ROLE'] = 'ELT_AGENT'
    $settings['SNOWFLAKE_PRIVATE_KEY_FILE'] = Join-Path $credentialDir 'snowflake_private_key.p8'
    if (-not (Test-Path -LiteralPath $settings['SNOWFLAKE_PRIVATE_KEY_FILE'])) {
        throw '先运行 scripts/prepare_keypair.py；密钥文件尚未生成。'
    }
}
if ($Action -in @('Tinker', 'Snowflake')) {
    New-Item -ItemType Directory -Force -Path $credentialDir | Out-Null
    $settings | Export-Clixml -LiteralPath $credentialFile
    Write-Host '已保存到 Git 忽略的本地配置。SecureString 使用当前 Windows 用户的 DPAPI 加密。'
}
if ($Action -eq 'Load') {
    foreach ($field in $settings.Keys) {
        $value = $settings[$field]
        if ($value -is [Security.SecureString]) {
            $value = [Net.NetworkCredential]::new('', $value).Password
        }
        [Environment]::SetEnvironmentVariable($field, [string]$value, 'Process')
    }
    Write-Host '已加载到当前 PowerShell 进程；关闭窗口即结束。未调用云服务。'
}
if ($Action -eq 'Status') {
    foreach ($field in @('TINKER_API_KEY', 'SNOWFLAKE_ACCOUNT', 'SNOWFLAKE_USER', 'SNOWFLAKE_PRIVATE_KEY_FILE')) {
        [PSCustomObject]@{Setting=$field; Saved=$settings.ContainsKey($field)}
    }
}

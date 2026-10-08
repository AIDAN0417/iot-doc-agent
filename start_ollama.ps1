# 本机启动入口；支持官方安装或 IOT_OLLAMA_EXE 指定的官方便携版。
$ErrorActionPreference = 'Stop'
$taskOllama = $env:IOT_OLLAMA_EXE
if (-not $taskOllama) {
    $taskCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if ($taskCommand) { $taskOllama = $taskCommand.Source }
}
if (-not $taskOllama -or -not (Test-Path -LiteralPath $taskOllama)) {
    throw '找不到 Ollama，请安装官方版本或设置 IOT_OLLAMA_EXE 为 ollama.exe 的路径。'
}
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = Join-Path $PSScriptRoot 'data\models\ollama'
New-Item -ItemType Directory -Path $env:OLLAMA_MODELS -Force | Out-Null
& $taskOllama serve

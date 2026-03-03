Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venvPy = ".\.venv\Scripts\python.exe"
if (!(Test-Path $venvPy)) {
  $pyCmd = "py"
  if (-not (Get-Command $pyCmd -ErrorAction SilentlyContinue)) { $pyCmd = "python" }
  & $pyCmd -3 -m venv .venv
  & $venvPy -m pip install -r requirements.txt
}
$provider = $null
if ($env:DEEPSEEK_API_KEY -and $env:DEEPSEEK_API_KEY -ne "") { $provider = "deepseek" }
elseif ($env:GEMINI_API_KEY -and $env:GEMINI_API_KEY -ne "") { $provider = "gemini" }
if (-not $provider) {
  $choice = Read-Host "选择 Provider (deepseek/gemini)"
  if ($choice -and $choice.ToLower() -eq "gemini") {
    if (-not $env:GEMINI_API_KEY -or $env:GEMINI_API_KEY -eq "") {
      $gk = Read-Host "输入 GEMINI_API_KEY"
      if ($gk) { $env:GEMINI_API_KEY = $gk }
    }
    $provider = "gemini"
  } else {
    if (-not $env:DEEPSEEK_API_KEY -or $env:DEEPSEEK_API_KEY -eq "") {
      $dk = Read-Host "输入 DEEPSEEK_API_KEY"
      if ($dk) { $env:DEEPSEEK_API_KEY = $dk }
    }
    $provider = "deepseek"
  }
}
if (-not $env:DEEPSEEK_BASE_URL -or $env:DEEPSEEK_BASE_URL -eq "") { $env:DEEPSEEK_BASE_URL = "https://api.deepseek.com" }
if (-not $env:GEMINI_ENDPOINT -or $env:GEMINI_ENDPOINT -eq "") { $env:GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models" }
if ($env:GATEWAY_PORT -and $env:GATEWAY_PORT -ne "") {
    $port = [int]$env:GATEWAY_PORT
} else {
    $port = 8765
}
# Remove auto-switch port logic to avoid mismatch with client
# try {
#   $conn = Test-NetConnection -ComputerName 127.0.0.1 -Port $port -WarningAction SilentlyContinue
#   if ($conn.TcpTestSucceeded) { $port = 8787 }
# } catch { }
Write-Host ("启动网关: http://127.0.0.1:{0}  | Provider: {1}" -f $port, $provider)
& $venvPy -m uvicorn server:app --host 127.0.0.1 --port $port

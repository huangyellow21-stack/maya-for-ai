Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venvPy = ".\.venv\Scripts\python.exe"

if (!(Test-Path $venvPy)) {
  $pyCmd = "py"
  $pyArgs = @("-3", "-m", "venv", ".venv")
  
  if (-not (Get-Command $pyCmd -ErrorAction SilentlyContinue)) { 
      $pyCmd = "python"
      $pyArgs = @("-m", "venv", ".venv")
  }
  
  Write-Host "Creating virtual environment using $pyCmd..."
  & $pyCmd $pyArgs
  
  if (!(Test-Path $venvPy)) {
      Write-Error "Failed to create virtual environment. Could not find $venvPy after creation."
      Exit 1
  }
  
  Write-Host "Installing requirements..."
  & $venvPy -m pip install -r requirements.txt
  if ($LASTEXITCODE -ne 0) {
      Write-Error "Failed to install requirements."
      Exit 1
  }
}

$provider = "deepseek" # Default fallback provider if not set in environment
if ($env:DEEPSEEK_API_KEY -and $env:DEEPSEEK_API_KEY -ne "") { $provider = "deepseek" }
elseif ($env:GEMINI_API_KEY -and $env:GEMINI_API_KEY -ne "") { $provider = "gemini" }

if (-not $env:DEEPSEEK_BASE_URL -or $env:DEEPSEEK_BASE_URL -eq "") { $env:DEEPSEEK_BASE_URL = "https://api.deepseek.com" }
if (-not $env:GEMINI_ENDPOINT -or $env:GEMINI_ENDPOINT -eq "") { $env:GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models" }
if ($env:GATEWAY_PORT -and $env:GATEWAY_PORT -ne "") {
    $port = [int]$env:GATEWAY_PORT
} else {
    $port = 8765
}

Write-Host ("启动网关: http://127.0.0.1:{0}  | Provider: {1}" -f $port, $provider)
& $venvPy -m uvicorn server:app --host 127.0.0.1 --port $port

if ($LASTEXITCODE -ne 0) {
    Write-Error "uvicorn 服务异常退出，退出码: $LASTEXITCODE"
    Exit $LASTEXITCODE
}

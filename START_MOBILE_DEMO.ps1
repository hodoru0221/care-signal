$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$nodeDir = "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$pnpmDir = "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback"

if (-not (Test-Path $pythonExe)) {
    throw "Codex Python runtime을 찾을 수 없습니다. README의 수동 실행 방법을 이용해 주세요."
}

$serverCommand = "Set-Location '$projectDir'; & '$pythonExe' -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
$simulatorCommand = "Set-Location '$projectDir'; Start-Sleep -Seconds 3; & '$pythonExe' -m backend.simulator --url http://127.0.0.1:8000"
$mobileCommand = "`$env:PATH='$nodeDir;$pnpmDir;'+`$env:PATH; Set-Location '$projectDir\mobile'; pnpm start --lan"

Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $serverCommand
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $simulatorCommand
Start-Process powershell.exe -ArgumentList "-NoExit", "-Command", $mobileCommand

Write-Host "테스트 프로그램 3개를 실행했습니다." -ForegroundColor Green
Write-Host "1. Windows 방화벽 질문이 나오면 개인 네트워크 액세스를 허용하세요."
Write-Host "2. 휴대폰과 PC를 같은 네트워크에 연결하세요."
Write-Host "3. 휴대폰 Expo Go 앱으로 모바일 창의 QR 코드를 스캔하세요."
Write-Host "4. 앱 서버 주소: http://172.30.1.68:8000"
Write-Host "5. 환자 연결 코드: CARE-101"

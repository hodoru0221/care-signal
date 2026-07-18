$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mobileDir = Join-Path $projectDir "mobile"
$nodeDir = "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin"
$pnpmDir = "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback"
$env:PATH = "$nodeDir;$pnpmDir;$env:PATH"
$env:CI = "false"

Set-Location $mobileDir
Write-Host "1/2 Expo 로그인: 사용자 이름은 hodoru0221입니다." -ForegroundColor Cyan
pnpm dlx eas-cli@21.0.1 login
if ($LASTEXITCODE -ne 0) { throw "Expo 로그인에 실패했습니다." }

Write-Host "2/2 기존 care-signal 프로젝트 연결" -ForegroundColor Cyan
pnpm dlx eas-cli@21.0.1 project:init
if ($LASTEXITCODE -ne 0) { throw "Expo 프로젝트 연결에 실패했습니다." }

Write-Host "연결 완료. 이 창의 Project ID가 포함된 마지막 부분을 확인하세요." -ForegroundColor Green
pnpm dlx eas-cli@21.0.1 project:info
Read-Host "Enter를 누르면 종료합니다"

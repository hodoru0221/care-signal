@echo off
setlocal
set "PATH=C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;%PATH%"
pushd "%~dp0mobile"

echo Step 1 of 3: Sign in to Expo
echo Expo username: hodoru0221
call "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" dlx eas-cli@21.0.1 login
if errorlevel 1 goto error

echo.
echo Step 2 of 3: Link the existing care-signal project
call "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" dlx eas-cli@21.0.1 project:init
if errorlevel 1 goto error

echo.
echo Step 3 of 3: Show linked project information
call "C:\Users\Direct\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd" dlx eas-cli@21.0.1 project:info
if errorlevel 1 goto error

echo.
echo Expo project linked successfully.
popd
pause
exit /b 0

:error
echo.
echo The command failed. Please capture this window.
popd
pause
exit /b 1

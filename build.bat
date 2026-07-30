@echo off
setlocal enabledelayedexpansion

set PROJECT_NAME=cacherc2
for /f %%A in ('git describe --tags --always 2^>nul') do set VERSION=%%A
if "%VERSION%"=="" set VERSION=unknown

echo Building %PROJECT_NAME%
echo Version: %VERSION%
echo.

if not exist bin mkdir bin

echo Building for Windows x64...
set GOOS=windows
set GOARCH=amd64
go build -ldflags="-s -w -H windowsgui" -o "bin\%PROJECT_NAME%-windows-x64.exe" .\cmd\client
if errorlevel 1 (
    echo Error building Windows version
    exit /b 1
)

echo Build completed successfully!
echo.
echo Binaries:
dir /s /b bin\
echo.
echo Done!

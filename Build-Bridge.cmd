@echo off
setlocal EnableExtensions
set "ROOT=%~dp0"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo ERROR: Visual Studio Build Tools / vswhere.exe not found.
  exit /b 1
)
set "VS_PATH="
for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VS_PATH=%%I"
if not defined VS_PATH (
  echo ERROR: MSVC C++ build tools not found.
  exit /b 1
)
call "%VS_PATH%\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64 >nul
if errorlevel 1 exit /b 1
pushd "%ROOT%"
cl /nologo /std:c++17 /EHsc /O2 /MD /DUNICODE /D_UNICODE "BambuNetworkBridge.cpp" /Fe:"BambuNetworkBridge.exe"
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

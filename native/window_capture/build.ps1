# Build the Windows SDK capture bridge; no third-party SDK or runtime installation.
$ErrorActionPreference = 'Stop'
$captureVsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$env:Path = "$(Split-Path $captureVsWhere -Parent);$env:SystemRoot\System32;$env:SystemRoot;$env:Path"
if (-not (Test-Path -LiteralPath $captureVsWhere)) { throw 'Visual Studio C++ build tools are required.' }
$captureVsPath = & $captureVsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $captureVsPath) { throw 'Visual Studio x64 C++ tools were not found.' }
& (Join-Path $captureVsPath 'Common7\Tools\Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
$captureRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$captureBuild = Join-Path $captureRoot 'build\window_capture'
New-Item -ItemType Directory -Path $captureBuild -Force | Out-Null
$captureDll = Join-Path $PSScriptRoot 'WindowCapture_x64.dll'
Push-Location $captureBuild
try {
    & cl.exe /nologo /std:c++17 /O2 /GL /EHsc /MT /DNDEBUG /utf-8 /W4 /Brepro /LD `
        (Join-Path $PSScriptRoot 'native.cpp') "/Fe:$captureDll" /link /INCREMENTAL:NO /OPT:REF /OPT:ICF /Brepro `
        d3d11.lib windowsapp.lib user32.lib "/IMPLIB:$(Join-Path $captureBuild 'WindowCapture_x64.lib')"
    if ($LASTEXITCODE -ne 0) { throw "Window capture build failed: $LASTEXITCODE" }
    Get-Item -LiteralPath $captureDll | Select-Object FullName, Length
} finally { Pop-Location }

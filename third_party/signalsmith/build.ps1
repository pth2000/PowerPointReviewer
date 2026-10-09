# Rebuild the bundled x64 DLL with Visual Studio's C++ tools. No network required.
$ErrorActionPreference = 'Stop'
$signalsmithVsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
# The build may be launched by a helper with a minimal PATH.
$env:Path = "$(Split-Path $signalsmithVsWhere -Parent);$env:SystemRoot\System32;$env:SystemRoot;$env:Path"
if (-not (Test-Path -LiteralPath $signalsmithVsWhere)) {
    throw 'Visual Studio C++ build tools are required to rebuild this DLL.'
}
$signalsmithVsPath = & $signalsmithVsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $signalsmithVsPath) { throw 'Visual Studio x64 C++ tools were not found.' }
& (Join-Path $signalsmithVsPath 'Common7\Tools\Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
$signalsmithRepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$signalsmithBuildDir = Join-Path $signalsmithRepoRoot 'build\signalsmith'
New-Item -ItemType Directory -Path $signalsmithBuildDir -Force | Out-Null
$signalsmithDll = Join-Path $PSScriptRoot 'SignalsmithStretch_x64.dll'
Push-Location $signalsmithBuildDir
try {
    & cl.exe /nologo /std:c++17 /O2 /GL /EHsc /MT /DNDEBUG /utf-8 /W4 /external:W0 /Brepro `
        "/external:I$(Join-Path $PSScriptRoot 'include')" /LD (Join-Path $PSScriptRoot 'native.cpp') `
        "/Fe:$signalsmithDll" /link /INCREMENTAL:NO /OPT:REF /OPT:ICF /Brepro `
        "/IMPLIB:$(Join-Path $signalsmithBuildDir 'SignalsmithStretch_x64.lib')"
    if ($LASTEXITCODE -ne 0) { throw "Native build failed with exit code $LASTEXITCODE." }
    Get-Item -LiteralPath $signalsmithDll | Select-Object FullName, Length
    Write-Output "SHA256: $((Get-FileHash -LiteralPath $signalsmithDll -Algorithm SHA256).Hash)"
} finally {
    Pop-Location
}

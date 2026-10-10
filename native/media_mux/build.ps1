# Rebuild with Visual Studio C++ tools and the vendored FFmpeg public headers.
$ErrorActionPreference = 'Stop'
$muxVsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$env:Path = "$(Split-Path $muxVsWhere -Parent);$env:SystemRoot\System32;$env:SystemRoot;$env:Path"
if (-not (Test-Path -LiteralPath $muxVsWhere)) { throw 'Visual Studio C++ build tools are required.' }
$muxVsPath = & $muxVsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $muxVsPath) { throw 'Visual Studio x64 C++ tools were not found.' }
& (Join-Path $muxVsPath 'Common7\Tools\Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
$muxRepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$muxBuildDir = Join-Path $muxRepoRoot 'build\media_mux'
New-Item -ItemType Directory -Path $muxBuildDir -Force | Out-Null
$muxDll = Join-Path $PSScriptRoot 'MediaMux_x64.dll'
Push-Location $muxBuildDir
try {
    & cl.exe /nologo /std:c++17 /O2 /GL /EHsc /MT /DNDEBUG /utf-8 /W4 /external:W0 /Brepro `
        "/external:I$(Join-Path $muxRepoRoot 'third_party\ffmpeg\include')" /LD (Join-Path $PSScriptRoot 'native.cpp') `
        "/Fe:$muxDll" /link /INCREMENTAL:NO /OPT:REF /OPT:ICF /Brepro `
        "/IMPLIB:$(Join-Path $muxBuildDir 'MediaMux_x64.lib')"
    if ($LASTEXITCODE -ne 0) { throw "Media mux build failed with exit code $LASTEXITCODE." }
    Get-Item -LiteralPath $muxDll | Select-Object FullName, Length
} finally {
    Pop-Location
}

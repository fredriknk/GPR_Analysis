param(
    [Parameter(Mandatory = $true)][string]$Script,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$ScriptArguments
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vs = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'CUDA kernel compilation requires the MSVC C++ toolchain.' }
$vcvars = Join-Path $vs 'VC/Auxiliary/Build/vcvars64.bat'
$compilerEnvironment = & cmd.exe /d /c "call `"$vcvars`" >nul && set"
if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the MSVC environment.' }
foreach ($entry in $compilerEnvironment) {
    if ($entry -match '^([^=]+)=(.*)$') { Set-Item -LiteralPath "Env:$($Matches[1])" -Value $Matches[2] }
}
$env:PYTHONUTF8 = '1'
$python = Join-Path $repo '.spikes/gprmax4/Scripts/python.exe'
& $python -u $Script @ScriptArguments
exit $LASTEXITCODE

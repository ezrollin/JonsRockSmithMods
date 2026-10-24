param(
    [string]$ToolkitDirectory = 'C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\RocksmithToolkit'
)

$ErrorActionPreference = 'Stop'
$compiler = 'C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe'
$source = Join-Path $PSScriptRoot 'SplitPackHelper.cs'
$outputDirectory = Join-Path $PSScriptRoot 'bin'
$genericOutput = Join-Path $outputDirectory 'SplitPackHelper.exe'
$toolkitAssembly = Join-Path $ToolkitDirectory 'RocksmithToolkitLib.dll'
$jsonAssembly = Join-Path $ToolkitDirectory 'Newtonsoft.Json.dll'
foreach ($required in @($compiler, $source, $toolkitAssembly, $jsonAssembly)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing required file: $required" }
}
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
$architectureOutputs = @()
foreach ($architecture in @('x86', 'x64')) {
    $output = Join-Path $outputDirectory "SplitPackHelper-$architecture.exe"
    & $compiler /nologo /target:exe "/platform:$architecture" /optimize+ "/out:$output" "/reference:$toolkitAssembly" "/reference:$jsonAssembly" $source
    if ($LASTEXITCODE -ne 0) { throw "Split helper compile failed for ${architecture}: $LASTEXITCODE" }
    $architectureOutputs += $output
}
Copy-Item -LiteralPath (Join-Path $outputDirectory 'SplitPackHelper-x64.exe') -Destination $genericOutput -Force
Write-Output $genericOutput
$architectureOutputs | ForEach-Object { Write-Output $_ }

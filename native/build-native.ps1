param(
    [Parameter(Mandatory = $true)]
    [string] $RsModsSource
)

$ErrorActionPreference = 'Stop'
$expectedRevision = '1e4a67d43dfd0bae2b613edc4cd67b922332dc61'
$nativeDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$sourceDirectory = (Resolve-Path -LiteralPath $RsModsSource).Path
$gitSafeSourceDirectory = $sourceDirectory.Replace('\', '/')
$sourceRevision = (& git -c "safe.directory=$gitSafeSourceDirectory" -C $sourceDirectory rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceRevision -ne $expectedRevision) {
    throw "RSMods source must be at $expectedRevision; found $sourceRevision"
}

$msbuild = 'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe'
if (-not (Test-Path -LiteralPath $msbuild)) {
    throw "MSBuild not found: $msbuild"
}

$buildDirectory = Join-Path $nativeDirectory ('builds\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $buildDirectory) | Out-Null
& git -c "safe.directory=$gitSafeSourceDirectory" clone --quiet --no-hardlinks $sourceDirectory $buildDirectory
if ($LASTEXITCODE -ne 0) { throw 'Could not create isolated RSMods build checkout.' }

& git -C $buildDirectory checkout --quiet --detach $expectedRevision
if ($LASTEXITCODE -ne 0) { throw 'Could not select pinned RSMods revision.' }

$patchFile = Join-Path $nativeDirectory 'jons-fixed-song-speed.patch'
& git -C $buildDirectory apply --check $patchFile
if ($LASTEXITCODE -ne 0) { throw 'Native patch does not apply cleanly.' }
& git -C $buildDirectory apply $patchFile
if ($LASTEXITCODE -ne 0) { throw 'Native patch application failed.' }

$project = Join-Path $buildDirectory 'DLL\DLL.vcxproj'
& $msbuild $project /t:Build /p:Configuration=Release /p:Platform=Win32 /p:PostBuildEventUseInBuild=false /m:4 /verbosity:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw 'Native DLL build failed.' }

$artifact = Join-Path $buildDirectory 'DLL\Installer\Resources\xinput1_3.dll'
if (-not (Test-Path -LiteralPath $artifact)) { throw "DLL missing after build: $artifact" }
$installableDirectory = Join-Path $nativeDirectory 'artifacts'
New-Item -ItemType Directory -Force -Path $installableDirectory | Out-Null
$installableArtifact = Join-Path $installableDirectory 'xinput1_3.dll'
Copy-Item -LiteralPath $artifact -Destination $installableArtifact -Force
$builtHash = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash
$installableHash = (Get-FileHash -LiteralPath $installableArtifact -Algorithm SHA256).Hash
if ($builtHash -ne $installableHash) { throw 'Installable native artifact failed SHA256 verification.' }
Write-Output "Built isolated DLL: $artifact"
Write-Output "Installable artifact: $installableArtifact"
Write-Output "SHA256: $builtHash"

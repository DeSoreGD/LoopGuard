param(
    [switch]$Clean,
    [switch]$Installer
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Remove-ReleaseArtifact {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelativePath
    )

    $targetPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $RelativePath))
    $rootPrefix = $repoRoot
    if (-not $rootPrefix.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $rootPrefix += [System.IO.Path]::DirectorySeparatorChar
    }

    if (-not $targetPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove path outside repo: $targetPath"
    }

    if (Test-Path -LiteralPath $targetPath) {
        Remove-Item -LiteralPath $targetPath -Recurse -Force
    }
}

function Find-InnoSetupCompiler {
    $checkedPaths = New-Object System.Collections.Generic.List[string]

    $pathCommand = Get-Command "iscc.exe" -ErrorAction SilentlyContinue
    $checkedPaths.Add("PATH:iscc.exe")
    if ($null -ne $pathCommand) {
        return @{
            Path = $pathCommand.Source
            Checked = $checkedPaths
        }
    }

    $candidatePaths = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $candidatePaths.Add(
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidatePaths.Add(
            (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidatePaths.Add(
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
        )
    }

    foreach ($candidatePath in $candidatePaths) {
        $checkedPaths.Add($candidatePath)
        if (Test-Path -LiteralPath $candidatePath) {
            return @{
                Path = $candidatePath
                Checked = $checkedPaths
            }
        }
    }

    return @{
        Path = $null
        Checked = $checkedPaths
    }
}

Push-Location $repoRoot
try {
    if ($Clean) {
        Remove-ReleaseArtifact "dist\SelfBoss"
        Remove-ReleaseArtifact "dist\LoopGuard"
        Remove-ReleaseArtifact "dist\installer"
        Remove-ReleaseArtifact "build\SelfBoss"
        Remove-ReleaseArtifact "build\LoopGuard"
    }

    $env:PYTHONPATH = Join-Path $repoRoot "src"
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    $pyInstallerArgs = @(
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--workpath",
        "build\LoopGuard",
        "packaging\selfboss.spec"
    )
    if (Test-Path $venvPython) {
        & $venvPython @pyInstallerArgs
    }
    else {
        python @pyInstallerArgs
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    if ($Installer) {
        $loopGuardExe = Join-Path $repoRoot "dist\LoopGuard\LoopGuard.exe"
        if (-not (Test-Path -LiteralPath $loopGuardExe)) {
            throw "PyInstaller artifact missing: $loopGuardExe"
        }
        $nativeHostExe = Join-Path $repoRoot "dist\LoopGuard\LoopGuardNativeHost.exe"
        if (-not (Test-Path -LiteralPath $nativeHostExe)) {
            throw "PyInstaller native host artifact missing: $nativeHostExe"
        }

        $compiler = Find-InnoSetupCompiler
        if (-not $compiler.Path) {
            $checked = [string]::Join([Environment]::NewLine, $compiler.Checked)
            throw @"
ISCC not found. Install Inno Setup 6 or add iscc.exe to PATH, then rerun:
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Clean -Installer

Checked paths:
$checked
"@
        }

        & $compiler.Path "packaging\installer\LoopGuard.iss"
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    exit $LASTEXITCODE
}
finally {
    Pop-Location
}

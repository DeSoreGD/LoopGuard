param(
    [string]$OutputPath = "dist\extension\LoopGuardChromeExtension.zip"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$extensionRoot = Join-Path $repoRoot "browser_extension\chrome_mv3"
$resolvedOutputPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $OutputPath))
$repoPrefix = $repoRoot
if (-not $repoPrefix.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
    $repoPrefix += [System.IO.Path]::DirectorySeparatorChar
}

if (-not $resolvedOutputPath.StartsWith($repoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to write extension package outside repo: $resolvedOutputPath"
}

$requiredFiles = @(
    "manifest.json",
    "background.js",
    "blocked.html",
    "blocked.js",
    "content_scripts\youtube_spa.js"
)

foreach ($relativePath in $requiredFiles) {
    $candidate = Join-Path $extensionRoot $relativePath
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Required extension file missing: $candidate"
    }
}

$stagingRoot = Join-Path $repoRoot "dist\extension\_chrome_mv3_zip_staging"
if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingRoot | Out-Null

$excludedDirectoryNames = @(".git", "__pycache__", "tests", "test", "tmp", "temp")
$excludedFilePatterns = @(
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.bak",
    "LoopGuard.exe",
    "LoopGuardNativeHost.exe"
)

Get-ChildItem -LiteralPath $extensionRoot -Recurse -File | ForEach-Object {
    $relativePath = $_.FullName.Substring($extensionRoot.Length).TrimStart('\', '/')
    $pathParts = $relativePath -split '[\\/]'
    $skip = $false
    foreach ($part in $pathParts) {
        if ($excludedDirectoryNames -contains $part.ToLowerInvariant()) {
            $skip = $true
        }
    }
    foreach ($pattern in $excludedFilePatterns) {
        if ($_.Name -like $pattern) {
            $skip = $true
        }
    }
    if ($skip) {
        return
    }
    $destination = Join-Path $stagingRoot $relativePath
    $destinationParent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $destinationParent)) {
        New-Item -ItemType Directory -Path $destinationParent | Out-Null
    }
    Copy-Item -LiteralPath $_.FullName -Destination $destination
}

$outputParent = Split-Path -Parent $resolvedOutputPath
if (-not (Test-Path -LiteralPath $outputParent)) {
    New-Item -ItemType Directory -Path $outputParent | Out-Null
}
if (Test-Path -LiteralPath $resolvedOutputPath) {
    Remove-Item -LiteralPath $resolvedOutputPath -Force
}

Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $resolvedOutputPath -Force
Remove-Item -LiteralPath $stagingRoot -Recurse -Force

Write-Host "Created Chrome extension package: $resolvedOutputPath"

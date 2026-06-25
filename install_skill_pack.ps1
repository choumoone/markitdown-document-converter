[CmdletBinding()]
param(
    [string]$CodexHome = (Join-Path $HOME ".codex")
)

$ErrorActionPreference = "Stop"
$skillsHome = Join-Path $CodexHome "skills"
$coreDestination = Join-Path $skillsHome "markitdown-document-converter"

function Copy-SkillTree {
    param(
        [Parameter(Mandatory)] [string]$Source,
        [Parameter(Mandatory)] [string]$Destination
    )

    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $skillsHome -Force | Out-Null
New-Item -ItemType Directory -Path $coreDestination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "SKILL.md") -Destination $coreDestination -Force
foreach ($folder in @("agents", "references", "scripts", "templates", "tests")) {
    $source = Join-Path $PSScriptRoot $folder
    if (Test-Path -LiteralPath $source) {
        Copy-SkillTree -Source $source -Destination (Join-Path $coreDestination $folder)
    }
}

$specialistRoot = Join-Path $PSScriptRoot "skills"
Get-ChildItem -LiteralPath $specialistRoot -Directory | ForEach-Object {
    Copy-SkillTree -Source $_.FullName -Destination (Join-Path $skillsHome $_.Name)
}

Write-Host "Installed MarkItDown skill pack to $skillsHome"

$ErrorActionPreference = "Stop"

$secretDir = Join-Path $HOME ".codex\secrets"
$minimaxPath = Join-Path $secretDir "minimax.env"
$markitdownPath = Join-Path $secretDir "markitdown-document-converter.env"
$apiKey = Get-Clipboard

if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "Clipboard is empty. Copy the MiniMax API key first."
}

New-Item -ItemType Directory -Force -Path $secretDir | Out-Null

$lines = @(
    "MINIMAX_API_KEY=$apiKey"
    "OPENAI_API_KEY=$apiKey"
    "OPENAI_BASE_URL=https://api.minimaxi.com/v1"
    "MARKITDOWN_OCR_MODEL=MiniMax-M3"
)

$lines | Set-Content -LiteralPath $minimaxPath -Encoding UTF8
$lines | Set-Content -LiteralPath $markitdownPath -Encoding UTF8

Write-Host "Saved MiniMax OCR environment to $minimaxPath"
Write-Host "Saved MarkItDown OCR environment to $markitdownPath"

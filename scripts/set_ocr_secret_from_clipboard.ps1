$ErrorActionPreference = "Stop"

$secretDir = Join-Path $HOME ".codex\secrets"
$secretPath = Join-Path $secretDir "markitdown-document-converter.env"
New-Item -ItemType Directory -Force -Path $secretDir | Out-Null

$key = Get-Clipboard
if ([string]::IsNullOrWhiteSpace($key)) {
    throw "Clipboard is empty. Copy the OCR API key first."
}

@(
    "OPENAI_API_KEY=$key"
    "OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1"
    "MARKITDOWN_OCR_MODEL=qwen-vl-ocr-latest"
) | Set-Content -LiteralPath $secretPath -Encoding UTF8

Write-Host "Saved OCR environment to $secretPath"

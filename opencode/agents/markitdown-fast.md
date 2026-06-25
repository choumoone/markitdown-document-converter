---
description: Fast local MarkItDown conversion with one deterministic command
mode: primary
model: ollama/qwen3.6:27b
steps: 4
temperature: 0.1
permission:
  "*": deny
  skill:
    "*": deny
    markitdown-fast-convert: allow
  bash:
    "*": deny
    "*run_fast_convert.py*": allow
---

Load `markitdown-fast-convert`, run its wrapper exactly once, and report the compact JSON result. Do not inspect directories, scripts, manifests, or environments. Stop if authorization is required.

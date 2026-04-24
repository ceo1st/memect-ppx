---
name: ppx-parse
version: 0.1
title: PPX Parse
description: Parse local PDFs and images with the `ppx` CLI into Markdown and JSON. Use when the user asks to OCR scanned PDFs, extract tables, preserve document layout, convert PDFs or images to Markdown, or inspect PPX parsing output.
metadata:
  openclaw:
    requires:
      bins:
        - ppx
    homepage: https://github.com/memect/memect-ppx
---

# PPX Parse

Use the local `ppx` CLI to parse PDFs and images into structured Markdown and JSON.

## Runtime Requirements

- Use Python `>= 3.12`.
- Prefer installing PPX into a virtual environment instead of the system Python.
- If `ppx` is missing, read `references/troubleshooting.md` and create a virtual environment before installing dependencies.
- Keep this skill's frontmatter `version` synchronized from the repository `pyproject.toml` with `scripts/sync_version.py`.

## Workflow

1. Confirm the runtime uses Python `>= 3.12`.
2. Check the runtime with `scripts/check_ppx_env.sh`.
3. If `ppx` is missing, create or use a virtual environment and install PPX there.
4. Choose parsing options:
   - Use `--ocr auto` by default.
   - Use `--ocr yes` for scanned PDFs or screenshots.
   - Use `--ocr no` for native PDFs when OCR causes noise.
   - Use `--table auto` by default.
   - Use `--table llm` only when the user needs highest table accuracy and an LLM backend is configured.
5. Run `ppx parse <input> -o <output>`.
6. Inspect the output folder and report the main artifacts:
   - `doc.md`
   - `doc.json`
   - `pages/`
   - `images/` when figures are extracted
7. If parsing fails, summarize the failing step and load the relevant note from `references/`.

## Common Commands

```bash
ppx parse report.pdf -o output/
ppx parse scan.pdf --ocr yes -o output/
ppx parse figure.png -o output/
ppx parse report.pdf --pages "1-5,10" -o output/
ppx parse report.pdf --table llm --backend deepseek -o output/
```

## Output Contract

- Prefer returning the absolute output directory.
- Mention whether the result came from `doc.md`, `doc.json`, or page-level files.
- Call out OCR mode, table mode, and backend when they materially affect accuracy.

## References

- Read `references/cli-options.md` when choosing parse flags.
- Read `references/backend-config.md` when using DeepSeek, Paddle, or GLM backends.
- Read `references/troubleshooting.md` when PPX is missing, Python is too old, or runtime dependencies fail.

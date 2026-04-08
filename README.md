# bare_act_pdf2html

Convert an Indian Bare Act PDF into a structured JSON document that follows the schema pattern in `act_model.json`.

## What this script does

`pdf_to_act_json.py` reads text from each PDF page, normalizes lines, and builds a JSON payload with:

- top-level act metadata (title, year, act number, enactment date)
- preamble content
- body structure (`PART`, `CHAPTER`, `Section` hierarchy)
- uncategorized sections
- schedules (if present)
- raw text and simple HTML-rendered text for content blocks

### Parser hardening for mixed Bare Act formats

The converter includes additional heuristics to better handle variations across scans and layouts:

- repeated page headers/footers are auto-removed when detected across pages
- `ARRANGEMENT OF SECTIONS` / `CONTENTS` blocks are ignored so the real body is parsed once
- hierarchy recognition supports `PART`, `CHAPTER`, `SUBPART`, and `TITLE`
- section-start detection accepts common number styles (`12`, `12.`, `12)`, `12-A` style leading tokens)
- nearby all-caps lines are used as fallback names for parts/chapters where headings are split across lines

These changes keep the output in the same JSON shape while improving structural fidelity.

## Prerequisites

- Python 3.9+ (recommended)
- `pip`
- Python package: `pypdf`

Install dependency:

```bash
pip install pypdf
```

## Files in this repository

- `pdf_to_act_json.py` — conversion script
- `act_model.json` — target schema reference
- `2435_a1961-43.pdf` — sample input PDF

## Execution steps

### 1) (Optional but recommended) Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2) Install dependencies

```bash
pip install pypdf
```

### 3) Run the converter

Basic usage:

```bash
python pdf_to_act_json.py 2435_a1961-43.pdf
```

This writes output to `2435_a1961-43.json` by default.

### 4) Run with explicit output file

```bash
python pdf_to_act_json.py 2435_a1961-43.pdf --output output.json
```

### 5) Control JSON indentation

```bash
python pdf_to_act_json.py 2435_a1961-43.pdf --output output.json --indent 2
```

## Command reference

```bash
python pdf_to_act_json.py <pdf_path> [--output <json_path>] [--indent <n>]
```

- `<pdf_path>`: input PDF file path (required)
- `--output`, `-o`: output JSON path (optional; defaults to `<pdf_stem>.json`)
- `--indent`: pretty-print indentation for JSON (default: `2`)

## Example with another PDF

```bash
python pdf_to_act_json.py /path/to/your_act.pdf --output /path/to/your_act.json
```

## Troubleshooting

### Error: `pypdf is required to read PDFs`

Install dependency:

```bash
pip install pypdf
```

### Output JSON looks sparse or sections are misplaced

The parser uses text heuristics (for `PART`, `CHAPTER`, and section-number patterns). PDFs with unusual formatting/OCR issues may require script tuning.

## Quick verification

After running the script, validate that JSON is readable:

```bash
python -m json.tool output.json > /dev/null && echo "JSON is valid"
```

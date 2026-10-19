# coding_agent

A lightweight repository-aware coding agent for identifying likely bug locations and applying a simple targeted fix.

## Included tools

- Repository search: finds files relevant to a plain-English bug description
- Bug locator: highlights likely lines in a file with context
- File replacement: swaps a matched code snippet for a corrected one

## Usage

```bash
uv run python -m coding_agent "divide by zero bug in sample math"
```

The project also includes a sample math file at [sample_math.py](sample_math.py) that intentionally contains no bug so you can inject one yourself.

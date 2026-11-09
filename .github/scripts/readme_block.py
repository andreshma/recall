"""Print a fenced code block of README.md marked with ``<!-- NAME -->``.

CI runs the README's install steps verbatim with it (job ``quickstart``):

    python .github/scripts/readme_block.py quickstart-install > quickstart.ps1

Exits 1 if the marker or its block is missing, so a README edit that drops the
block fails CI instead of silently testing nothing.
"""

import re
import sys
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"


def block(name: str, text: str) -> str | None:
    """Body of the first fenced block right after ``<!-- name -->``."""
    m = re.search(rf"<!-- {re.escape(name)} -->\s*```[a-z]*\n(.*?)```", text, re.S)
    return m.group(1) if m else None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: readme_block.py NAME", file=sys.stderr)
        return 2
    body = block(argv[0], README.read_text(encoding="utf-8"))
    if body is None:
        print(f"block {argv[0]!r} not found in README.md", file=sys.stderr)
        return 1
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

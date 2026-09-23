"""The CFC narrative prompts, kept as text files rather than Python string literals.

These are the client's own prompts (CFC, 2026-09-23), written by the methodology owner and
adopted verbatim. Keeping each one in its own file means the wording in the repository is
byte-for-byte what he issued -- a new revision is a file swap, and `git diff` shows his
changes as prose rather than as re-flowed Python escaping.

Anything the pipeline needs that his text does not define (the JSON envelope the answer is
returned in) is added by the module that loads it, never by editing his file.
"""
from pathlib import Path

_DIR = Path(__file__).parent


def load(name: str) -> str:
    """The prompt text of `<name>.txt`, stripped of a trailing newline."""
    return (_DIR / f"{name}.txt").read_text(encoding="utf-8").rstrip("\n")

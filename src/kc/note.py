"""Low-friction note intake: write a timestamped markdown file into inbox.

Shared by `kc note` (CLI) and `POST /note` (`kc serve`), so the feedback
loop has a single implementation. A note only becomes knowledge after
`compile`; this module never touches raw/ or the DB.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .config import Config


def write_note(cfg: Config, text: str, title: str | None = None) -> Path:
    """Write ``text`` to ``inbox/note-<ts>.md`` and return the new path.

    Raises ``ValueError`` when ``text`` is empty/blank. Collisions within the
    same second are disambiguated with a ``-N`` suffix (never overwrite).
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("note 内容不能为空")
    cfg.ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    heading = (title or "").strip() or "随手记"
    content = f"# {heading}\n\n{text}\n"
    dest = cfg.dir("inbox") / f"note-{ts}.md"
    n = 1
    while dest.exists():
        dest = cfg.dir("inbox") / f"note-{ts}-{n}.md"
        n += 1
    dest.write_text(content, encoding="utf-8")
    return dest

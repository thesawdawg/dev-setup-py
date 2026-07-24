from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

TRANSCRIPT_DIR = Path.home() / ".local" / "share" / "dev-setup" / "agent" / "transcripts"


class Transcript:
    """Append-only session record, for working out what the agent actually did.

    Written after every turn rather than at exit: a session that ends in Ctrl-C or
    a crash is exactly the one worth reading afterwards.
    """

    def __init__(self, path: Path, *, model: str, host: str, workspace: str) -> None:
        self.path = path
        self.meta = {
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": model,
            "host": host,
            "workspace": workspace,
        }
        self.turns: list[dict[str, Any]] = []

    @classmethod
    def create(cls, *, model: str, host: str, workspace: str) -> Transcript:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return cls(
            TRANSCRIPT_DIR / f"{stamp}.json", model=model, host=host, workspace=workspace
        )

    def record(self, messages: list[dict[str, Any]]) -> None:
        self.turns = [dict(m) for m in messages]
        self.flush()

    def flush(self) -> None:
        payload = {**self.meta, "messages": self.turns}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write-then-rename so a crash mid-write cannot leave a half-written
            # file where a readable one used to be.
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            # A transcript is a debugging aid. Losing it must never take the
            # session down with it.
            pass

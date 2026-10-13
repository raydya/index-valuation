"""Retain source responses by content hash for reproducibility and later review."""

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID


def json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Path, UUID)):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def json_text(value) -> str:
    return json.dumps(value, default=json_default, ensure_ascii=False, indent=2, allow_nan=False)


class Archive:
    def __init__(self, root: Path):
        self.root = root

    def save(self, source: str, content: bytes, suffix: str) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / source / f"{digest}.{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as output:
                output.write(content)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Corrupted source archive: {path}") from None
        return digest, str(path)

    def save_json(self, source: str, value) -> str:
        return self.save(source, json_text(value).encode("utf-8"), "json")[1]

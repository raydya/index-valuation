"""Durable per-index results for a single scheduled update batch."""

import json
import os
from pathlib import Path
from uuid import UUID

from index_valuation.archive import json_default
from index_valuation.domain import DataError


class ResultJournal:
    """Create a new journal and persist each completed result before continuing."""

    def __init__(self, path: Path, batch_id: UUID | str):
        self.batch_id = str(UUID(str(batch_id)))
        path.parent.mkdir(parents=True, exist_ok=True)
        self._output = path.open("x", encoding="utf-8")

    def append(self, report: dict) -> None:
        try:
            line = json.dumps(
                {"batch_id": self.batch_id, "report": report},
                default=json_default,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise DataError("任务结果无法序列化到批次记录") from error
        self._output.write(line + "\n")
        self._output.flush()
        os.fsync(self._output.fileno())

    def close(self) -> None:
        self._output.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def read_reports(path: Path, batch_id: UUID | str) -> list[dict]:
    """Read complete records for this batch; a killed writer may leave a final fragment.

    Missing files mean the child did not create its journal. Corrupt complete lines
    raise DataError rather than silently treating a broken journal as a successful
    batch. The caller is responsible for restricting the trusted journal path.
    """
    expected_batch = str(UUID(str(batch_id)))
    try:
        source = path.open("rb")
    except FileNotFoundError:
        return []
    reports = []
    with source:
        for line_number, line in enumerate(source, start=1):
            if not line.endswith(b"\n"):
                break
            try:
                entry = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise DataError(f"批次记录第 {line_number} 行不是有效 JSON") from error
            if not isinstance(entry, dict):
                raise DataError(f"批次记录第 {line_number} 行不是对象")
            if entry.get("batch_id") != expected_batch:
                continue
            report = entry.get("report")
            if not isinstance(report, dict) or not isinstance(report.get("index"), str):
                raise DataError(f"批次记录第 {line_number} 行缺少有效指数结果")
            reports.append(report)
    return reports

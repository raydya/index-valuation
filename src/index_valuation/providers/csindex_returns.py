"""CSI's public workbook download for explicitly mapped gross total-return indices."""

import logging
import re
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from io import BytesIO
from threading import Lock
from time import monotonic, sleep
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

import httpx

from index_valuation.domain import (
    DataError,
    Observation,
    ReferenceHistory,
    SourceError,
    decimal_or_none,
)

URL = "https://www.csindex.com.cn/csindex-home/exportExcel/downloadindex-perf"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
log = logging.getLogger(__name__)
MIN_REQUEST_INTERVAL = 2.0
_REQUEST_LOCK = Lock()
_last_request = None


def _throttle():
    """Conservative client pacing, shared by readers in the current process.

    CSI publishes no confirmed numeric rate limit for this public export. This
    interval avoids bursts; it is not permission to retry an access rejection.
    """
    global _last_request
    with _REQUEST_LOCK:
        if _last_request is not None:
            delay = MIN_REQUEST_INTERVAL - (monotonic() - _last_request)
            if delay > 0:
                sleep(delay)
        _last_request = monotonic()


def _normalized(value):
    return re.sub(r"\s+", "", value).casefold()


HEADERS = {
    "date": {"日期date", "日期", "date"},
    "code": {"指数代码indexcode", "指数代码", "indexcode"},
    "close": {"收盘close", "收盘", "close"},
    "name_cn": {"指数中文全称indexchinesename(full)", "指数中文全称", "indexchinesename(full)"},
    "name_en": {"指数英文全称indexenglishname(full)", "指数英文全称", "indexenglishname(full)"},
}


def _cells(row, strings):
    result = {}
    for cell in row:
        match = re.fullmatch(r"([A-Z]+)[0-9]+", cell.get("r", ""))
        if not match or match[1] in result:
            raise DataError("CSI total-return workbook contains invalid cell addresses")
        kind = cell.get("t")
        if kind == "inlineStr":
            value = "".join(cell.itertext())
        else:
            node = cell.find("s:v", NS)
            value = node.text or "" if node is not None else ""
            if kind == "s":
                try:
                    position = int(value)
                    if position < 0:
                        raise IndexError
                    value = strings[position]
                except (ValueError, IndexError) as error:
                    raise DataError("CSI workbook contains an invalid shared string") from error
            elif kind == "e" or cell.find("s:f", NS) is not None:
                raise DataError("CSI total-return workbook contains a formula or cell error")
        result[match[1]] = value.strip()
    return result


def workbook_rows(content):
    """Decode the source's XLSX without a spreadsheet dependency or column-position guesses."""
    result, found_sheet = [], False
    try:
        with ZipFile(BytesIO(content)) as workbook:
            if sum(item.file_size for item in workbook.infolist()) > 50 * 1024 * 1024:
                raise SourceError("CSI total-return workbook exceeds the expected size")
            strings = []
            if "xl/sharedStrings.xml" in workbook.namelist():
                strings = [
                    "".join(item.itertext())
                    for item in ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
                ]
            for name in workbook.namelist():
                if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                    continue
                sheet = ElementTree.fromstring(workbook.read(name))
                rows = sheet.findall(".//s:sheetData/s:row", NS)
                if not rows:
                    continue
                found_sheet = True
                headers = _cells(rows[0], strings)
                mapping = {}
                for column, title in headers.items():
                    for field, aliases in HEADERS.items():
                        if _normalized(title) in aliases:
                            if field in mapping:
                                raise DataError("CSI workbook repeats a required header")
                            mapping[field] = column
                if not {"date", "code", "close"} <= mapping.keys() or not (
                    {"name_cn", "name_en"} & mapping.keys()
                ):
                    raise DataError("CSI total-return workbook schema changed")
                for row in rows[1:]:
                    values = _cells(row, strings)
                    if not any(values.values()):
                        continue
                    result.append(
                        {field: values.get(column, "") for field, column in mapping.items()}
                    )
    except (BadZipFile, ElementTree.ParseError, KeyError) as error:
        raise SourceError("CSI did not return a valid total-return XLSX workbook") from error
    if not found_sheet:
        raise SourceError("CSI total-return workbook has no data sheet")
    return result


def parse_total_returns(content, spec, start, end, observed_at, path, *, requested_codes=None):
    # Only an explicit original-request allowlist may admit a batch workbook.
    # Normal single-index collection remains strict even if another requested
    # index would have a plausible full-return name.
    allowed_codes = {spec.provider_code} if requested_codes is None else set(requested_codes)
    if spec.provider_code not in allowed_codes or not all(
        isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9]+", code) for code in allowed_codes
    ):
        raise DataError("CSI total-return batch must include the requested target identity")
    observations, seen = [], set()
    for row in workbook_rows(content):
        if row["code"] not in allowed_codes:
            raise DataError("CSI total-return workbook returned a different index")
        names = [row.get("name_cn", ""), row.get("name_en", "")]
        if any("净收益" in name or "net return" in name.casefold() for name in names) or not any(
            "全收益" in name or "total return" in name.casefold() for name in names
        ):
            raise DataError("CSI workbook identity is not a gross total-return index")
        if row["code"] != spec.provider_code:
            continue
        try:
            day = datetime.strptime(row["date"].replace("-", ""), "%Y%m%d").date()
        except ValueError as error:
            raise DataError("CSI total-return workbook returned an invalid date") from error
        # The public export includes the requested end date; the caller asks for
        # one additional day, matching CSI's varying boundary behavior. Never
        # admit a pre-base-date row merely because the source repeats its base value.
        if not start <= day <= end:
            continue
        if day in seen:
            raise DataError("CSI total-return workbook returned duplicate dates")
        seen.add(day)
        price = decimal_or_none(row["close"])
        quote_at = datetime.combine(
            day, clock_time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if price is None or price <= 0 or quote_at > observed_at:
            raise DataError("CSI total-return workbook returned an invalid or unfinished close")
        observations.append(
            Observation(
                symbol=spec.quote_code,
                trade_date=day,
                source="csindex",
                price=price,
                eps_ttm=None,
                pe_ttm=None,
                observed_at=observed_at,
                quote_at=quote_at,
                currency=spec.currency,
                price_basis="gross_total_return",
                eps_method="not_applicable",
                artifact_path=path,
                raw=row,
            )
        )
    return sorted(observations, key=lambda row: row.trade_date)


class CSITotalReturnHistory:
    source_name = "csindex"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if start > end:
            raise DataError("Invalid total-return history range")
        observations, failures, consecutive_errors = [], [], 0
        with httpx.Client(timeout=self.timeout, transport=httpx.HTTPTransport(retries=0)) as client:
            current = start
            while current <= end:
                last = min(end, date(current.year, 12, 31))
                body = [
                    {
                        "indexCode": spec.provider_code,
                        "startDate": current.strftime("%Y%m%d"),
                        "endDate": (last + timedelta(days=1)).strftime("%Y%m%d"),
                    }
                ]
                observed_at = datetime.now(UTC)
                request = {"url": URL, "params": {"language": "CH"}, "json": body}
                status, path, halt = None, None, False
                try:
                    _throttle()
                    response = client.post(URL, params=request["params"], json=body)
                    observed_at = datetime.now(UTC)
                    status = response.status_code
                    suffix = "xlsx" if response.content.startswith(b"PK") else "bin"
                    _, binary_path = self.archive.save(
                        "csindex_total_return", response.content, suffix
                    )
                    path = self.archive.save_json(
                        "csindex_total_return",
                        {
                            **request,
                            "observed_at": observed_at,
                            "status_code": status,
                            "content_type": response.headers.get("content-type"),
                            "workbook_path": binary_path,
                        },
                    )
                    response.raise_for_status()
                    rows = parse_total_returns(
                        response.content, spec, current, last, observed_at, path
                    )
                except (httpx.HTTPError, SourceError) as error:
                    consecutive_errors += 1
                    if path is None:
                        path = self.archive.save_json(
                            "csindex_total_return",
                            {**request, "observed_at": observed_at, "error": str(error)},
                        )
                    failures.append(
                        {"start": current, "end": last, "error": str(error), "artifact": path}
                    )
                    log.warning("CSI total return %s %s–%s: %s", spec.key, current, last, error)
                    # Access/rate-limit rejection is not a reason to try another
                    # range. Other transient failures stop after three chunks.
                    halt = status in {401, 403, 429} or consecutive_errors >= 3
                else:
                    consecutive_errors = 0
                    observations.extend(rows)
                    if not rows:
                        failures.append(
                            {
                                "start": current,
                                "end": last,
                                "error": "no_total_return_data_in_requested_range",
                                "artifact": path,
                            }
                        )
                current = last + timedelta(days=1)
                if halt:
                    if current <= end:
                        failures.append(
                            {
                                "start": current,
                                "end": end,
                                "error": "not_attempted_after_source_failure",
                            }
                        )
                    break
        return ReferenceHistory(observations=observations, failures=failures)

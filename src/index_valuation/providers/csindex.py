"""Official CSI constituent weights; preserve the date embedded in the workbook."""

from datetime import date, datetime
from decimal import Decimal

import httpx
import xlrd

from index_valuation.archive import Archive
from index_valuation.domain import (
    Constituent,
    DataError,
    IndexSpec,
    SourceError,
    WeightSnapshot,
    decimal_or_none,
)

BASE_URL = "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile"
EXCHANGES = {"上海证券交易所": "SH", "深圳证券交易所": "SZ"}


def parse_weights(content: bytes, spec: IndexSpec) -> tuple[date, tuple[Constituent, ...]]:
    try:
        book = xlrd.open_workbook(file_contents=content)
        sheet = book.sheet_by_index(0)
    except (xlrd.XLRDError, IndexError) as error:
        raise DataError("CSI response is not a usable Excel workbook") from error
    headers = [str(value).lower().replace(" ", "") for value in sheet.row_values(0)]
    if len(headers) != 10 or "date" not in headers[0] or "weight" not in headers[9]:
        raise DataError("CSI workbook schema changed")
    dates, members = set(), []
    for i in range(1, sheet.nrows):
        row = sheet.row_values(i)
        if not any(str(value).strip() for value in row):
            continue
        day = datetime.strptime(str(row[0]).split(".")[0], "%Y%m%d").date()
        dates.add(day)
        code = str(row[1]).split(".")[0].zfill(len(spec.provider_code))
        if code != spec.provider_code:
            raise DataError("CSI returned a different index")
        if row[7] not in EXCHANGES:
            raise DataError(f"Unsupported exchange: {row[7]}")
        stock_code = str(row[4]).split(".")[0].zfill(6)
        if len(stock_code) != 6 or not stock_code.isdigit():
            raise DataError("Invalid constituent code")
        weight = decimal_or_none(row[9])
        if weight is None or weight <= 0:
            raise DataError(f"Invalid constituent weight: {stock_code}")
        members.append(Constituent(f"{EXCHANGES[row[7]]}.{stock_code}", str(row[5]), weight))
    if (
        len(dates) != 1
        or not members
        or (spec.expected_constituents is not None and len(members) != spec.expected_constituents)
    ):
        raise DataError("Unexpected CSI snapshot dates or constituent count")
    if len({member.symbol for member in members}) != len(members):
        raise DataError("Duplicate CSI constituents")
    total = sum((member.weight_percent for member in members), Decimal(0))
    if abs(total - 100) > Decimal("0.2"):
        raise DataError(f"CSI weights sum to {total}%, outside rounding tolerance")
    return dates.pop(), tuple(members)


class CSIWeights:
    def __init__(self, archive: Archive):
        self.archive = archive

    def fetch(self, spec: IndexSpec) -> WeightSnapshot:
        if spec.market != "CN" or spec.reference_history_source != "csindex":
            raise SourceError("No constituent-weight adapter configured for this index provider")
        url = f"{BASE_URL}/closeweight/{spec.provider_code}closeweight.xls"
        transport = httpx.HTTPTransport(retries=2)
        try:
            with httpx.Client(transport=transport, timeout=30, follow_redirects=True) as client:
                response = client.get(url)
                response.raise_for_status()
        except httpx.HTTPError as error:
            raise SourceError(f"CSI weight download failed: {error}") from error
        as_of, members = parse_weights(response.content, spec)
        digest, path = self.archive.save("csindex", response.content, "xls")
        return WeightSnapshot(as_of, members, "csindex", url, digest, path)

"""Daily index closes redistributed by FRED from S&P and Nasdaq; no PE estimates."""

import csv
import re
from datetime import UTC, date, datetime, time, timedelta
from html.parser import HTMLParser
from io import StringIO
from zoneinfo import ZoneInfo

import httpx
import pandas_market_calendars as mcal

from index_valuation.domain import DataError, Observation, ReferenceHistory, decimal_or_none

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
MAPPINGS = {"SP500": "US..SPX", "NASDAQ100": "US..NDX"}
SP500_COVERAGE_POLICY = "https://fred.stlouisfed.org/series/SP500"
SERIES_URL = "https://fred.stlouisfed.org/series/{}"


class _RecentObservations(HTMLParser):
    """Read only the identified FRED metadata and recent-observations table."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonicals, self.frequencies, self.rows = [], [], []
        self.table_count = 0
        self.in_table = False
        self.frequency_depth = 0
        self.frequency_parts = []
        self.row = self.cell = None
        self.view_all = False

    def handle_starttag(self, tag, attrs):
        attrs = {key: value or "" for key, value in attrs}
        if tag == "link" and "canonical" in attrs.get("rel", "").split():
            self.canonicals.append(attrs.get("href"))
        if tag == "span":
            if self.frequency_depth:
                self.frequency_depth += 1
            elif "series-meta-value-frequency" in attrs.get("class", "").split():
                self.frequency_depth = 1
                self.frequency_parts = []
        if tag == "table":
            if self.in_table:
                raise DataError("FRED recent observations contain nested tables")
            if attrs.get("id") == "recent-obs":
                self.table_count += 1
                self.in_table = True
        if not self.in_table:
            return
        if tag == "tr":
            if self.row is not None:
                raise DataError("FRED recent observations contain malformed rows")
            self.row, self.view_all = [], False
        elif tag in {"td", "th"}:
            if self.row is None or self.cell is not None:
                raise DataError("FRED recent observations contain malformed cells")
            self.cell = []
        elif tag == "a" and attrs.get("id") == "viewall-button":
            self.view_all = True

    def handle_endtag(self, tag):
        if tag == "span" and self.frequency_depth:
            self.frequency_depth -= 1
            if not self.frequency_depth:
                self.frequencies.append("".join(self.frequency_parts))
        if not self.in_table:
            return
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr":
            if self.row is None or self.cell is not None:
                raise DataError("FRED recent observations contain unclosed cells")
            if not (self.view_all and self.row == ["View All"]):
                self.rows.append(self.row)
            self.row = None
        elif tag == "table":
            if self.row is not None:
                raise DataError("FRED recent observations contain unclosed rows")
            self.in_table = False

    def handle_data(self, data):
        if self.frequency_depth:
            self.frequency_parts.append(data)
        if self.cell is not None:
            self.cell.append(data)


def parse_recent_observations(text, spec, start, end, observed_at, path):
    parser = _RecentObservations()
    parser.feed(text)
    parser.close()
    url = SERIES_URL.format(spec.fred_series)
    if parser.canonicals != [url]:
        raise DataError("FRED recent observations returned a different series identity")
    if (
        not parser.frequencies
        or any("".join(value.split()) != "Daily,Close" for value in parser.frequencies)
        or parser.frequency_depth
    ):
        raise DataError("FRED recent observations require Daily, Close frequency")
    if parser.table_count != 1 or parser.in_table or not parser.rows:
        raise DataError("FRED recent observations table is missing or incomplete")
    rows, seen = [], set()
    for cells in parser.rows:
        if len(cells) != 3 or cells[2] or not re.fullmatch(r"\d{4}-\d{2}-\d{2}:?", cells[0]):
            raise DataError("FRED recent observations returned an invalid observation date or row")
        try:
            day = date.fromisoformat(cells[0].removesuffix(":"))
        except ValueError as error:
            raise DataError(
                "FRED recent observations returned an invalid observation date"
            ) from error
        if day in seen:
            raise DataError("FRED recent observations returned duplicate dates")
        seen.add(day)
        price = decimal_or_none(cells[1].replace(",", ""))
        quote_at = datetime.combine(
            day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if (
            not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", cells[1])
            or price is None
            or price <= 0
            or quote_at > observed_at
        ):
            raise DataError("FRED recent observations returned invalid or unfinished index closes")
        if start <= day <= end:
            rows.append(
                Observation(
                    spec.quote_code,
                    day,
                    "fred",
                    price,
                    None,
                    None,
                    observed_at,
                    quote_at,
                    currency=spec.currency,
                    eps_method="not_provided",
                    artifact_path=path,
                    raw={
                        "observation_date": day.isoformat(),
                        spec.fred_series: str(price),
                        "retrieval_method": "series_recent_observations",
                        "url": url,
                        "date_text": cells[0],
                        "value_text": cells[1],
                        "frequency": "Daily, Close",
                    },
                )
            )
    return sorted(rows, key=lambda row: row.trade_date)


def _recent_closed_date(spec, start, end, observed_at):
    today = observed_at.astimezone(ZoneInfo(spec.timezone)).date()
    recent_start = max(start, today - timedelta(days=14))
    recent_end = min(end, today)
    if recent_start > recent_end:
        return None
    schedule = mcal.get_calendar("NYSE").schedule(start_date=recent_start, end_date=recent_end)
    closed = schedule.loc[schedule["market_close"] <= observed_at]
    return closed.index[-1].date() if not closed.empty else None


def parse_history(text, spec, start, end, observed_at, path):
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames != ["observation_date", spec.fred_series]:
        raise DataError("FRED returned a different series or CSV schema")
    rows, seen = [], set()
    for item in reader:
        try:
            day = date.fromisoformat(item["observation_date"])
        except (TypeError, ValueError) as error:
            raise DataError("FRED returned an invalid observation date") from error
        if not start <= day <= end or day in seen:
            raise DataError("FRED returned duplicate or out-of-range dates")
        seen.add(day)
        raw = item[spec.fred_series]
        if raw in {"", "."}:  # FRED includes blank holiday/missing observations.
            continue
        price = decimal_or_none(raw)
        quote_at = datetime.combine(
            day, time.fromisoformat(spec.close_time), ZoneInfo(spec.timezone)
        )
        if price is None or price <= 0 or quote_at > observed_at:
            raise DataError("FRED returned invalid or unfinished index closes")
        rows.append(
            Observation(
                spec.quote_code,
                day,
                "fred",
                price,
                None,
                None,
                observed_at,
                quote_at,
                currency=spec.currency,
                eps_method="not_provided",
                artifact_path=path,
                raw=item,
            )
        )
    return rows


class FredHistory:
    source_name = "fred"

    def __init__(self, archive, timeout=20):
        self.archive, self.timeout = archive, timeout

    def index_history(self, spec, start, end):
        if spec.market != "US" or MAPPINGS.get(spec.fred_series) != spec.quote_code:
            raise DataError("FRED requires an explicitly mapped daily US index series")
        if start > end:
            raise DataError("FRED historical range must be ordered")
        batch = ReferenceHistory()
        cursor = start
        if spec.fred_series == "SP500":
            # FRED's S&P licence exposes only ten years. A request wholly before
            # that window can ignore cosd/coed and return the entire current
            # series. Do not request or relabel that data as historical coverage.
            today = datetime.now(ZoneInfo(spec.timezone)).date()
            try:
                available_start = today.replace(year=today.year - 10)
            except ValueError:
                available_start = today.replace(year=today.year - 10, day=28)
            if cursor < available_start:
                batch.failures.append(
                    {
                        "stage": "prices",
                        "start": cursor,
                        "end": min(end, available_start - timedelta(days=1)),
                        "category": "unsupported_historical_coverage",
                        "error": "FRED SP500 offers only a rolling ten-year daily history",
                        "policy_url": SP500_COVERAGE_POLICY,
                        "available_start_bound": available_start,
                        "as_of": today,
                    }
                )
                cursor = available_start
        if cursor > end:
            return batch
        with httpx.Client(timeout=self.timeout, transport=httpx.HTTPTransport(retries=2)) as client:
            while cursor <= end:
                last = min(date(cursor.year + 4, 12, 31), end)
                params = {
                    "id": spec.fred_series,
                    "cosd": cursor.isoformat(),
                    "coed": last.isoformat(),
                }
                try:
                    response = client.get(URL, params=params)
                    response.raise_for_status()
                except httpx.HTTPError as error:
                    batch.failures.append({"start": cursor, "end": last, "error": str(error)})
                else:
                    observed_at = datetime.now(UTC)
                    path = self.archive.save_json(
                        "fred_history",
                        {
                            "observed_at": observed_at,
                            "url": URL,
                            "params": params,
                            "text": response.text,
                        },
                    )
                    try:
                        rows = parse_history(response.text, spec, cursor, last, observed_at, path)
                    except DataError as error:
                        batch.failures.append(
                            {
                                "stage": "prices",
                                "start": cursor,
                                "end": last,
                                "category": "invalid_data",
                                "error": str(error),
                                "artifact_path": path,
                            }
                        )
                    else:
                        batch.observations.extend(rows)
                cursor = last + timedelta(days=1)
            self._supplement_recent(client, spec, start, end, batch)
        return batch

    def _supplement_recent(self, client, spec, start, end, batch):
        target = _recent_closed_date(spec, start, end, datetime.now(UTC))
        existing = {row.trade_date: row for row in batch.observations}
        if target is None or target in existing:
            return
        url = SERIES_URL.format(spec.fred_series)
        context = {
            "stage": "prices",
            "start": start,
            "end": end,
            "target_date": target,
            "retrieval_method": "series_recent_observations",
            "url": url,
        }
        try:
            response = client.get(url)
        except httpx.HTTPError as error:
            path = self.archive.save_json(
                "fred_history",
                {**context, "observed_at": datetime.now(UTC), "error": str(error)},
            )
            batch.failures.append(
                {
                    **context,
                    "category": "request_failed",
                    "error": str(error),
                    "artifact_path": path,
                }
            )
            return
        observed_at = datetime.now(UTC)
        path = self.archive.save_json(
            "fred_history",
            {
                **context,
                "observed_at": observed_at,
                "status_code": response.status_code,
                "text": response.text,
            },
        )
        try:
            response.raise_for_status()
        except httpx.HTTPError as error:
            batch.failures.append(
                {
                    **context,
                    "category": "request_failed",
                    "error": str(error),
                    "artifact_path": path,
                }
            )
            return
        try:
            rows = parse_recent_observations(response.text, spec, start, end, observed_at, path)
        except DataError as error:
            batch.failures.append(
                {**context, "category": "invalid_data", "error": str(error), "artifact_path": path}
            )
            return
        for row in rows:
            previous = existing.get(row.trade_date)
            if previous is None:
                batch.observations.append(row)
            elif previous.price != row.price:
                batch.failures.append(
                    {
                        **context,
                        "category": "same_source_conflict",
                        "error": "FRED CSV and recent observations disagree; preserving CSV close",
                        "trade_date": row.trade_date,
                        "csv_value": previous.price,
                        "page_value": row.price,
                        "csv_artifact_path": previous.artifact_path,
                        "artifact_path": path,
                    }
                )
        batch.observations.sort(key=lambda row: row.trade_date)

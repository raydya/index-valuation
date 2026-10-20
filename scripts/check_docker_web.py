"""Capture and compare read-only local web APIs before a deployment cutover.

Run ``snapshot --url http://127.0.0.1:8000`` before stopping the old web process,
then ``compare --url http://127.0.0.1:18000`` against the candidate container.
Only HTTP GET is used. The baseline keeps compressed full responses so every
date, valuation, band, return and task receipt can be compared exactly.
"""

import argparse
import csv
import gzip
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/exports/docker-migration"
TASK_IDS = {"erp", "portfolio_us", "index_us", "index_asia", "portfolio_asia"}
HISTORICAL_KEYS = {"csi300", "hstech", "sp500"}
MAX_DIFFERENCES = 40
IGNORED_FIELDS = ["checked_at", "tasks[*].scheduler.source"]


def local_url(value):
    parts = urlsplit(value)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise argparse.ArgumentTypeError("Use a local HTTP origin without credentials or a path")
    return value.rstrip("/")


def normalized(value, path=()):
    """Ignore response read times, never stored observation or execution times."""
    if isinstance(value, dict):
        return {
            key: normalized(item, (*path, key))
            for key, item in value.items()
            if key != "checked_at" and not (path[-1:] == ("scheduler",) and key == "source")
        }
    if isinstance(value, list):
        return [normalized(item, path) for item in value]
    return value


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def fetch(client, endpoint):
    response = client.get(endpoint)
    if response.status_code != 200:
        return {"http_status": response.status_code, "body": response.text}
    if endpoint.split("?", 1)[0].endswith(".csv"):
        reader = csv.DictReader(io.StringIO(response.text.lstrip("\ufeff")))
        body = {"columns": reader.fieldnames, "rows": list(reader)}
    else:
        body = normalized(response.json())
    return {"http_status": response.status_code, "body": body}


def describe(endpoint, response):
    body = response["body"]
    result = {"http_status": response["http_status"]}
    if result["http_status"] != 200:
        return result
    if endpoint == "/api/indices":
        result["index_keys"] = [item["key"] for item in body]
    elif endpoint == "/api/scheduled-tasks":
        result["tasks"] = [
            {
                "id": item["id"],
                "schedule": item["schedule_text"],
                "scheduler": item["scheduler"]["state"],
                "last_run": item["last_run"]["status"],
                "data_items": len(item["data_items"]),
            }
            for item in body["tasks"]
        ]
    elif endpoint == "/api/market-overview":
        result["indices"] = [
            {key: item.get(key) for key in ("key", "as_of", "pe", "signal")}
            for item in body["items"]
        ]
    elif "/dashboard" in endpoint:
        result.update(
            {
                "selected": {
                    key: body["selected"].get(key)
                    for key in ("trade_date", "price", "pe", "implied_eps", "fixed")
                },
                "method": body["method"],
                "series_rows": len(body["series"]),
                "available_start": body["available_start"],
                "available_end": body["available_end"],
                "price_return": body["price_return"],
                "total_return": body["total_return"],
            }
        )
    elif "/export.csv" in endpoint:
        rows = body["rows"]
        result.update(
            {
                "rows": len(rows),
                "columns": body["columns"],
                "start_date": rows[0].get("trade_date") if rows else None,
                "end_date": rows[-1].get("trade_date") if rows else None,
            }
        )
    return result


def require_baseline(endpoint, response):
    if response["http_status"] != 200:
        raise ValueError(f"{endpoint}: HTTP {response['http_status']}, cannot establish baseline")
    body = response["body"]
    if endpoint == "/api/scheduled-tasks":
        if {item["id"] for item in body["tasks"]} != TASK_IDS or len(body["tasks"]) != 5:
            raise ValueError("The baseline must contain the five known scheduled tasks")
    elif "/dashboard" in endpoint:
        for key in ("selected", "series", "price_return", "total_return", "panic_line"):
            if key not in body:
                raise ValueError(f"{endpoint}: missing {key}")


def snapshot(client, url, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    assets = output.parent / f"{output.stem}.responses"
    assets.mkdir(exist_ok=True)
    manifest = {
        "format_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "url": url,
        "ignored_fields": IGNORED_FIELDS,
        "endpoints": {},
    }
    indices = None
    endpoints = ["/api/indices", "/api/market-overview", "/api/scheduled-tasks"]
    while endpoints:
        endpoint = endpoints.pop(0)
        response = fetch(client, endpoint)
        require_baseline(endpoint, response)
        payload = encoded(response)
        filename = hashlib.sha256(endpoint.encode()).hexdigest()[:16] + ".json.gz"
        with gzip.open(assets / filename, "wb") as stream:
            stream.write(payload)
        manifest["endpoints"][endpoint] = {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "response_file": f"{assets.name}/{filename}",
            "summary": describe(endpoint, response),
        }
        if endpoint == "/api/indices":
            indices = response["body"]
            for index in indices:
                key = index["key"]
                endpoints.extend(
                    (f"/api/indices/{key}/dashboard", f"/api/indices/{key}/export.csv")
                )
        elif endpoint.endswith("/dashboard"):
            key = endpoint.split("/")[3]
            series = response["body"]["series"]
            if key in HISTORICAL_KEYS and series:
                day = series[max(0, len(series) - 100)]["trade_date"]
                endpoints.extend(
                    (
                        f"{endpoint}?as_of={day}&chart_years=1",
                        f"/api/indices/{key}/export.csv?as_of={day}&chart_years=1",
                    )
                )
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "status": "captured",
        "indices": len(indices or []),
        "endpoints": len(manifest["endpoints"]),
        "baseline": str(output),
    }


def brief(value):
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 200 else text[:197] + "..."


def differences(before, after, path="$", found=None):
    """Collect bounded field paths while whole-response hashes detect all changes."""
    found = [] if found is None else found
    if len(found) >= MAX_DIFFERENCES or before == after:
        return found
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                found.append(
                    {
                        "path": f"{path}.{key}",
                        "change": "added" if key not in before else "removed",
                    }
                )
            else:
                differences(before[key], after[key], f"{path}.{key}", found)
            if len(found) >= MAX_DIFFERENCES:
                break
    elif isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            found.append({"path": path + ".length", "before": len(before), "after": len(after)})
        for index, (old, new) in enumerate(zip(before, after, strict=False)):
            differences(old, new, f"{path}[{index}]", found)
            if len(found) >= MAX_DIFFERENCES:
                break
    else:
        found.append({"path": path, "before": brief(before), "after": brief(after)})
    return found


def compare(client, url, baseline, output):
    manifest = json.loads(baseline.read_text())
    if manifest["format_version"] != 1:
        raise ValueError("Unsupported baseline format")
    results = []
    for endpoint, expected in manifest["endpoints"].items():
        response = fetch(client, endpoint)
        actual_hash = hashlib.sha256(encoded(response)).hexdigest()
        matched = actual_hash == expected["sha256"]
        item = {
            "endpoint": endpoint,
            "matched": matched,
            "baseline_sha256": expected["sha256"],
            "candidate_sha256": actual_hash,
        }
        if not matched:
            with gzip.open(baseline.parent / expected["response_file"], "rt") as stream:
                previous = json.load(stream)
            item["differences"] = differences(previous, response)
            item["difference_limit"] = MAX_DIFFERENCES
        results.append(item)
    report = {
        "status": "passed" if all(item["matched"] for item in results) else "different",
        "baseline": str(baseline),
        "baseline_url": manifest["url"],
        "candidate_url": url,
        "checked_at": datetime.now(UTC).isoformat(),
        "ignored_fields": IGNORED_FIELDS,
        "matched_endpoints": sum(item["matched"] for item in results),
        "total_endpoints": len(results),
        "results": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return {
        "status": report["status"],
        "matched_endpoints": report["matched_endpoints"],
        "total_endpoints": len(results),
        "different_endpoints": [item["endpoint"] for item in results if not item["matched"]],
        "report": str(output),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("snapshot", help="Record the old service with read-only GETs")
    capture.add_argument("--url", type=local_url, default="http://127.0.0.1:8000")
    capture.add_argument("--output", type=Path, default=OUTPUT / "baseline.json")
    check = commands.add_parser("compare", help="Compare a service against the captured baseline")
    check.add_argument("--url", type=local_url, default="http://127.0.0.1:18000")
    check.add_argument("--baseline", type=Path, default=OUTPUT / "baseline.json")
    check.add_argument("--output", type=Path, default=OUTPUT / "comparison.json")
    args = parser.parse_args()
    try:
        with httpx.Client(base_url=args.url, timeout=60, trust_env=False) as client:
            if args.command == "snapshot":
                result = snapshot(client, args.url, args.output)
            else:
                result = compare(client, args.url, args.baseline, args.output)
    except (httpx.HTTPError, OSError, ValueError, KeyError) as error:
        # Do not print responses, environment variables or arbitrary exception details.
        print(json.dumps({"status": "error", "error_type": type(error).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"captured", "passed"} else 2


if __name__ == "__main__":
    raise SystemExit(main())

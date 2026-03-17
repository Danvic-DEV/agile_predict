from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen, Request
import json

NORDPOOL_DAY_AHEAD_URL = "https://dataportal-api.nordpoolgroup.com/api/DayAheadPrices"


def build_nordpool_params(target_date: date) -> dict[str, str]:
    return {
        "date": target_date.strftime("%Y-%m-%d"),
        "market": "N2EX_DayAhead",
        "deliveryArea": "UK",
        "currency": "GBP",
    }


def parse_day_ahead_payload(payload: dict) -> dict[datetime, float]:
    entries = payload.get("multiAreaEntries", [])
    data: dict[datetime, float] = {}

    for row in entries:
        delivery_start = row.get("deliveryStart")
        value = row.get("entryPerArea", {}).get("UK")
        if delivery_start is None or value is None:
            continue

        dt = datetime.fromisoformat(delivery_start.replace("Z", "+00:00"))
        data[dt] = float(value)

    return dict(sorted(data.items(), key=lambda item: item[0]))


def fetch_day_ahead_prices(now: datetime | None = None, timeout: int = 20) -> dict[datetime, float]:
    current = now or datetime.now(timezone.utc)
    target_date = (current + timedelta(hours=13)).date()
    params = build_nordpool_params(target_date)
    query = urlencode(params)
    url = f"{NORDPOOL_DAY_AHEAD_URL}?{query}"

    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; agile-predict)"})
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return parse_day_ahead_payload(payload)

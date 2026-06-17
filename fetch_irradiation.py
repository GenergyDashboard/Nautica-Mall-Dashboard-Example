"""
fetch_irradiation.py

Fetches hourly direct irradiation data from Open-Meteo API
for Nautica Shopping Centre. Stores in data/irradiation_data.json.

- Fetches today from the Forecast API
- Backfills any missing days (last 90 days) from the Archive API
- Accumulates daily records for dashboard irradiation overlays
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

LATITUDE = -33.0442418919348
LONGITUDE = 18.05227655326906
TIMEZONE = "Africa/Johannesburg"

FORECAST_API = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_API = "https://archive-api.open-meteo.com/v1/archive"

DATA_DIR = Path("data")
IRRADIATION_FILE = DATA_DIR / "irradiation_data.json"

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]
BACKFILL_DAYS = 90


def fetch_with_retry(url, timeout=30):
    """Fetch URL with retry logic for transient failures."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Genergy-Solar-Dashboard/1.0'
            })
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.loads(response.read())
        except Exception as e:
            last_error = e
            if any(code in str(e) for code in ['502', '503', '504', 'timed out', 'Timeout', 'Gateway']):
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    print(f"  ⚠️  Attempt {attempt+1} failed ({e}), retrying in {delay}s...")
                    time.sleep(delay)
                    continue
            raise
    raise last_error


def parse_irradiation_response(data):
    """Parse Open-Meteo response into daily records keyed by date."""
    records = {}
    timestamps = data["hourly"]["time"]
    values = data["hourly"]["direct_radiation"]

    # Group by date
    days = {}
    for ts, val in zip(timestamps, values):
        date_str = ts.split("T")[0]
        hour = int(ts.split("T")[1].split(":")[0])
        if date_str not in days:
            days[date_str] = [0.0] * 24
        days[date_str][hour] = round(val, 1) if val is not None else 0.0

    for date_str, hourly in days.items():
        daily_total_wh = round(sum(hourly), 1)
        peak = round(max(hourly), 1)
        sun_hours = sum(1 for v in hourly if v > 10)
        records[date_str] = {
            "hourly_wm2": hourly,
            "peak_wm2": peak,
            "daily_total_wh_m2": daily_total_wh,
            "daily_total_kwh_m2": round(daily_total_wh / 1000, 3),
            "sun_hours": sun_hours
        }

    return records


def fetch_today():
    """Fetch today's irradiation from Forecast API."""
    url = (
        f"{FORECAST_API}"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&hourly=direct_radiation&forecast_days=1"
        f"&timezone={TIMEZONE}"
    )
    print("🌤️  Fetching today's irradiation...")
    try:
        data = fetch_with_retry(url)
        records = parse_irradiation_response(data)
        for ds, rec in records.items():
            print(f"  📅 {ds}: peak={rec['peak_wm2']} W/m², total={rec['daily_total_wh_m2']} Wh/m², sun={rec['sun_hours']}h")
        return records
    except Exception as e:
        print(f"  ❌ Forecast API failed: {e}")
        return {}


def fetch_archive(start_date, end_date):
    """Fetch historical irradiation from Archive API."""
    url = (
        f"{ARCHIVE_API}"
        f"?latitude={LATITUDE}&longitude={LONGITUDE}"
        f"&hourly=direct_radiation"
        f"&start_date={start_date}&end_date={end_date}"
        f"&timezone={TIMEZONE}"
    )
    print(f"📚 Fetching archive: {start_date} to {end_date}...")
    try:
        data = fetch_with_retry(url, timeout=60)
        records = parse_irradiation_response(data)
        print(f"  ✅ Got {len(records)} days from archive")
        return records
    except Exception as e:
        print(f"  ❌ Archive API failed: {e}")
        return {}


def load_existing_data():
    """Load existing irradiation history."""
    if IRRADIATION_FILE.exists():
        with open(IRRADIATION_FILE, "r") as f:
            return json.load(f)
    return {
        "plant": "Nautica Shopping Centre",
        "location": {"latitude": LATITUDE, "longitude": LONGITUDE, "timezone": TIMEZONE},
        "daily_records": {}
    }


def main():
    print("🌤️  Nautica Shopping Centre - Irradiation Data")
    print("=" * 50)
    print(f"📍 Location: {LATITUDE}, {LONGITUDE}")

    existing = load_existing_data()
    records = existing.get("daily_records", {})
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")

    # 1. Fetch today
    today_records = fetch_today()
    records.update(today_records)

    # 2. Find missing dates in the last BACKFILL_DAYS
    missing = []
    for i in range(BACKFILL_DAYS):
        ds = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        if ds not in records:
            missing.append(ds)

    if missing:
        print(f"\n📊 {len(missing)} missing days in last {BACKFILL_DAYS} days — backfilling...")
        missing.sort()

        # Batch into 30-day chunks (Archive API limit)
        batch_start = missing[0]
        batch_end = missing[0]
        batches = []

        for ds in missing[1:]:
            ds_date = datetime.strptime(ds, "%Y-%m-%d")
            end_date = datetime.strptime(batch_end, "%Y-%m-%d")
            if (ds_date - end_date).days <= 31:
                batch_end = ds
            else:
                batches.append((batch_start, batch_end))
                batch_start = ds
                batch_end = ds
        batches.append((batch_start, batch_end))

        for start, end in batches:
            archive_records = fetch_archive(start, end)
            records.update(archive_records)
            time.sleep(1)  # Be nice to the API
    else:
        print(f"✅ No missing days in last {BACKFILL_DAYS} days")

    # 3. Prune old records
    cutoff = (today - timedelta(days=BACKFILL_DAYS)).strftime("%Y-%m-%d")
    records = {k: v for k, v in records.items() if k >= cutoff}

    # 4. Save
    existing["daily_records"] = records
    existing["last_updated"] = today.strftime("%Y-%m-%d %H:%M:%S")

    DATA_DIR.mkdir(exist_ok=True)
    with open(IRRADIATION_FILE, "w") as f:
        json.dump(existing, f, indent=2)

    print(f"\n💾 Saved {len(records)} days to {IRRADIATION_FILE}")
    print(f"✅ Irradiation data complete!")
    if not today_records and not missing:
        print("⚠️  No new data fetched — using existing records")
        sys.exit(0)


if __name__ == "__main__":
    main()

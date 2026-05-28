"""
fetch_irradiation.py

Fetches today's hourly direct irradiation data from Open-Meteo API
for Nautica Shopping Centre and stores it in data/irradiation_data.json.

Accumulates daily records over time so the dashboard can show
irradiation history alongside generation data.

API: https://api.open-meteo.com/v1/forecast
Location: Nautica Shopping Centre, Saldanha Bay, Western Cape
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path


# ── Configuration ──────────────────────────────────────────────────────────
LATITUDE = -33.04416069960933
LONGITUDE = 18.05220351790642
TIMEZONE = "Africa/Johannesburg"

FORECAST_API = "https://api.open-meteo.com/v1/forecast"

DATA_DIR = Path("data")
IRRADIATION_FILE = DATA_DIR / "irradiation_data.json"

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # seconds between retries


def fetch_with_retry(url, timeout=30):
    """Fetch URL with retry logic for transient failures (502, 503, 504)."""
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
            error_str = str(e)
            # Retry on server errors (502, 503, 504) and timeouts
            if any(code in error_str for code in ['502', '503', '504', 'timed out', 'Timeout', 'Gateway']):
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    print(f"  ⚠️  Attempt {attempt+1} failed ({error_str}), retrying in {delay}s...")
                    time.sleep(delay)
                    continue
            # Non-retryable error — raise immediately
            raise
    raise last_error


def fetch_today_irradiation():
    """Fetch today's hourly direct irradiation from Open-Meteo API."""
    url = (
        f"{FORECAST_API}"
        f"?latitude={LATITUDE}"
        f"&longitude={LONGITUDE}"
        f"&hourly=direct_radiation"
        f"&forecast_days=1"
        f"&timezone={TIMEZONE}"
    )

    print(f"🌤️  Fetching irradiation from Open-Meteo API...")
    print(f"📍 Location: {LATITUDE}, {LONGITUDE}")

    try:
        data = fetch_with_retry(url)
    except Exception as e:
        print(f"❌ API request failed after {MAX_RETRIES} attempts: {e}")
        return None

    # Parse response
    timestamps = data["hourly"]["time"]
    radiation_values = data["hourly"]["direct_radiation"]

    # Build hourly array (24 hours, W/m²)
    hourly = []
    for ts, val in zip(timestamps, radiation_values):
        hour = int(ts.split("T")[1].split(":")[0])
        hourly.append({
            "hour": hour,
            "direct_radiation_wm2": round(val, 1) if val is not None else 0.0
        })

    # Calculate daily summary
    values = [h["direct_radiation_wm2"] for h in hourly]
    daily_total_wh = round(sum(values), 1)
    daily_total_kwh = round(daily_total_wh / 1000, 3)
    peak_wm2 = round(max(values), 1)
    sun_hours = sum(1 for v in values if v > 10)

    date_str = timestamps[0].split("T")[0]

    print(f"📅 Date: {date_str}")
    print(f"☀️  Peak irradiation: {peak_wm2} W/m²")
    print(f"⚡ Daily total: {daily_total_wh} Wh/m² ({daily_total_kwh} kWh/m²)")
    print(f"🕐 Sun hours (>10 W/m²): {sun_hours}h")

    return {
        "date": date_str,
        "hourly": values,
        "peak_wm2": peak_wm2,
        "daily_total_wh_m2": daily_total_wh,
        "daily_total_kwh_m2": daily_total_kwh,
        "sun_hours": sun_hours
    }


def load_existing_data():
    """Load existing irradiation history."""
    if IRRADIATION_FILE.exists():
        with open(IRRADIATION_FILE, "r") as f:
            return json.load(f)
    return {
        "plant": "Nautica Shopping Centre",
        "location": {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "timezone": TIMEZONE
        },
        "daily_records": {}
    }


def save_data(data):
    """Save irradiation data to file."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(IRRADIATION_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"💾 Saved to {IRRADIATION_FILE}")


def main():
    print("🌤️  Nautica Shopping Centre - Irradiation Data")
    print("=" * 50)

    today_data = fetch_today_irradiation()

    if today_data is None:
        # Don't fail the workflow — just skip today's irradiation
        print("⚠️  Skipping irradiation update (API unavailable)")
        print("ℹ️  Dashboard will use last available irradiation data")
        sys.exit(0)  # Exit 0 so the workflow continues

    # Load existing and merge
    existing = load_existing_data()
    date_str = today_data["date"]

    # Store in daily_records with hourly_wm2 array for dashboard compatibility
    existing["daily_records"][date_str] = {
        "hourly_wm2": today_data["hourly"],
        "peak_wm2": today_data["peak_wm2"],
        "daily_total_wh_m2": today_data["daily_total_wh_m2"],
        "daily_total_kwh_m2": today_data["daily_total_kwh_m2"],
        "sun_hours": today_data["sun_hours"]
    }

    # Prune records older than 90 days to keep file size manageable
    cutoff_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    existing["daily_records"] = {
        k: v for k, v in existing["daily_records"].items()
        if k >= cutoff_date
    }

    existing["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    save_data(existing)
    print(f"✅ Irradiation data updated ({len(existing['daily_records'])} days in history)")


if __name__ == "__main__":
    main()

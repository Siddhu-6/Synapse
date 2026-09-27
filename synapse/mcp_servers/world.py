"""Small keyless world tools: weather and current date/time. No API keys, no accounts."""
from __future__ import annotations

from datetime import datetime

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

mcp = FastMCP("world", instructions="Weather and current date/time.")


@mcp.tool(description="Current date, time and weekday in the given IANA timezone (default local).")
def now(timezone: str = "") -> dict:
    if timezone:
        try:
            from zoneinfo import ZoneInfo
            dt = datetime.now(ZoneInfo(timezone))
        except Exception as e:
            raise ToolError(f"unknown timezone '{timezone}'") from e
    else:
        dt = datetime.now()
    return {"iso": dt.isoformat(timespec="seconds"), "weekday": dt.strftime("%A"),
            "date": dt.strftime("%Y-%m-%d"), "time": dt.strftime("%H:%M"), "timezone": timezone or "local"}


@mcp.tool(description="Weather now and for the next days in a city. Keyless (Open-Meteo).")
def weather(city: str, days: int = 3) -> dict:
    try:
        geo = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": city, "count": 1}, timeout=15).json()
    except httpx.HTTPError as e:
        raise ToolError(f"geocoding failed: {e}") from e
    hits = geo.get("results") or []
    if not hits:
        raise ToolError(f"could not find a place called '{city}'")
    place = hits[0]
    try:
        r = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
            "latitude": place["latitude"], "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": min(max(days, 1), 7), "timezone": "auto"}).json()
    except httpx.HTTPError as e:
        raise ToolError(f"forecast failed: {e}") from e
    cur, daily = r.get("current", {}), r.get("daily", {})
    return {"place": f"{place['name']}, {place.get('country', '')}".strip(", "),
            "now": {"temp_c": cur.get("temperature_2m"), "humidity": cur.get("relative_humidity_2m"),
                    "precip_mm": cur.get("precipitation"), "wind_kmh": cur.get("wind_speed_10m")},
            "forecast": [{"date": d, "max_c": mx, "min_c": mn, "rain_chance_pct": pp}
                         for d, mx, mn, pp in zip(daily.get("time", []), daily.get("temperature_2m_max", []),
                                                  daily.get("temperature_2m_min", []),
                                                  daily.get("precipitation_probability_max", []), strict=False)]}


if __name__ == "__main__":
    mcp.run()

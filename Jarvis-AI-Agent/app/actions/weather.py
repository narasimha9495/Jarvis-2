"""Weather via Open-Meteo (free, no API key)."""

from typing import Any, Dict

import httpx

from app.actions.base import BaseAction

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather interpretation codes
_WMO = [
    ((0, 0), "Clear sky"),
    ((1, 1), "Mainly clear"),
    ((2, 2), "Partly cloudy"),
    ((3, 3), "Overcast"),
    ((45, 48), "Fog"),
    ((51, 57), "Drizzle"),
    ((61, 67), "Rain"),
    ((71, 77), "Snow"),
    ((80, 82), "Rain showers"),
    ((85, 86), "Snow showers"),
    ((95, 99), "Thunderstorm"),
]


class WeatherAction(BaseAction):
    """Get current weather and a 3-day forecast for any city."""

    @property
    def name(self) -> str:
        return "weather"

    @property
    def description(self) -> str:
        return "Get current weather and forecast for any city"

    @staticmethod
    def _weather_code_to_text(code: Any) -> str:
        if not isinstance(code, int):
            return "Unknown"
        for (low, high), text in _WMO:
            if low <= code <= high:
                return text
        return "Unknown"

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if params.get("action") != "get_weather":
            return {"success": False, "message": f"Unknown action: {params.get('action')}"}

        city = (params.get("city") or "").strip()
        if not city:
            return {"success": False, "message": "City parameter is required."}

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                geo = await client.get(GEOCODE_URL, params={"name": city, "count": 1})
                geo.raise_for_status()
                results = geo.json().get("results")
                if not results:
                    return {"success": False, "not_found": True, "message": f"City '{city}' not found."}

                loc = results[0]
                weather = await client.get(
                    FORECAST_URL,
                    params={
                        "latitude": loc["latitude"],
                        "longitude": loc["longitude"],
                        "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                        "timezone": "auto",
                        "forecast_days": 3,
                    },
                )
                weather.raise_for_status()
                data = weather.json()
        except httpx.HTTPError as e:
            return {"success": False, "message": f"Weather service error: {e}"}
        except Exception as e:
            return {"success": False, "message": f"Unexpected weather error: {e}"}

        current = data.get("current", {})
        daily = data.get("daily", {})
        current_weather = {
            "temp": current.get("temperature_2m"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "condition": self._weather_code_to_text(current.get("weather_code")),
        }

        times = daily.get("time", [])
        highs = daily.get("temperature_2m_max", [])
        lows = daily.get("temperature_2m_min", [])
        codes = daily.get("weather_code", [])
        forecast = [
            {
                "date": times[i],
                "max_temp": highs[i] if i < len(highs) else None,
                "min_temp": lows[i] if i < len(lows) else None,
                "condition": self._weather_code_to_text(codes[i] if i < len(codes) else None),
            }
            for i in range(len(times))
        ]

        resolved = ", ".join(p for p in (loc.get("name"), loc.get("country")) if p)
        return {
            "success": True,
            "city": resolved,
            "current": current_weather,
            "forecast": forecast,
            "message": f"{resolved}: {current_weather['temp']}°C, {current_weather['condition']}",
        }

"""
S.I.M.O.N. Plugin — Weather
============================
Get current weather and forecasts for any city.
Uses wttr.in — free, no API key needed.

Voice commands:
  "Simon, what's the weather in [CITY]?"
  "Simon, will it rain in Miami today?"
  "Simon, 3-day forecast for Seattle"
"""

import httpx
import asyncio

METADATA = {
    "name":        "Weather",
    "description": "Current weather and forecasts via wttr.in (no API key)",
    "version":     "1.0",
    "author":      "S.I.M.O.N.",
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Get current weather conditions for any city. Use when asked "
                "'what's the weather', 'will it rain', 'temperature in [city]', "
                "'forecast for [city]', 'is it cold in [city]'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name, e.g. '[CITY]', 'New York', 'London UK'"
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of forecast days: 1 = today only, 3 = 3-day forecast (default 1)"
                    }
                },
                "required": ["city"]
            }
        }
    }
]


async def execute(name: str, args: dict) -> str:
    if name != "get_weather":
        return None  # not our tool

    city = args.get("city", "").strip()
    days = min(int(args.get("days", 1)), 3)

    if not city:
        return "No city specified."

    try:
        # wttr.in returns clean JSON weather data — free, no auth
        url = f"https://wttr.in/{city.replace(' ', '+')}?format=j1"
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url, headers={"User-Agent": "SIMON/4.2"})
            resp.raise_for_status()
            data = resp.json()

        current = data["current_condition"][0]
        area    = data["nearest_area"][0]

        city_name  = area["areaName"][0]["value"]
        region     = area.get("region", [{}])[0].get("value", "")
        country    = area.get("country", [{}])[0].get("value", "")
        location   = f"{city_name}, {region}" if region else f"{city_name}, {country}"

        temp_f     = current["temp_F"]
        feels_f    = current["FeelsLikeF"]
        humidity   = current["humidity"]
        wind_mph   = current["windspeedMiles"]
        wind_dir   = current["winddir16Point"]
        desc       = current["weatherDesc"][0]["value"]
        visibility = current["visibility"]

        lines = [
            f"{location}: {desc}",
            f"{temp_f}°F (feels like {feels_f}°F)",
            f"Humidity {humidity}% | Wind {wind_mph}mph {wind_dir} | Visibility {visibility}mi",
        ]

        # Add forecast days if requested
        if days > 1:
            weather_days = data.get("weather", [])[:days]
            day_names = ["Today", "Tomorrow", "Day after tomorrow"]
            for i, day in enumerate(weather_days):
                if i == 0:
                    continue  # already showing current
                label    = day_names[i] if i < len(day_names) else f"Day {i+1}"
                max_f    = day["maxtempF"]
                min_f    = day["mintempF"]
                day_desc = day["hourly"][4]["weatherDesc"][0]["value"]  # midday
                lines.append(f"{label}: {day_desc}, {min_f}–{max_f}°F")

        return " | ".join(lines)

    except httpx.TimeoutException:
        return f"Weather request timed out for {city}."
    except httpx.HTTPStatusError as e:
        return f"Weather service returned error {e.response.status_code} for {city}."
    except Exception as e:
        return f"Could not get weather for {city}: {e}"

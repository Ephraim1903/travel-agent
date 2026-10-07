"""
My Travel Agent - interactive web version (v2)

Run:
    cd "C:\\travel agent"
    set GEMINI_API_KEY=your-key-here
    python app.py
"""

import os
import re
import json
import math
import time
import socket
import datetime
import threading
import webbrowser
from urllib.parse import quote_plus

import requests
from flask import Flask, request, jsonify, Response
from google import genai
from google.genai import types

WEATHER_CODES = {
    0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow",
    75: "Heavy snow", 80: "Rain showers", 81: "Rain showers",
    82: "Violent rain showers", 95: "Thunderstorm",
    96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


# =====================================================================
# TOOLS (used by the chat agent AND by the buttons on the web page)
# =====================================================================

# ---------- TOOL 1: WEATHER ----------
def get_weather(city: str, days: int = 5) -> dict:
    """Get the daily weather forecast for a city or travel destination.

    Args:
        city: Name of the city or place, for example "Munnar" or "Goa".
        days: Number of forecast days, from 1 to 7.
    """
    try:
        days = max(1, min(int(days), 7))
        geo = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1},
            timeout=10,
        ).json()
        if not geo.get("results"):
            return {"error": f"Could not find a place called '{city}'."}
        place = geo["results"][0]

        forecast = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "forecast_days": days,
            },
            timeout=10,
        ).json()
        d = forecast["daily"]

        result = []
        for i in range(len(d["time"])):
            result.append({
                "date": d["time"][i],
                "code": d["weather_code"][i],
                "conditions": WEATHER_CODES.get(d["weather_code"][i], "Unknown"),
                "max_temp_c": d["temperature_2m_max"][i],
                "min_temp_c": d["temperature_2m_min"][i],
                "rain_chance_percent": d["precipitation_probability_max"][i],
            })
        return {
            "place": f"{place['name']}, {place.get('country', '')}",
            "forecast": result,
        }
    except Exception as e:
        return {"error": f"Could not get weather right now: {e}"}


# ---------- TOOL 2: PLACES ----------
def find_places(destination: str, interest: str = "top tourist attractions") -> dict:
    """Get a Google Maps link to explore places to visit in a destination.
    After calling this, also suggest specific well-known places from your own
    knowledge that match the traveler's interests.

    Args:
        destination: The city or region, for example "Goa".
        interest: What the traveler likes, for example "beaches", "temples",
            "food", "trekking". Defaults to top tourist attractions.
    """
    query = f"{interest} in {destination}"
    return {
        "maps_link": "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query),
        "note": "Link shows these places on a map with photos and reviews.",
    }


# ---------- TOOL 3: HOTELS ----------
def find_hotels(destination: str, checkin: str = "", checkout: str = "", budget: str = "any") -> dict:
    """Get hotel search links for a destination.
    After calling this, also suggest a few well-known areas to stay in.

    Args:
        destination: The city or region, for example "Munnar".
        checkin: Check-in date as YYYY-MM-DD. Leave empty if unknown.
        checkout: Check-out date as YYYY-MM-DD. Leave empty if unknown.
        budget: Traveler budget, for example "budget", "mid-range", "luxury", or "any".
    """
    booking = "https://www.booking.com/searchresults.html?ss=" + quote_plus(destination)
    if checkin and checkout:
        booking += f"&checkin={checkin}&checkout={checkout}"

    maps_query = f"hotels in {destination}" if budget == "any" else f"{budget} hotels in {destination}"
    return {
        "booking_link": booking,
        "maps_link": "https://www.google.com/maps/search/?api=1&query=" + quote_plus(maps_query),
        "note": "Links open live search results with real prices and availability.",
    }


# ---------- TOOL 4: TICKETS ----------
def search_tickets(origin: str, destination: str, date: str, mode: str = "flight", return_date: str = "") -> dict:
    """Get a ticket search link for travel between two places, pre-filled as
    far as each site allows. This does NOT book or pay for tickets; the
    traveler finishes the booking and payment on that site themselves.

    Args:
        origin: Starting city, for example "Coimbatore".
        destination: Destination city, for example "Goa".
        date: Travel date as YYYY-MM-DD.
        mode: One of "flight", "train", or "bus".
        return_date: Optional return date as YYYY-MM-DD, for a round trip.
    """
    mode = mode.lower().strip()
    if mode == "flight":
        query = f"Flights from {origin} to {destination} on {date}"
        if return_date:
            query += f", returning {return_date}"
        link = "https://www.google.com/travel/flights?q=" + quote_plus(query)
        note = "Google Flights, already searched for your route and dates. Pick a flight and pay on the airline or Google's checkout."
    elif mode == "train":
        link = "https://www.irctc.co.in/nget/train-search"
        note = (f"IRCTC does not allow pre-filled or automated search links (it blocks bots by design), "
                f"so open it and search {origin} to {destination} on {date} yourself, then pay there.")
    elif mode == "bus":
        link = "https://www.redbus.in"
        note = f"redBus doesn't support pre-filled links either, so search {origin} to {destination} on {date} there, then pay there."
    else:
        return {"error": "Mode must be flight, train, or bus."}
    return {"mode": mode, "link": link, "note": note}


# ---------- TOOL 5: ROUTES ----------
def get_route(origin: str, destination: str, stops: str = "", travel_mode: str = "driving") -> dict:
    """Get a Google Maps route link between places, optionally with stops in between.
    Use it for the trip to the destination and for routes between places inside it.

    Args:
        origin: Starting place, for example "Panaji, Goa".
        destination: Final place, for example "Palolem Beach, Goa".
        stops: Optional places to pass through in order, separated by the
            | character, for example "Baga Beach|Fort Aguada". Maximum 9.
        travel_mode: One of "driving", "walking", "transit", or "bicycling".
    """
    travel_mode = travel_mode.lower().strip()
    if travel_mode not in ("driving", "walking", "transit", "bicycling"):
        return {"error": "travel_mode must be driving, walking, transit, or bicycling."}

    link = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote_plus(origin)}"
        f"&destination={quote_plus(destination)}"
        f"&travelmode={travel_mode}"
    )
    stop_list = [s.strip() for s in stops.split("|") if s.strip()][:9]
    if stop_list:
        link += "&waypoints=" + quote_plus("|".join(stop_list))
    return {"route_link": link, "note": "Opens turn-by-turn directions in Google Maps."}


# ---------- TOOL 6: CURRENCY ----------
def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert money from one currency to another using current exchange rates.

    Args:
        amount: The amount of money to convert, for example 5000.
        from_currency: 3-letter currency code to convert from, for example "INR".
        to_currency: 3-letter currency code to convert to, for example "USD".
    """
    f = from_currency.upper().strip()
    t = to_currency.upper().strip()
    try:
        amount = float(amount)
    except Exception:
        return {"error": "Amount must be a number."}

    if f == t:
        return {"amount": amount, "from": f, "to": t, "rate_for_1_unit": 1.0,
                "converted_amount": round(amount, 2), "rate_date": "today",
                "note": "Same currency."}

    sources = [
        ("https://api.frankfurter.dev/v1/latest", {"base": f, "symbols": t}),
        ("https://api.frankfurter.app/latest", {"from": f, "to": t}),
    ]
    for url, params in sources:
        try:
            data = requests.get(url, params=params, timeout=10).json()
            rate = data["rates"][t]
            return {
                "amount": amount,
                "from": f,
                "to": t,
                "rate_for_1_unit": rate,
                "converted_amount": round(amount * rate, 2),
                "rate_date": data.get("date", "unknown"),
                "note": "Reference rates. Banks and cards charge slightly different rates.",
            }
        except Exception:
            continue
    return {"error": f"Could not get a rate for {f} to {t}. Check the currency codes."}


# ---------- TOOL 7: BUDGET ----------
def calculate_budget(
    days: int,
    nights: int,
    travelers: int,
    hotel_per_night_per_room: float,
    food_per_person_per_day: float,
    local_transport_per_day: float,
    activities_per_person_per_day: float,
    travel_tickets_total: float = 0,
    currency: str = "INR",
) -> dict:
    """Add up a trip budget from estimated costs. This tool only does the math.
    The cost numbers are rough estimates, so tell the traveler that.

    Args:
        days: Number of trip days.
        nights: Number of hotel nights.
        travelers: Number of people traveling.
        hotel_per_night_per_room: Estimated hotel price per night for one room.
        food_per_person_per_day: Estimated food cost per person per day.
        local_transport_per_day: Estimated local transport cost per day for the group.
        activities_per_person_per_day: Estimated activity and entry fees per person per day.
        travel_tickets_total: Total cost of tickets to get there and back, if known.
        currency: Currency code for all the numbers, for example "INR".
    """
    try:
        rooms = math.ceil(int(travelers) / 2)
        hotel = int(nights) * rooms * float(hotel_per_night_per_room)
        food = int(days) * int(travelers) * float(food_per_person_per_day)
        transport = int(days) * float(local_transport_per_day)
        activities = int(days) * int(travelers) * float(activities_per_person_per_day)
        tickets = float(travel_tickets_total)
        subtotal = hotel + food + transport + activities + tickets
        buffer = subtotal * 0.10
        return {
            "currency": currency,
            "rooms_assumed": rooms,
            "hotel": round(hotel),
            "food": round(food),
            "local_transport": round(transport),
            "activities": round(activities),
            "tickets": round(tickets),
            "subtotal": round(subtotal),
            "safety_buffer_10_percent": round(buffer),
            "total_with_buffer": round(subtotal + buffer),
            "per_person_with_buffer": round((subtotal + buffer) / int(travelers)),
        }
    except Exception as e:
        return {"error": f"Could not calculate the budget: {e}"}


# ---------- TOOL 8: PACKING LIST ----------
def make_packing_list(city: str, days: int = 5) -> dict:
    """Build a packing list based on the real weather forecast for a destination.

    Args:
        city: The destination, for example "Goa".
        days: Number of forecast days to check, from 1 to 7.
    """
    w = get_weather(city, days)
    if "error" in w:
        return w
    try:
        f = w["forecast"]
        max_t = max(d["max_temp_c"] for d in f)
        min_t = min(d["min_temp_c"] for d in f)
        rain = max((d["rain_chance_percent"] or 0) for d in f)

        items = [
            "Government photo ID",
            "Phone, charger and power bank",
            "Cash and cards",
            "Any regular medicines",
            "Basic toiletries",
            "Reusable water bottle",
            "Comfortable walking shoes",
        ]
        if max_t >= 30:
            items += ["Light cotton clothes", "Sunscreen", "Sunglasses and a hat"]
        if min_t <= 15:
            items += ["Warm jacket", "Layers such as sweaters"]
        elif min_t <= 22:
            items += ["Light jacket or sweater for evenings"]
        if rain >= 40:
            items += ["Umbrella or raincoat", "Waterproof bag or phone pouch",
                      "Quick-dry clothes", "Sandals that can get wet"]
        return {
            "place": w["place"],
            "based_on": f"Forecast for the next {len(f)} days from today. For trips further away, treat it as a rough guide.",
            "temperature_range_c": f"{min_t} to {max_t}",
            "highest_rain_chance_percent": rain,
            "packing_list": items,
        }
    except Exception as e:
        return {"error": f"Could not build the packing list: {e}"}


# =====================================================================
# THE AI BRAIN (with automatic switching between free models)
# =====================================================================
MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]
model_index = 0

client = genai.Client()
today = datetime.date.today().isoformat()

SYSTEM = (
    f"You are a friendly, fun travel agent. Today's date is {today}. "
    "Use your tools to get real data instead of guessing. "
    "Never invent prices, availability, or flight/train times, and never "
    "claim you booked anything. For tickets and hotels, share the links "
    "from your tools and explain that the traveler completes the booking "
    "on that site. If you need something important (like the starting "
    "city or travel date), ask one short question. "
    "When you make a budget, use the calculate_budget tool and clearly say the "
    "cost numbers are rough estimates. Use convert_currency for money conversions. "
    "Use make_packing_list for packing advice. "
    "If asked to save a plan, tell the traveler to click the 'Save this plan' "
    "button under your answer. "
    "Show links as plain URLs, never as markdown links. Keep answers clear "
    "and easy to read."
)

TOOLS = [get_weather, find_places, find_hotels, search_tickets, get_route,
         convert_currency, calculate_budget, make_packing_list]


def new_chat():
    return client.chats.create(
        model=MODELS[model_index],
        config=types.GenerateContentConfig(system_instruction=SYSTEM, tools=TOOLS),
    )


chat = new_chat()


def _classify(text: str):
    used_up = "PerDay" in text or "NOT_FOUND" in text or "404" in text
    busy = any(c in text for c in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
    return used_up, busy


AGENT_DEADLINE_SECONDS = 25  # never let one agent call hang longer than this


def ask(message: str) -> str:
    """Send a chat message. Switches model immediately if one is busy or
    used up, no artificial waiting, and never runs past a time budget."""
    global chat, model_index
    deadline = time.monotonic() + AGENT_DEADLINE_SECONDS
    while True:
        try:
            reply = chat.send_message(message)
            return reply.text or "(No answer came back. Try rephrasing.)"
        except Exception as e:
            text = str(e)
            used_up, busy = _classify(text)

            if (used_up or busy) and time.monotonic() < deadline:
                if model_index + 1 < len(MODELS):
                    model_index += 1
                    print(f"(Switching to {MODELS[model_index]}...)")
                    chat = new_chat()
                    continue
                return ("All the free models are busy or used up right now. "
                        "Wait a few minutes and try again.")
            if used_up or busy:
                return "That's taking too long right now. Please try again in a minute."

            return f"Something went wrong: {text}"


def generate_json(prompt: str, system: str):
    """One single AI request that must answer in JSON. Switches model
    immediately on failure, no artificial waiting, with a time budget so
    this never hangs long enough to trigger a server timeout."""
    global model_index
    deadline = time.monotonic() + AGENT_DEADLINE_SECONDS
    while True:
        try:
            reply = client.models.generate_content(
                model=MODELS[model_index],
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                ),
            )
            if reply.text:
                return reply.text, None
            return None, "The AI sent back an empty answer."
        except Exception as e:
            text = str(e)
            used_up, busy = _classify(text)

            if (used_up or busy) and time.monotonic() < deadline:
                if model_index + 1 < len(MODELS):
                    model_index += 1
                    print(f"(Switching to {MODELS[model_index]}...)")
                    continue
                return None, ("All the free AI models are busy or used up right now. "
                              "Wait a few minutes and try again.")
            if used_up or busy:
                return None, "That's taking too long right now. Please try again in a minute."
            return None, f"The AI hit a problem: {text}"


def parse_json_loose(text: str):
    """Turn the AI's answer into a Python dict, even if it added extra text."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        data = json.loads(t)
    except Exception:
        a, b = t.find("{"), t.rfind("}")
        if a == -1 or b <= a:
            return None
        try:
            data = json.loads(t[a:b + 1])
        except Exception:
            return None
    return data if isinstance(data, dict) else None


BUDGET_DEFAULTS = {
    "budget": {"hotel": 1500, "food": 500, "transport": 600, "activities": 300},
    "mid-range": {"hotel": 3500, "food": 900, "transport": 1200, "activities": 600},
    "luxury": {"hotel": 9000, "food": 2000, "transport": 3000, "activities": 1500},
}


def clean_number(value, fallback, low=0, high=200000):
    try:
        n = float(value)
        if low <= n <= high:
            return n
    except Exception:
        pass
    return fallback


# =====================================================================
# AGENT 1: THE INTAKE AGENT
# Reads whatever the traveler typed in plain words and turns it into
# clean, structured trip details. Fills in anything they left out.
# =====================================================================
INTAKE_SYSTEM = (
    "You are the Intake Agent on a travel-planning team. Your only job is to read "
    "what a traveler wrote and turn it into clean structured trip details for your "
    "teammates (a Destination Scout and a Trip Planner) to use next. "
    "Answer with valid JSON only, no markdown, no extra text."
)


def extract_trip_details(free_text: str, origin: str, travelers: int, today_iso: str):
    """Ask the Intake Agent to read the traveler's free-text request and fill in
    any missing details (destination, dates, budget level, interests)."""
    prompt = f"""Today's date is {today_iso}. A traveler starting from "{origin}" with
{travelers} traveler(s) wrote this about the trip they want:

\"\"\"{free_text}\"\"\"

Work out the trip details. Rules:
- destination: the place they want to go, written as a real city or region name.
  If they did not name one, or said something vague like "somewhere nice" or
  "a beach", leave this as an empty string "" so a Destination Scout can suggest places.
- start_date / end_date: ISO YYYY-MM-DD. If they gave exact dates, use those.
  If they gave a rough time ("next weekend", "in December"), pick sensible real
  dates in the future for that. If they gave nothing at all, pick a start date
  about 14 days from today and a 3-day trip.
- Never pick a start date before {today_iso}. Keep the whole trip to 7 days or fewer.
- budget_level: one of "budget", "mid-range", "luxury". Guess from their wording,
  default to "mid-range" if unclear.
- interests: a short list (up to 5) of what they care about, e.g. "beaches",
  "food", "nightlife", "heritage", "nature", "relaxing", "adventure".
- trip_vibe: one short friendly sentence describing the trip in your own words.

Return exactly this JSON shape:
{{"destination": "", "start_date": "{today_iso}", "end_date": "{today_iso}",
  "budget_level": "mid-range", "interests": [], "trip_vibe": ""}}"""
    text, err = generate_json(prompt, INTAKE_SYSTEM)
    data = parse_json_loose(text) if text else None
    return data, err


# =====================================================================
# AGENT 2: THE DESTINATION SCOUT AGENT
# Only called when the traveler didn't name a destination. Suggests
# real places that match their interests, origin and budget.
# =====================================================================
SCOUT_SYSTEM = (
    "You are the Destination Scout on a travel-planning team. Your only job is to "
    "suggest real, well-known destinations that fit what the traveler wants. "
    "Answer with valid JSON only, no markdown, no extra text."
)


def suggest_destinations(origin: str, travelers: int, budget_level: str, interests, trip_vibe: str, days: int):
    """Ask the Scout Agent for 3 real destination options."""
    prompt = f"""A traveler starting from "{origin}" wants a {days}-day trip for
{travelers} traveler(s), budget level "{budget_level}", interested in:
{', '.join(interests) if interests else 'a balanced mix of things'}.
They described it as: "{trip_vibe}"

Suggest exactly 3 real destinations reachable from {origin} that fit this. Prefer
places popular with Indian travelers unless the interests clearly point elsewhere.

Return exactly this JSON shape:
{{"options": [
  {{"destination": "City, Region", "why": "one short friendly sentence on why it fits",
    "best_for": "3 or 4 word tag, e.g. 'Beaches and nightlife'"}}
]}}"""
    text, err = generate_json(prompt, SCOUT_SYSTEM)
    data = parse_json_loose(text) if text else None
    return data, err


CURRENCY_HINTS = [
    (("dubai", "abu dhabi", "sharjah", "uae"), "AED"),
    (("bangkok", "phuket", "pattaya", "thailand", "chiang mai"), "THB"),
    (("singapore",), "SGD"),
    (("bali", "jakarta", "indonesia"), "IDR"),
    (("colombo", "sri lanka"), "LKR"),
    (("maldives", "male"), "MVR"),
    (("kuala lumpur", "malaysia", "langkawi"), "MYR"),
    (("london", "uk", "england", "scotland"), "GBP"),
    (("paris", "france", "rome", "italy", "spain", "germany", "europe"), "EUR"),
    (("new york", "usa", "america", "united states"), "USD"),
    (("tokyo", "japan"), "JPY"),
    (("sydney", "melbourne", "australia"), "AUD"),
    (("nepal", "kathmandu"), "NPR"),
    (("bhutan",), "BTN"),
]


def guess_currency(destination: str) -> str:
    low = (destination or "").lower()
    for keywords, code in CURRENCY_HINTS:
        if any(k in low for k in keywords):
            return code
    return "INR"


# =====================================================================
# THE WEB APP
# =====================================================================
app = Flask(__name__)
lock = threading.Lock()
PLANS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trip_plans")


def bad(message, code=400):
    return jsonify(error=message), code


# Safety net: if anything unexpected breaks anywhere in the app, send back
# clean JSON instead of a blank or HTML error page the browser can't read.
@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.errorhandler(Exception)
def handle_any_error(e):
    import traceback
    print("UNHANDLED ERROR:", repr(e))
    traceback.print_exc()
    return jsonify(error="Something went wrong on the server. Please try again in a moment."), 500


@app.route("/")
def home():
    return Response(PAGE, mimetype="text/html")


# ---------- chat ----------
@app.route("/chat", methods=["POST"])
def chat_route():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify(reply="Please type a message.")
    with lock:
        reply = ask(message)
    return jsonify(reply=reply)


@app.route("/reset", methods=["POST"])
def reset_route():
    global chat
    with lock:
        chat = new_chat()
    return jsonify(ok=True)


# ---------- quick tools (no AI used, so they never use up your daily limit) ----------
@app.route("/api/weather")
def api_weather():
    city = request.args.get("city", "").strip()
    if not city:
        return bad("Enter a city to check the weather.")
    result = get_weather(city, request.args.get("days", 5))
    if "error" in result:
        return bad(result["error"])
    return jsonify(result)


@app.route("/api/packing")
def api_packing():
    city = request.args.get("city", "").strip()
    if not city:
        return bad("Enter a destination to build the packing list.")
    result = make_packing_list(city, request.args.get("days", 7))
    if "error" in result:
        return bad(result["error"])
    return jsonify(result)


@app.route("/api/currency")
def api_currency():
    try:
        amount = float(request.args.get("amount", "1"))
    except ValueError:
        return bad("Amount must be a number.")
    if amount < 0 or amount > 1e12:
        return bad("Enter an amount between 0 and 1,000,000,000,000.")
    result = convert_currency(amount, request.args.get("from", "INR"), request.args.get("to", "USD"))
    if "error" in result:
        return bad(result["error"])
    return jsonify(result)


@app.route("/api/route", methods=["POST"])
def api_route():
    d = request.get_json(silent=True) or {}
    origin = str(d.get("origin", "")).strip()[:120]
    destination = str(d.get("destination", "")).strip()[:120]
    if not origin or not destination:
        return bad("Enter both a start and an end place.")
    stops = [str(s).strip()[:120] for s in (d.get("stops") or []) if str(s).strip()][:9]
    result = get_route(origin, destination, "|".join(stops), str(d.get("mode", "driving")))
    if "error" in result:
        return bad(result["error"])
    return jsonify(result)


def build_links(origin, destination, start, end, budget, interests):
    places = []
    for interest in (interests or ["top tourist attractions"]):
        places.append({"interest": interest, "link": find_places(destination, interest)["maps_link"]})
    hotels = find_hotels(destination, start, end, budget)
    flight = search_tickets(origin, destination, start, "flight", return_date=end)
    train = search_tickets(origin, destination, start, "train")
    bus = search_tickets(origin, destination, start, "bus")
    return {
        "flight": flight["link"], "flight_note": flight["note"],
        "train": train["link"], "train_note": train["note"],
        "bus": bus["link"], "bus_note": bus["note"],
        "hotels_booking": hotels["booking_link"],
        "hotels_maps": hotels["maps_link"],
        "places": places,
    }


# ---------- the big one: plan my trip (coordinates 2-3 agents) ----------
@app.route("/api/plan", methods=["POST"])
def api_plan():
    d = request.get_json(silent=True) or {}
    name = str(d.get("name", "")).strip()[:60]
    origin = str(d.get("origin", "")).strip()[:80]
    free_text = str(d.get("free_text", "")).strip()[:600]
    destination = str(d.get("destination", "")).strip()[:80]
    if not origin:
        return bad("Tell us which city you're starting from.")
    if not destination and not free_text:
        return bad("Tell us a destination, or describe the trip you want.")

    try:
        travelers = max(1, min(int(d.get("travelers", 2)), 20))
    except (TypeError, ValueError):
        travelers = 2

    start_in = str(d.get("start", "")).strip()
    end_in = str(d.get("end", "")).strip()
    budget_in = str(d.get("budget", "")).strip()
    interests_in = [str(i).strip()[:40] for i in (d.get("interests") or []) if str(i).strip()][:6]

    # ----- AGENT 1: INTAKE AGENT -----
    # Only needed when the traveler hasn't already given us everything
    # structured (e.g. this is the first call, or free_text changed).
    intake_error = None
    trip_vibe = ""
    if not (destination and start_in and end_in and budget_in):
        with lock:
            parsed, intake_error = extract_trip_details(
                free_text or destination, origin, travelers, today
            )
        if parsed:
            destination = destination or str(parsed.get("destination", "")).strip()[:80]
            start_in = start_in or str(parsed.get("start_date", "")).strip()
            end_in = end_in or str(parsed.get("end_date", "")).strip()
            budget_in = budget_in or str(parsed.get("budget_level", "")).strip()
            if not interests_in:
                interests_in = [str(i).strip()[:40] for i in (parsed.get("interests") or []) if str(i).strip()][:6]
            trip_vibe = str(parsed.get("trip_vibe", ""))[:300]

    budget = budget_in if budget_in in BUDGET_DEFAULTS else "mid-range"
    interests = interests_in

    try:
        start_date = datetime.date.fromisoformat(start_in)
    except ValueError:
        start_date = datetime.date.today() + datetime.timedelta(days=14)
    try:
        end_date = datetime.date.fromisoformat(end_in)
    except ValueError:
        end_date = start_date + datetime.timedelta(days=2)
    if end_date < start_date:
        end_date = start_date + datetime.timedelta(days=2)
    if start_date < datetime.date.today():
        shift = datetime.date.today() - start_date
        start_date += shift
        end_date += shift
    days = min((end_date - start_date).days + 1, 7)
    end_date = start_date + datetime.timedelta(days=days - 1)
    start, end = start_date.isoformat(), end_date.isoformat()

    # ----- AGENT 2: DESTINATION SCOUT AGENT -----
    # Only called when nobody (not the traveler, not the Intake Agent)
    # could pin down an actual destination.
    if not destination:
        with lock:
            scouted, scout_error = suggest_destinations(origin, travelers, budget, interests, trip_vibe, days)
        options = []
        if scouted and isinstance(scouted.get("options"), list):
            for o in scouted["options"][:3]:
                if not isinstance(o, dict):
                    continue
                dest = str(o.get("destination", "")).strip()[:80]
                if dest:
                    options.append({
                        "destination": dest,
                        "why": str(o.get("why", ""))[:200],
                        "best_for": str(o.get("best_for", ""))[:60],
                    })
        if not options:
            return bad(scout_error or "Could not think of destinations right now. Try naming one yourself.")
        return jsonify(
            type="suggestions",
            name=name, origin=origin, travelers=travelers,
            start=start, end=end, days=days, budget=budget,
            interests=interests, trip_vibe=trip_vibe, options=options,
        )

    weather = get_weather(destination, 7)
    packing = make_packing_list(destination, 7)
    links = build_links(origin, destination, start, end, budget, interests)

    weather_note = "Weather forecast is not available."
    if "error" not in weather:
        weather_note = "; ".join(
            f"{x['date']}: {x['conditions']}, {x['min_temp_c']} to {x['max_temp_c']} C, "
            f"rain chance {x['rain_chance_percent']}%"
            for x in weather["forecast"]
        )

    system = ("You are the Trip Planner Agent on a travel-planning team. The Intake "
              "Agent (and maybe a Destination Scout) have already confirmed the trip "
              "details below; your job is only to design the itinerary. "
              "Answer with valid JSON only, with no markdown and no extra text.")
    prompt = f"""Plan a trip.
Starting from: {origin}
Destination: {destination}
Dates: {start} to {end} ({days} days)
Travelers: {travelers}
Budget level: {budget}
Interests: {', '.join(interests) if interests else 'a balanced mix'}
Forecast for the next 7 days from today ({today}): {weather_note}

Rules:
- Use only real, well-known places. Write every place as "Place name, {destination}" so a map can find it.
- Give exactly {days} days. Each day has 3 or 4 places, ordered so the route between them is short.
- If the forecast shows rain, prefer indoor or sheltered options on those days.
- Do not invent prices, opening hours, or train or flight times.
- budget_estimates_inr are rough numbers in Indian rupees: hotel price per night for one room, food per person per day, local transport per day for the whole group, activities per person per day.

Return exactly this JSON shape:
{{
  "summary": "two friendly sentences about the trip",
  "stay_area": "the best area to stay and why, in one or two sentences",
  "days": [
    {{"day": 1, "title": "short day title", "places": ["Place, {destination}", "Place, {destination}", "Place, {destination}"], "notes": "one or two practical sentences"}}
  ],
  "tips": ["three to five short practical tips"],
  "budget_estimates_inr": {{"hotel_per_night_per_room": 0, "food_per_person_per_day": 0, "local_transport_per_day": 0, "activities_per_person_per_day": 0}}
}}"""

    with lock:
        text, ai_error = generate_json(prompt, system)

    plan = parse_json_loose(text) if text else None
    if text and plan is None:
        ai_error = "The AI's answer could not be read. Press Plan my trip again."

    clean_days, day_routes = [], []
    estimates = {}
    if plan:
        for i, day in enumerate((plan.get("days") or [])[:days]):
            if not isinstance(day, dict):
                continue
            places = [str(p).strip()[:120] for p in (day.get("places") or []) if str(p).strip()][:6]
            clean_days.append({
                "day": i + 1,
                "title": str(day.get("title", f"Day {i + 1}"))[:100],
                "places": places,
                "notes": str(day.get("notes", ""))[:400],
            })
            link = None
            if len(places) >= 2:
                r = get_route(places[0], places[-1], "|".join(places[1:-1]), "driving")
                link = r.get("route_link")
            day_routes.append(link)
        est = plan.get("budget_estimates_inr")
        estimates = est if isinstance(est, dict) else {}

    base = BUDGET_DEFAULTS[budget]
    budget_defaults = {
        "days": days,
        "nights": max(0, days - 1),
        "travelers": travelers,
        "hotel": clean_number(estimates.get("hotel_per_night_per_room"), base["hotel"], 0, 100000),
        "food": clean_number(estimates.get("food_per_person_per_day"), base["food"], 0, 20000),
        "transport": clean_number(estimates.get("local_transport_per_day"), base["transport"], 0, 50000),
        "activities": clean_number(estimates.get("activities_per_person_per_day"), base["activities"], 0, 30000),
        "tickets": 0,
    }

    tips = []
    if plan and isinstance(plan.get("tips"), list):
        tips = [str(t)[:300] for t in plan["tips"] if str(t).strip()][:6]

    overall_route = get_route(origin, destination, "", "driving").get("route_link")

    return jsonify(
        type="plan",
        name=name,
        trip_vibe=trip_vibe,
        form={"origin": origin, "destination": destination, "start": start, "end": end,
              "days": days, "travelers": travelers, "budget": budget, "interests": interests},
        summary=str(plan.get("summary", ""))[:600] if plan else "",
        stay_area=str(plan.get("stay_area", ""))[:500] if plan else "",
        days=clean_days,
        day_routes=day_routes,
        overall_route=overall_route,
        tips=tips,
        weather=None if "error" in weather else weather,
        weather_error=weather.get("error"),
        packing=None if "error" in packing else packing,
        links=links,
        budget_defaults=budget_defaults,
        currency_guess=guess_currency(destination),
        ai_error=ai_error if not plan else intake_error,
    )


# ---------- saved trips ----------
def slugify(text):
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")[:40] or "trip"


def safe_plan_path(name):
    if not name or os.path.basename(name) != name or not name.endswith(".txt"):
        return None
    return os.path.join(PLANS_DIR, name)


def load_saved(path):
    """Read a saved file. New saves are JSON with the full structured plan
    (so the Saved tab can render it exactly like the Plan tab). Anything
    saved before this feature existed is plain "title\\n\\nbody" text, so
    that old format is still read correctly here."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict) and "text" in payload:
            return {
                "title": str(payload.get("title") or "Trip plan"),
                "kind": payload.get("kind") if payload.get("data") else "text",
                "text": str(payload.get("text") or ""),
                "data": payload.get("data"),
            }
    except Exception:
        pass
    title, _, body = raw.partition("\n\n")
    return {"title": title.strip() or "Trip plan", "kind": "text", "text": body.strip() or raw, "data": None}


@app.route("/api/save", methods=["POST"])
def api_save():
    d = request.get_json(silent=True) or {}
    text = str(d.get("text", "")).strip()
    title = str(d.get("title", "")).strip()[:80] or "Trip plan"
    kind = str(d.get("kind", "text")).strip()
    data = d.get("data") if kind == "plan" and isinstance(d.get("data"), dict) else None
    if not text and not data:
        return bad("There is nothing to save yet.")
    try:
        os.makedirs(PLANS_DIR, exist_ok=True)
        name = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{slugify(title)}.txt"
        payload = {"title": title, "kind": "plan" if data else "text", "text": text, "data": data}
        with open(os.path.join(PLANS_DIR, name), "w", encoding="utf-8") as f:
            json.dump(payload, f)
        return jsonify(ok=True, name=name, message="Saved to the Saved trips tab.")
    except Exception as e:
        return bad(f"Could not save: {e}", 500)


@app.route("/api/plans")
def api_plans():
    items = []
    if os.path.isdir(PLANS_DIR):
        for name in os.listdir(PLANS_DIR):
            path = safe_plan_path(name)
            if not path or not os.path.isfile(path):
                continue
            try:
                payload = load_saved(path)
                items.append({"name": name, "title": payload["title"], "kind": payload["kind"],
                              "saved": os.path.getmtime(path)})
            except Exception:
                continue
    items.sort(key=lambda x: x["saved"], reverse=True)
    for it in items:
        it["saved"] = datetime.datetime.fromtimestamp(it["saved"]).strftime("%d %b %Y, %I:%M %p")
    return jsonify(plans=items)


@app.route("/api/plans/<name>", methods=["GET", "DELETE"])
def api_plan_item(name):
    path = safe_plan_path(name)
    if not path or not os.path.isfile(path):
        return bad("That saved trip was not found.", 404)
    if request.method == "DELETE":
        try:
            os.remove(path)
            return jsonify(ok=True)
        except Exception as e:
            return bad(f"Could not delete: {e}", 500)
    payload = load_saved(path)
    return jsonify(name=name, title=payload["title"], kind=payload["kind"],
                    text=payload["text"], data=payload["data"])


@app.route("/api/plans/<name>/download")
def api_plan_download(name):
    path = safe_plan_path(name)
    if not path or not os.path.isfile(path):
        return bad("That saved trip was not found.", 404)
    payload = load_saved(path)
    content = payload["title"] + ("\n\n" + payload["text"] if payload["text"] else "")
    return Response(content, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# =====================================================================
# THE WEB PAGE
# =====================================================================
PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My Travel Agent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;600;700&family=Sora:wght@600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #f4f0fb; --surface: #ffffff; --ink: #2a1b45; --muted: #6f5f8a;
  --line: #ddd2f0; --primary: #7c3aed; --primary-ink: #ffffff; --sky: #ece3fb;
  --accent: #f3a63b; --danger: #b93a0b; --ok: #1d7a43; --shadow: 0 1px 2px rgba(42,27,69,.08), 0 6px 20px rgba(42,27,69,.08);
  --display: "Sora", "Segoe UI", Arial, sans-serif;
  --body: "Nunito Sans", "Segoe UI", Arial, sans-serif;
}
:root[data-theme="dark"] {
  --bg: #17102b; --surface: #221a3a; --ink: #ede7fb; --muted: #b3a4d6;
  --line: #392c5c; --primary: #a78bfa; --primary-ink: #1c1033; --sky: #2c2350;
  --accent: #f3b24f; --danger: #ff9a6b; --ok: #6fd39a; --shadow: 0 1px 2px rgba(0,0,0,.3), 0 6px 20px rgba(0,0,0,.25);
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--body); font-size: 16px; line-height: 1.55; }
h1, h2, h3 { font-family: var(--display); line-height: 1.2; margin: 0; }
button, input, select, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
a { color: var(--primary); }
:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
.sr { position: absolute; left: -9999px; }

/* layout */
.app { display: grid; grid-template-columns: 220px 1fr; min-height: 100vh; }
.rail { position: sticky; top: 0; height: 100vh; padding: 22px 14px; display: flex; flex-direction: column; gap: 6px;
        background: var(--surface); border-right: 1px solid var(--line); }
.brand { font-family: var(--display); font-weight: 700; font-size: 19px; padding: 4px 10px 18px; }
.brand span { color: var(--primary); }
.nav { display: flex; flex-direction: column; gap: 4px; }
.nav button { display: flex; align-items: center; gap: 12px; padding: 10px 12px; border: 0; background: none;
              border-radius: 10px; text-align: left; font-weight: 600; color: var(--muted); }
.nav button .ic { font-size: 19px; width: 24px; text-align: center; }
.nav button:hover { background: var(--sky); color: var(--ink); }
.nav button[aria-current="page"] { background: var(--primary); color: var(--primary-ink); }
.rail-foot { margin-top: auto; }
.ghost { border: 1px solid var(--line); background: none; padding: 8px 12px; border-radius: 10px; width: 100%; font-weight: 600; }
.ghost:hover { background: var(--sky); }
main { padding: 28px clamp(16px, 4vw, 44px) 120px; max-width: 1100px; width: 100%; }
.tab { display: none; }
.tab.active { display: block; }
.tab > h2 { font-size: 26px; margin-bottom: 4px; }
.lead { color: var(--muted); margin: 0 0 20px; max-width: 62ch; }

/* cards & forms */
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 16px; padding: 20px; box-shadow: var(--shadow); }
.card + .card, .stack > * + * { margin-top: 16px; }
.card h3 { font-size: 18px; margin-bottom: 10px; }
.grid2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 14px; }
.grid3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
label, .lab { display: block; font-weight: 700; font-size: 14px; margin-bottom: 6px; }
input[type=text], input[type=date], input[type=number], select {
  width: 100%; padding: 11px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--bg); min-height: 44px; }
input:focus, select:focus { border-color: var(--primary); }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { padding: 8px 14px; border-radius: 999px; border: 1px solid var(--line); background: var(--surface); font-weight: 600; font-size: 14px; }
.chip:hover { border-color: var(--primary); }
.chip[aria-pressed="true"] { background: var(--primary); border-color: var(--primary); color: var(--primary-ink); }
.btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; padding: 12px 20px; border: 0; border-radius: 12px;
       background: var(--primary); color: var(--primary-ink); font-weight: 700; text-decoration: none; min-height: 46px; }
.btn:hover { filter: brightness(1.08); }
.btn:disabled { opacity: .6; cursor: wait; }
.btn.alt { background: var(--sky); color: var(--ink); }
.btn.small { padding: 8px 14px; min-height: 38px; font-size: 14px; border-radius: 10px; }
.btn.warn { background: none; border: 1px solid var(--line); color: var(--danger); }
.row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
.note { color: var(--muted); font-size: 14px; }
.notice { padding: 12px 14px; border-radius: 12px; background: #fff3dc; color: #5a3a00; border: 1px solid #f0d29b; }
:root[data-theme="dark"] .notice { background: #3a2e12; color: #ffe3a8; border-color: #6b551f; }
.empty { text-align: center; padding: 34px 16px; color: var(--muted); }
.empty b { display: block; color: var(--ink); margin-bottom: 4px; font-family: var(--display); }

/* hero + boarding pass */
.hero { display: grid; grid-template-columns: 1fr minmax(300px, 420px); gap: 28px; align-items: center; margin-bottom: 22px; }
.hero h1 { font-size: clamp(30px, 5vw, 46px); letter-spacing: -.02em; margin-bottom: 10px; }
.hero p { color: var(--muted); margin: 0; max-width: 46ch; }
.pass { background: var(--surface); border: 1px solid var(--line); border-radius: 22px; box-shadow: var(--shadow); position: relative; overflow: hidden; }
.pass-main { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 22px 22px 18px;
             background: linear-gradient(135deg, var(--primary), #4c1d95); color: #fff; }
:root[data-theme="dark"] .pass-main { background: linear-gradient(135deg, #8b5cf6, #3b1d78); }
.pass-leg { min-width: 0; }
.pass-leg .k { display: block; font-size: 13px; opacity: .8; }
.pass-leg strong { display: block; font-family: var(--display); font-size: 22px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px; }
.pass-arrow { font-size: 24px; flex: none; }
.pass-stub { display: grid; grid-template-columns: repeat(3, auto); gap: 12px; justify-content: space-between; padding: 18px 22px 20px; position: relative;
             border-top: 2px dashed var(--line); }
.pass-stub::before, .pass-stub::after { content: ""; position: absolute; top: -12px; width: 22px; height: 22px; border-radius: 50%; background: var(--bg); border: 1px solid var(--line); }
.pass-stub::before { left: -12px; } .pass-stub::after { right: -12px; }
.pass-stub .k { display: block; font-size: 13px; color: var(--muted); }
.pass-stub b { font-family: var(--display); font-size: 15px; }

/* results */
.pill-links { display: flex; flex-wrap: wrap; gap: 10px; }
.wx { display: grid; grid-auto-flow: column; grid-auto-columns: minmax(118px, 1fr); gap: 10px; overflow-x: auto; padding-bottom: 6px; }
.wx-day { background: var(--bg); border: 1px solid var(--line); border-radius: 14px; padding: 12px; text-align: center; }
.wx-day .d { font-weight: 700; font-size: 14px; }
.wx-day .e { font-size: 34px; line-height: 1.3; }
.wx-day .t { font-family: var(--display); font-size: 17px; }
.wx-day .c { font-size: 13px; color: var(--muted); min-height: 20px; }
.bar { height: 6px; border-radius: 6px; background: var(--line); overflow: hidden; margin-top: 8px; }
.bar > i { display: block; height: 100%; background: #3b8fd6; }
.day { border-left: 4px solid var(--primary); }
.day h3 { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.day h3 small { font-family: var(--body); color: var(--muted); font-weight: 600; font-size: 14px; }
.places { list-style: none; margin: 10px 0; padding: 0; display: grid; gap: 6px; }
.places li a { display: block; padding: 8px 12px; border-radius: 10px; background: var(--bg); text-decoration: none; color: var(--ink); border: 1px solid var(--line); }
.places li a:hover { border-color: var(--primary); }
.tips { margin: 0; padding-left: 20px; }
.tips li { margin: 4px 0; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.result-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
.skeleton { padding: 30px; text-align: center; }
.spinner { width: 34px; height: 34px; border-radius: 50%; border: 4px solid var(--line); border-top-color: var(--primary); margin: 0 auto 12px; animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* budget */
.budget-wrap { display: grid; grid-template-columns: 1.1fr .9fr; gap: 16px; align-items: start; }
.slider-row { display: grid; grid-template-columns: 1fr 110px; gap: 6px 12px; align-items: center; margin-bottom: 14px; }
.slider-row label { grid-column: 1 / -1; margin: 0; }
.slider-row input[type=range] { width: 100%; accent-color: var(--primary); }
.total { font-family: var(--display); font-size: 34px; margin: 4px 0; }
.donut { width: 170px; height: 170px; border-radius: 50%; margin: 6px auto 14px; position: relative; }
.donut::after { content: ""; position: absolute; inset: 34px; border-radius: 50%; background: var(--surface); }
.legend { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
.legend li { display: flex; align-items: center; gap: 10px; font-size: 15px; }
.legend i { width: 12px; height: 12px; border-radius: 4px; flex: none; }
.legend span:last-child { margin-left: auto; font-weight: 700; }

/* packing */
.progress { height: 12px; border-radius: 12px; background: var(--line); overflow: hidden; }
.progress > i { display: block; height: 100%; width: 0; background: var(--ok); transition: width .3s; }
.check-list { list-style: none; margin: 14px 0 0; padding: 0; display: grid; gap: 8px; }
.check-list label { display: flex; gap: 12px; align-items: center; padding: 10px 12px; margin: 0; border: 1px solid var(--line); border-radius: 12px; font-weight: 600; cursor: pointer; background: var(--bg); }
.check-list input { width: 20px; height: 20px; accent-color: var(--primary); flex: none; }
.check-list input:checked + span { text-decoration: line-through; color: var(--muted); }

/* currency */
.money { font-family: var(--display); font-size: clamp(30px, 6vw, 46px); margin: 6px 0; overflow-wrap: anywhere; }
.swap { align-self: end; }

/* routes */
.stop-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; margin-bottom: 8px; }

/* saved */
.saved-item { display: flex; gap: 12px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
.saved-item h3 { font-size: 16px; margin: 0; }
.viewer { white-space: normal; }
.viewer h4 { margin: 14px 0 4px; font-family: var(--display); }
.viewer p { margin: 6px 0; }
.viewer ul { margin: 6px 0; padding-left: 20px; }

/* chat drawer */
.fab { position: fixed; right: 22px; bottom: 22px; z-index: 30; border: 0; border-radius: 999px; padding: 14px 20px; font-weight: 700;
       background: var(--accent); color: #2b1a00; box-shadow: 0 8px 24px rgba(0,0,0,.25); }
.drawer { position: fixed; top: 0; right: 0; height: 100%; width: min(430px, 100vw); z-index: 40; background: var(--surface); border-left: 1px solid var(--line);
          display: flex; flex-direction: column; transform: translateX(105%); transition: transform .25s ease; box-shadow: -10px 0 30px rgba(0,0,0,.15); }
.drawer.open { transform: none; }
.drawer-head { display: flex; justify-content: space-between; align-items: center; padding: 14px 16px; border-bottom: 1px solid var(--line); gap: 8px; }
.drawer-head h3 { font-size: 17px; }
.chat-log { flex: 1; overflow-y: auto; padding: 14px; }
.msg { padding: 10px 13px; border-radius: 14px; margin: 8px 0; max-width: 92%; word-wrap: break-word; overflow-wrap: anywhere; }
.msg.user { background: var(--primary); color: var(--primary-ink); margin-left: auto; border-bottom-right-radius: 4px; }
.msg.agent { background: var(--bg); border: 1px solid var(--line); border-bottom-left-radius: 4px; }
.msg p { margin: 4px 0; } .msg ul { margin: 4px 0; padding-left: 20px; } .msg h4 { margin: 8px 0 2px; font-family: var(--display); }
.msg.user a { color: inherit; }
.msg .save-btn { margin-top: 8px; }
.chat-suggest { padding: 0 14px 6px; }
.chat-bar { display: flex; gap: 8px; padding: 12px 14px 16px; border-top: 1px solid var(--line); }
.chat-bar input { flex: 1; }
.scrim { position: fixed; inset: 0; background: rgba(0,0,0,.35); z-index: 35; display: none; }
.scrim.show { display: block; }

/* toasts */
#toasts { position: fixed; left: 50%; transform: translateX(-50%); bottom: 90px; z-index: 60; display: grid; gap: 8px; width: min(420px, 92vw); pointer-events: none; }
.toast { background: #10303a; color: #fff; padding: 12px 16px; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,.3); pointer-events: auto; }
.toast.err { background: #8a2c08; }

/* mobile */
@media (max-width: 860px) {
  .app { grid-template-columns: 1fr; }
  .rail { position: fixed; top: auto; bottom: 0; left: 0; right: 0; height: auto; z-index: 25; flex-direction: row; padding: 6px 4px calc(6px + env(safe-area-inset-bottom));
          border-right: 0; border-top: 1px solid var(--line); }
  .brand, .rail-foot { display: none; }
  .nav { flex-direction: row; width: 100%; justify-content: space-between; }
  .nav button { flex-direction: column; gap: 0; padding: 6px 2px; flex: 1; font-size: 11px; text-align: center; min-width: 0; }
  .nav button .ic { font-size: 20px; }
  .hero, .budget-wrap, .two-col { grid-template-columns: 1fr; }
  .grid3 { grid-template-columns: 1fr 1fr; }
  .fab { bottom: 84px; right: 14px; }
  #toasts { bottom: 150px; }
  main { padding-bottom: 170px; }
}
@media (max-width: 520px) {
  .grid2, .grid3 { grid-template-columns: 1fr; }
  .pass-leg strong { font-size: 18px; max-width: 110px; }
}
@media (prefers-reduced-motion: reduce) {
  * { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="rail">
    <div class="brand">Trip<span>Pilot</span></div>
    <nav class="nav" aria-label="Sections">
      <button data-tab="plan"><span class="ic">🧭</span><span>Plan</span></button>
      <button data-tab="weather"><span class="ic">⛅</span><span>Weather</span></button>
      <button data-tab="routes"><span class="ic">🗺️</span><span>Routes</span></button>
      <button data-tab="budget"><span class="ic">💰</span><span>Budget</span></button>
      <button data-tab="packing"><span class="ic">🎒</span><span>Packing</span></button>
      <button data-tab="currency"><span class="ic">💱</span><span>Currency</span></button>
      <button data-tab="saved"><span class="ic">📁</span><span>Saved</span></button>
    </nav>
    <div class="rail-foot"><button class="ghost" id="themeBtn" type="button">Dark mode</button></div>
  </aside>

  <main>
    <!-- PLAN -->
    <section class="tab" id="tab-plan">
      <div class="hero">
        <div>
          <h1>Where to next?</h1>
          <p>Just tell me about yourself and the trip you're dreaming of. My team of agents will work out the rest: destination ideas, the day-by-day plan, weather, routes, budget and packing.</p>
        </div>
        <div class="pass" aria-live="polite">
          <div class="pass-main">
            <div class="pass-leg"><span class="k">From</span><strong id="pFrom">Start</strong></div>
            <div class="pass-arrow" aria-hidden="true">✈</div>
            <div class="pass-leg" style="text-align:right"><span class="k">To</span><strong id="pTo">Destination</strong></div>
          </div>
          <div class="pass-stub">
            <div><span class="k">Dates</span><b id="pDates">-</b></div>
            <div><span class="k">Days</span><b id="pDays">-</b></div>
            <div><span class="k">Travelers</span><b id="pTrav">2</b></div>
          </div>
        </div>
      </div>

      <form class="card" id="planForm" novalidate>
        <div class="grid2">
          <div><label for="travelerName">Your name</label><input type="text" id="travelerName" placeholder="So I can greet you" autocomplete="off"></div>
          <div><label for="from">Starting from</label><input type="text" id="from" placeholder="Your city" autocomplete="off"></div>
        </div>
        <div style="margin-top:14px">
          <label for="freeText">Tell me about the trip you want</label>
          <textarea id="freeText" rows="3" placeholder="Example: A relaxing 4-day beach trip, mid-range budget, me and my partner, we love good food and nightlife. Or just name a place: &quot;Munnar for 3 days&quot;." style="width:100%;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--bg);resize:vertical;min-height:84px"></textarea>
          <p class="note" style="margin:6px 0 0">No exact destination or dates yet? That's fine, my Scout agent will suggest places and my Intake agent will pick sensible dates.</p>
        </div>
        <details style="margin-top:16px">
          <summary class="lab" style="cursor:pointer">Already know more? Add exact details (optional)</summary>
          <div class="grid3" style="margin-top:14px">
            <div><label for="to">Exact destination</label><input type="text" id="to" placeholder="Leave blank for suggestions" autocomplete="off"></div>
            <div><label for="start">Start date</label><input type="date" id="start"></div>
            <div><label for="end">End date</label><input type="date" id="end"></div>
          </div>
          <div class="grid2" style="margin-top:14px">
            <div><label for="travelers">Travelers</label><input type="number" id="travelers" min="1" max="20" value="2"></div>
            <div>
              <span class="lab">Budget level</span>
              <div class="chips" id="budgetChips" role="group" aria-label="Budget level">
                <button type="button" class="chip" data-v="budget" aria-pressed="false">Budget</button>
                <button type="button" class="chip" data-v="mid-range" aria-pressed="false">Mid-range</button>
                <button type="button" class="chip" data-v="luxury" aria-pressed="false">Luxury</button>
              </div>
            </div>
          </div>
          <div style="margin-top:14px">
            <span class="lab">Interests (pick any)</span>
            <div class="chips" id="interestChips" role="group" aria-label="Interests"></div>
          </div>
        </details>
        <div class="row" style="margin-top:20px">
          <button class="btn" id="planBtn" type="submit">Plan my trip ✨</button>
          <span class="note">My agent team handles the rest. Everything on this page besides planning is free to use as much as you like.</span>
        </div>
      </form>
      <div id="planResult" class="stack" style="margin-top:20px"></div>
    </section>

    <!-- WEATHER -->
    <section class="tab" id="tab-weather">
      <h2>Weather</h2>
      <p class="lead">A 7-day forecast for any place. This uses no AI, so check as often as you like.</p>
      <div class="card">
        <div class="row">
          <div style="flex:1;min-width:200px"><label for="wxCity">Place</label><input type="text" id="wxCity" placeholder="Enter a city"></div>
          <div><label for="wxDays">Days</label>
            <select id="wxDays"><option value="3">3 days</option><option value="5">5 days</option><option value="7" selected>7 days</option></select></div>
          <button class="btn" id="wxBtn" type="button" style="align-self:end">Check weather</button>
        </div>
      </div>
      <div id="wxResult" style="margin-top:16px"></div>
    </section>

    <!-- ROUTES -->
    <section class="tab" id="tab-routes">
      <h2>Route builder</h2>
      <p class="lead">Add places in the order you want to visit them and get one Google Maps route.</p>
      <div class="card">
        <div class="grid2">
          <div><label for="rStart">Start</label><input type="text" id="rStart" placeholder="Where does the route begin?"></div>
          <div><label for="rEnd">End</label><input type="text" id="rEnd" placeholder="Where does it finish?"></div>
        </div>
        <div style="margin-top:14px">
          <span class="lab">Stops on the way (up to 9)</span>
          <div id="stopList"></div>
          <button class="btn alt small" id="addStop" type="button">Add a stop</button>
        </div>
        <div style="margin-top:14px">
          <span class="lab">How are you travelling?</span>
          <div class="chips" id="modeChips" role="group" aria-label="Travel mode">
            <button type="button" class="chip" data-v="driving" aria-pressed="true">Car</button>
            <button type="button" class="chip" data-v="transit" aria-pressed="false">Public transport</button>
            <button type="button" class="chip" data-v="walking" aria-pressed="false">Walking</button>
            <button type="button" class="chip" data-v="bicycling" aria-pressed="false">Bicycle</button>
          </div>
        </div>
        <div class="row" style="margin-top:18px">
          <button class="btn" id="routeBtn" type="button">Build route</button>
        </div>
        <div id="routeResult" style="margin-top:14px"></div>
      </div>
    </section>

    <!-- BUDGET -->
    <section class="tab" id="tab-budget">
      <h2>Budget</h2>
      <p class="lead">Drag the sliders and watch the total change. Every number here is an estimate you can change.</p>
      <div class="budget-wrap">
        <div class="card" id="budgetInputs"></div>
        <div class="card">
          <h3>Estimated total</h3>
          <div class="total" id="bTotal">₹0</div>
          <div class="note" id="bPer">-</div>
          <div class="donut" id="donut" aria-hidden="true"></div>
          <ul class="legend" id="bLegend"></ul>
          <div class="row" style="margin-top:16px">
            <button class="btn alt small" id="bCopy" type="button">Copy summary</button>
            <button class="btn small" id="bSave" type="button">Save budget</button>
          </div>
          <p class="note" style="margin-bottom:0">Includes a 10% safety buffer. Hotel rooms are counted as one room for every 2 travelers.</p>
        </div>
      </div>
    </section>

    <!-- PACKING -->
    <section class="tab" id="tab-packing">
      <h2>Packing checklist</h2>
      <p class="lead">Built from the real forecast for your destination. Tick items as you pack. Your ticks are remembered on this computer.</p>
      <div class="card">
        <div class="row">
          <div style="flex:1;min-width:200px"><label for="pkCity">Destination</label><input type="text" id="pkCity" placeholder="Enter a city"></div>
          <button class="btn" id="pkBtn" type="button" style="align-self:end">Build list</button>
        </div>
      </div>
      <div id="pkResult" style="margin-top:16px"></div>
    </section>

    <!-- CURRENCY -->
    <section class="tab" id="tab-currency">
      <h2>Currency converter</h2>
      <p class="lead">Live reference rates. Your bank or card will charge a slightly different rate.</p>
      <div class="card">
        <div class="grid3" style="grid-template-columns:1.2fr 1fr auto 1fr">
          <div><label for="cAmt">Amount</label><input type="number" id="cAmt" value="10000" min="0" step="any"></div>
          <div><label for="cFrom">From</label><select id="cFrom"></select></div>
          <button class="ghost swap" id="cSwap" type="button" aria-label="Swap currencies" style="width:48px;min-height:44px">⇄</button>
          <div><label for="cTo">To</label><select id="cTo"></select></div>
        </div>
        <div class="chips" id="cQuick" style="margin-top:14px"></div>
        <div id="cResult" style="margin-top:14px" aria-live="polite"></div>
      </div>
    </section>

    <!-- SAVED -->
    <section class="tab" id="tab-saved">
      <h2>Saved trips</h2>
      <p class="lead">Plans, budgets and chat answers you saved. They live in the trip_plans folder next to app.py.</p>
      <div id="savedList" class="stack"></div>
      <div id="savedView" style="margin-top:16px"></div>
    </section>
  </main>
</div>

<button class="fab" id="fab" type="button">💬 Ask the agent</button>
<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" aria-label="Chat with the travel agent">
  <div class="drawer-head">
    <h3>Travel agent chat</h3>
    <div class="row" style="gap:6px">
      <button class="btn alt small" id="chatNew" type="button">New chat</button>
      <button class="btn alt small" id="chatClose" type="button" aria-label="Close chat">Close</button>
    </div>
  </div>
  <div class="chat-log" id="chatLog">
    <div class="msg agent">Hi! Ask me anything about your trip. I can check weather, find places and hotels, build routes, convert money and more.</div>
  </div>
  <div class="chat-suggest chips" id="chatChips"></div>
  <div class="chat-bar">
    <input type="text" id="chatBox" placeholder="Ask about your trip" autocomplete="off">
    <button class="btn" id="chatSend" type="button">Send</button>
  </div>
</aside>
<div id="toasts" role="status" aria-live="polite"></div>

<script>
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const inr = (n) => "₹" + Math.round(n || 0).toLocaleString("en-IN");
function getStore(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function setStore(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

/* ---------- small helpers ---------- */
function toast(msg, isErr) {
  const t = document.createElement("div");
  t.className = "toast" + (isErr ? " err" : "");
  t.textContent = msg;
  $("#toasts").appendChild(t);
  setTimeout(() => t.remove(), isErr ? 6000 : 3200);
}

async function api(path, options) {
  let r;
  try { r = await fetch(path, options); }
  catch (e) { throw new Error("Could not reach the app. Check that the black window is still running."); }
  let data = null;
  try { data = await r.json(); } catch (e) { throw new Error("The app sent an answer that could not be read."); }
  if (!r.ok || data.error) throw new Error(data.error || "Something went wrong.");
  return data;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

function md(text) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]])/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  let out = "", inList = false;
  const closeList = () => { if (inList) { out += "</ul>"; inList = false; } };
  // Matches old-format saved-plan lines like "Flights: https://..." or
  // "Route: https://..." and turns them into a clean button instead of a
  // long raw URL wrapped across several lines.
  const linkLineRe = /^([A-Za-z][A-Za-z0-9 .'()-]{0,42}):\s*(https?:\/\/\S+)\s*$/;
  // Matches the plain-text section headers planToText() writes, e.g.
  // "DAY 1: Beaches", "TICKETS", "WEATHER (Goa, India)".
  const headerRe = /^(DAY \d+:.*|TICKETS|HOTELS|TIPS|PACKING|WHERE TO STAY|WEATHER \(.*\)|ROUGH BUDGET:.*)$/;
  String(text || "").split("\n").forEach((line) => {
    let m;
    if ((m = line.match(linkLineRe))) {
      closeList();
      out += '<p style="margin:6px 0"><a class="btn small alt" href="' + esc(m[2]) + '" target="_blank" rel="noopener">' + esc(m[1]) + '</a></p>';
    }
    else if (headerRe.test(line.trim())) { closeList(); out += "<h4>" + inline(line.trim()) + "</h4>"; }
    else if ((m = line.match(/^\s*#{1,4}\s+(.*)/))) { closeList(); out += "<h4>" + inline(m[1]) + "</h4>"; }
    else if ((m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)/))) { if (!inList) { out += "<ul>"; inList = true; } out += "<li>" + inline(m[1]) + "</li>"; }
    else if (!line.trim()) { closeList(); }
    else { closeList(); out += "<p>" + inline(line) + "</p>"; }
  });
  closeList();
  return out;
}

function fmtDate(iso, opts) {
  const d = new Date(iso + "T00:00:00");
  return isNaN(d) ? iso : d.toLocaleDateString("en-IN", opts || { day: "numeric", month: "short" });
}
function localISO(d) {
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}
function weatherEmoji(code) {
  if (code === 0) return "☀️"; if (code === 1) return "🌤️"; if (code === 2) return "⛅"; if (code === 3) return "☁️";
  if (code === 45 || code === 48) return "🌫️"; if (code >= 51 && code <= 55) return "🌦️";
  if (code >= 61 && code <= 65) return "🌧️"; if (code >= 71 && code <= 75) return "❄️";
  if (code >= 80 && code <= 82) return "🌧️"; if (code >= 95) return "⛈️"; return "🌡️";
}

/* ---------- theme ---------- */
function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  $("#themeBtn").textContent = t === "dark" ? "Light mode" : "Dark mode";
}
applyTheme(getStore("theme") || (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
$("#themeBtn").addEventListener("click", () => {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  setStore("theme", next); applyTheme(next);
});

/* ---------- tabs ---------- */
const TABS = ["plan", "weather", "routes", "budget", "packing", "currency", "saved"];
function showTab(name) {
  if (!TABS.includes(name)) name = "plan";
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  $$(".nav button").forEach((b) => { if (b.dataset.tab === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  window.scrollTo(0, 0);
  if (name === "saved") loadSaved();
}
$$(".nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- chips ---------- */
function singleChips(box, onChange) {
  $$(".chip", box).forEach((c) => c.addEventListener("click", () => {
    $$(".chip", box).forEach((x) => x.setAttribute("aria-pressed", "false"));
    c.setAttribute("aria-pressed", "true");
    if (onChange) onChange(c.dataset.v);
  }));
}
const chipValue = (box) => { const c = $('.chip[aria-pressed="true"]', box); return c ? c.dataset.v : ""; };

const INTERESTS = ["Beaches", "Food", "Temples and heritage", "Nature and trekking", "Nightlife", "Shopping", "Adventure sports", "Relaxing"];
INTERESTS.forEach((name) => {
  const b = document.createElement("button");
  b.type = "button"; b.className = "chip"; b.textContent = name; b.dataset.v = name; b.setAttribute("aria-pressed", "false");
  b.addEventListener("click", () => b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true"));
  $("#interestChips").appendChild(b);
});
singleChips($("#budgetChips"));

/* ---------- boarding pass + defaults ---------- */
function tripDays() {
  const s = $("#start").value, e = $("#end").value;
  if (!s || !e) return 0;
  return Math.round((new Date(e + "T00:00:00") - new Date(s + "T00:00:00")) / 86400000) + 1;
}
function updatePass() {
  $("#pFrom").textContent = $("#from").value.trim() || "Start";
  $("#pTo").textContent = $("#to").value.trim() || "Destination";
  const s = $("#start").value, e = $("#end").value;
  $("#pDates").textContent = s && e ? fmtDate(s) + " to " + fmtDate(e) : "-";
  const n = tripDays();
  $("#pDays").textContent = n > 0 ? n : "-";
  $("#pTrav").textContent = $("#travelers").value || "-";
}
["from", "to", "start", "end", "travelers"].forEach((id) => $("#" + id).addEventListener("input", updatePass));

(function setDateMin() {
  // No default dates are filled in: leaving them blank tells the Intake
  // Agent to pick sensible dates itself from what you typed.
  $("#start").min = localISO(new Date());
})();
$("#start").addEventListener("change", () => {
  const s = $("#start").value;
  if (s && (!$("#end").value || $("#end").value < s)) {
    const e = new Date(s + "T00:00:00"); e.setDate(e.getDate() + 2); $("#end").value = localISO(e);
  }
  $("#end").min = s; updatePass();
});
updatePass();

/* keep the other tabs in step with the destination */
let lastTo = "", lastFrom = "";
function syncFields(ids, newVal, oldVal) {
  ids.forEach((id) => { const f = $("#" + id); if (!f.value.trim() || f.value === oldVal) f.value = newVal; });
}
$("#to").addEventListener("input", () => { const v = $("#to").value; syncFields(["wxCity", "pkCity", "rEnd"], v, lastTo); lastTo = v; });
$("#from").addEventListener("input", () => { const v = $("#from").value; syncFields(["rStart"], v, lastFrom); lastFrom = v; });

/* ---------- weather rendering ---------- */
function weatherCards(w) {
  return '<div class="wx">' + w.forecast.map((d) => {
    const rain = d.rain_chance_percent == null ? 0 : d.rain_chance_percent;
    return '<div class="wx-day"><div class="d">' + esc(fmtDate(d.date, { weekday: "short", day: "numeric", month: "short" })) + '</div>' +
      '<div class="e" aria-hidden="true">' + weatherEmoji(d.code) + '</div>' +
      '<div class="t">' + Math.round(d.max_temp_c) + '° <span class="note">/ ' + Math.round(d.min_temp_c) + '°</span></div>' +
      '<div class="c">' + esc(d.conditions) + '</div>' +
      '<div class="note">Rain ' + rain + '%</div><div class="bar"><i style="width:' + rain + '%"></i></div></div>';
  }).join("") + "</div>";
}

/* ---------- PLAN (coordinates the agent team) ---------- */
let currentPlan = null;
let loadingTimer = null;

function planLoading(msgs) {
  let i = 0;
  $("#planResult").innerHTML = '<div class="card skeleton"><div class="spinner"></div><b id="loadMsg">' + msgs[0] + '</b><div class="note">This can take up to a minute when an agent is busy.</div></div>';
  clearInterval(loadingTimer);
  loadingTimer = setInterval(() => { i = Math.min(i + 1, msgs.length - 1); const m = $("#loadMsg"); if (m) m.textContent = msgs[i]; }, 6000);
  $("#planResult").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function runPlan(body) {
  const btn = $("#planBtn"); btn.disabled = true; btn.textContent = "Planning…";
  planLoading(["Intake agent is reading your request…", "Scout agent is weighing destinations…", "Planner agent is building the itinerary…", "Almost there…"]);
  try {
    const data = await postJSON("/api/plan", body);
    if (data.type === "suggestions") { renderSuggestions(data); return; }
    currentPlan = data;
    renderPlan(data);
    autoFillOtherTabs(data);
    if (data.ai_error) toast("The day-by-day plan is missing, but the links below still work.", true);
  } catch (e) {
    $("#planResult").innerHTML = '<div class="card"><div class="notice"><b>Could not plan the trip.</b><br>' + esc(e.message) + '</div></div>';
  } finally {
    clearInterval(loadingTimer); btn.disabled = false; btn.textContent = "Plan my trip ✨";
  }
}

function renderSuggestions(d) {
  $("#planResult").innerHTML = '<div class="card"><h3>🔭 Destination Scout suggests…</h3>' +
    '<p class="note" style="margin-top:0">' + esc(d.trip_vibe || "You didn't name a destination, so here are a few that fit what you told me.") + '</p>' +
    '<div class="grid3" id="scoutOptions"></div></div>';
  const box = $("#scoutOptions");
  (d.options || []).forEach((o) => {
    const div = document.createElement("div");
    div.className = "card";
    div.style.boxShadow = "none";
    div.innerHTML = '<h3 style="font-size:16px">' + esc(o.destination) + '</h3>' +
      '<p class="note" style="margin:4px 0 10px">' + esc(o.best_for) + '</p>' +
      '<p style="margin:0 0 14px">' + esc(o.why) + '</p>' +
      '<button class="btn small" type="button">Plan this trip</button>';
    $("button", div).addEventListener("click", () => {
      runPlan({
        name: d.name, origin: d.origin, travelers: d.travelers,
        destination: o.destination, start: d.start, end: d.end,
        budget: d.budget, interests: d.interests, free_text: d.trip_vibe,
      });
    });
    box.appendChild(div);
  });
}

function autoFillOtherTabs(data) {
  const f = data.form;
  // Weather tab
  $("#wxCity").value = f.destination;
  if (data.weather) $("#wxResult").innerHTML = '<div class="card"><h3>' + esc(data.weather.place) + '</h3>' + weatherCards(data.weather) + '</div>';
  // Routes tab
  $("#rStart").value = f.origin; $("#rEnd").value = f.destination;
  if (data.overall_route) {
    $("#routeResult").innerHTML = '<div class="row">' + linkBtn(data.overall_route, "Open route in Google Maps") + '</div><p class="note">Filled in automatically from your trip plan.</p>';
  }
  // Budget tab
  applyBudgetDefaults(data.budget_defaults);
  // Packing tab
  if (data.packing) { $("#pkCity").value = f.destination; buildPacking(data.packing); }
  // Currency tab: guess the destination's currency automatically
  if (data.currency_guess && data.currency_guess !== "INR") {
    $("#cFrom").value = "INR"; $("#cTo").value = data.currency_guess; convert();
  }
}

$("#planForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = {
    name: $("#travelerName").value.trim(),
    origin: $("#from").value.trim(),
    free_text: $("#freeText").value.trim(),
    destination: $("#to").value.trim(),
    start: $("#start").value, end: $("#end").value,
    travelers: parseInt($("#travelers").value, 10) || 2,
    budget: chipValue($("#budgetChips")) || "",
    interests: $$("#interestChips .chip").filter((c) => c.getAttribute("aria-pressed") === "true").map((c) => c.dataset.v),
  };
  if (!body.origin) return toast("Tell us which city you're starting from.", true);
  if (!body.destination && !body.free_text) return toast("Name a destination, or describe the trip you want.", true);
  const n = tripDays();
  if (n > 7) return toast("Plans cover up to 7 days. Shorten the dates a little, or leave them blank.", true);
  await runPlan(body);
});



function linkBtn(url, label, alt) {
  return '<a class="btn small ' + (alt ? "alt" : "") + '" href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(label) + '</a>';
}
function mapsSearch(q) { return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(q); }

// Builds the full interactive plan layout as HTML. Used for both the live
// Plan tab result and a reopened Saved trip, so both look and work the same.
function buildPlanHTML(d, opts) {
  opts = opts || {};
  const f = d.form; const L = d.links; let h = "";
  const title = f.destination + " · " + f.days + (f.days === 1 ? " day" : " days");
  const greeting = d.name ? "Here's your trip, " + esc(d.name) + "! 🧳 " : "";
  const headerRight = opts.showSave
    ? '<button class="btn small" id="savePlanBtn" type="button">Save this plan</button>'
    : (opts.headerRight || '');

  h += '<div class="card"><div class="result-head"><div><h2>' + esc(title) + '</h2>' +
       '<div class="note">' + esc(f.origin) + ' to ' + esc(f.destination) + ', ' + esc(fmtDate(f.start)) + ' to ' + esc(fmtDate(f.end)) + ', ' + f.travelers + ' traveler' + (f.travelers > 1 ? "s" : "") + '</div></div>' +
       headerRight + '</div>' +
       '<p style="margin:12px 0 0">' + greeting + esc(d.summary || "") + '</p>' +
       '<p class="note" style="margin:10px 0 0">Put together by your agent team: 🧭 Intake · 🔭 Scout · 🗺️ Planner, with help from the weather, route, budget and packing tools.</p></div>';

  if (d.ai_error) h += '<div class="notice"><b>The AI part did not finish.</b> ' + esc(d.ai_error) + ' You can press Plan my trip again in a minute. Weather, links and packing below still work.</div>';

  h += '<div class="card"><h3>Getting there</h3><div class="pill-links">' +
       linkBtn(L.flight, "Search flights, pre-filled") + linkBtn(L.train, "Trains on IRCTC", true) + linkBtn(L.bus, "Buses on redBus", true) + '</div>' +
       '<p class="note" style="margin:10px 0 2px">✈️ ' + esc(L.flight_note) + '</p>' +
       '<p class="note" style="margin:2px 0">🚆 ' + esc(L.train_note) + '</p>' +
       '<p class="note" style="margin:2px 0 0">🚌 ' + esc(L.bus_note) + '</p>' +
       '<p class="note" style="margin-top:10px">I can\'t complete the booking or payment myself. Train and flight booking sites block automated bots, and real payment needs a licensed travel agency setup. You stay in control of your money and your booking.</p></div>';

  h += '<div class="card"><h3>Where to stay</h3>' + (d.stay_area ? '<p style="margin-top:0">' + esc(d.stay_area) + '</p>' : '') +
       '<div class="pill-links">' + linkBtn(L.hotels_booking, "Hotels on Booking.com") + linkBtn(L.hotels_maps, "Hotels on the map", true) + '</div></div>';

  if (d.weather) {
    const late = new Date(f.start + "T00:00:00") - new Date(localISO(new Date()) + "T00:00:00") > 6 * 86400000;
    h += '<div class="card"><h3>Weather in ' + esc(d.weather.place) + '</h3>' + weatherCards(d.weather) +
         (late ? '<p class="note" style="margin-bottom:0">This is the forecast for the next 7 days. Your trip is later, so treat it as a rough guide.</p>' : '') + '</div>';
  } else if (d.weather_error) {
    h += '<div class="notice">' + esc(d.weather_error) + '</div>';
  }

  if (d.days.length) {
    h += '<h3 style="margin-top:8px">Day by day</h3>';
    d.days.forEach((day, idx) => {
      const date = new Date(f.start + "T00:00:00"); date.setDate(date.getDate() + idx);
      h += '<div class="card day"><h3>Day ' + day.day + ': ' + esc(day.title) + ' <small>' + esc(fmtDate(localISO(date), { weekday: "long", day: "numeric", month: "short" })) + '</small></h3>' +
           '<ul class="places">' + day.places.map((p) => '<li><a href="' + esc(mapsSearch(p)) + '" target="_blank" rel="noopener">📍 ' + esc(p) + '</a></li>').join("") + '</ul>' +
           (day.notes ? '<p style="margin:6px 0 10px">' + esc(day.notes) + '</p>' : '') +
           (d.day_routes[idx] ? linkBtn(d.day_routes[idx], "Open this day's route in Maps") : '') + '</div>';
    });
  }

  if (L.places.length) {
    h += '<div class="card"><h3>Explore on the map</h3><div class="pill-links">' +
         L.places.map((p) => linkBtn(p.link, p.interest, true)).join("") + '</div></div>';
  }
  if (d.tips.length) h += '<div class="card"><h3>Good to know</h3><ul class="tips">' + d.tips.map((t) => '<li>' + esc(t) + '</li>').join("") + '</ul></div>';

  const b = computeBudget(d.budget_defaults);
  h += '<div class="two-col"><div class="card"><h3>Rough budget</h3><div class="total">' + inr(b.total) + '</div>' +
       '<div class="note">About ' + inr(b.perPerson) + ' per person, with a 10% buffer. These are estimates, so check real prices on the booking links.</div>' +
       '<div class="row" style="margin-top:12px"><button class="btn alt small js-open-budget" type="button">Adjust in Budget</button></div></div>';
  if (d.packing) {
    h += '<div class="card"><h3>Packing highlights</h3><ul class="tips">' + d.packing.packing_list.slice(0, 6).map((x) => '<li>' + esc(x) + '</li>').join("") + '</ul>' +
         '<div class="row" style="margin-top:12px"><button class="btn alt small js-open-packing" type="button">Open full checklist</button></div></div>';
  }
  h += '</div>';
  return h;
}

// Wires up the buttons inside a plan's HTML (save / jump to Budget / jump
// to Packing) against the right data, whether it's the live plan or a
// reopened saved one.
function wirePlanButtons(container, d) {
  const sb = $('#savePlanBtn', container);
  if (sb) sb.addEventListener('click', () => savePlan(sb));
  const bb = $('.js-open-budget', container);
  if (bb) bb.addEventListener('click', () => { applyBudgetDefaults(d.budget_defaults); showTab('budget'); });
  const pb = $('.js-open-packing', container);
  if (pb) pb.addEventListener('click', () => {
    if (d.packing) { $('#pkCity').value = d.form.destination; buildPacking(d.packing); }
    showTab('packing');
  });
}

function renderPlan(d) {
  $("#planResult").innerHTML = buildPlanHTML(d, { showSave: true });
  wirePlanButtons($("#planResult"), d);
}

function planToText(d) {
  const f = d.form; const L = d.links; const lines = [];
  lines.push(f.origin + " to " + f.destination + ", " + f.start + " to " + f.end + " (" + f.days + " days, " + f.travelers + " travelers, " + f.budget + ")", "");
  if (d.summary) lines.push(d.summary, "");
  if (d.stay_area) lines.push("WHERE TO STAY", d.stay_area, "");
  lines.push("TICKETS", "Flights: " + L.flight, "Trains: " + L.train, "Buses: " + L.bus, "");
  lines.push("HOTELS", "Booking.com: " + L.hotels_booking, "Map: " + L.hotels_maps, "");
  if (d.weather) {
    lines.push("WEATHER (" + d.weather.place + ")");
    d.weather.forecast.forEach((x) => lines.push(x.date + ": " + x.conditions + ", " + Math.round(x.min_temp_c) + " to " + Math.round(x.max_temp_c) + " C, rain " + x.rain_chance_percent + "%"));
    lines.push("");
  }
  d.days.forEach((day, i) => {
    lines.push("DAY " + day.day + ": " + day.title);
    day.places.forEach((p) => lines.push("- " + p));
    if (day.notes) lines.push(day.notes);
    if (d.day_routes[i]) lines.push("Route: " + d.day_routes[i]);
    lines.push("");
  });
  if (d.tips.length) { lines.push("TIPS"); d.tips.forEach((t) => lines.push("- " + t)); lines.push(""); }
  const b = computeBudget(d.budget_defaults);
  lines.push("ROUGH BUDGET: " + inr(b.total) + " total, about " + inr(b.perPerson) + " per person (estimate)");
  if (d.packing) { lines.push("", "PACKING"); d.packing.packing_list.forEach((x) => lines.push("- " + x)); }
  return lines.join("\n");
}

async function savePlan(btn) {
  if (!currentPlan) return;
  btn.disabled = true;
  try {
    const f = currentPlan.form;
    await postJSON("/api/save", {
      title: f.destination + " trip, " + f.start,
      text: planToText(currentPlan),
      kind: "plan",
      data: currentPlan,
    });
    btn.textContent = "Saved"; toast("Saved to the Saved tab.");
  } catch (e) { btn.disabled = false; toast(e.message, true); }
}

/* ---------- WEATHER TAB ---------- */
async function loadWeather() {
  const city = $("#wxCity").value.trim();
  if (!city) return toast("Enter a city to check the weather.", true);
  const btn = $("#wxBtn"); btn.disabled = true; btn.textContent = "Checking…";
  try {
    const w = await api("/api/weather?city=" + encodeURIComponent(city) + "&days=" + $("#wxDays").value);
    $("#wxResult").innerHTML = '<div class="card"><h3>' + esc(w.place) + '</h3>' + weatherCards(w) + '</div>';
  } catch (e) {
    $("#wxResult").innerHTML = '<div class="notice">' + esc(e.message) + '</div>';
  } finally { btn.disabled = false; btn.textContent = "Check weather"; }
}
$("#wxBtn").addEventListener("click", loadWeather);
$("#wxCity").addEventListener("keydown", (e) => { if (e.key === "Enter") loadWeather(); });
$("#wxResult").innerHTML = '<div class="card empty"><b>No forecast yet</b>Enter a place and press Check weather.</div>';

/* ---------- ROUTES TAB ---------- */
function addStopRow(value) {
  if ($$("#stopList .stop-row").length >= 9) return toast("Google Maps allows up to 9 stops.", true);
  const row = document.createElement("div");
  row.className = "stop-row";
  row.innerHTML = '<input type="text" placeholder="A place to stop at" aria-label="Stop" value="' + esc(value || "") + '"><button class="btn warn small" type="button" aria-label="Remove stop">Remove</button>';
  $("button", row).addEventListener("click", () => row.remove());
  $("#stopList").appendChild(row);
  $("input", row).focus();
}
$("#addStop").addEventListener("click", () => addStopRow(""));
singleChips($("#modeChips"));
$("#routeBtn").addEventListener("click", async () => {
  const stops = $$("#stopList input").map((i) => i.value.trim()).filter(Boolean);
  const btn = $("#routeBtn"); btn.disabled = true;
  try {
    const r = await postJSON("/api/route", { origin: $("#rStart").value.trim(), destination: $("#rEnd").value.trim(), stops: stops, mode: chipValue($("#modeChips")) || "driving" });
    $("#routeResult").innerHTML = '<div class="row">' + linkBtn(r.route_link, "Open route in Google Maps") +
      '<button class="btn alt small" id="copyRoute" type="button">Copy link</button></div>' +
      '<p class="note">' + (stops.length ? stops.length + " stop" + (stops.length > 1 ? "s" : "") + " in the order you entered them." : "Direct route, no stops.") + '</p>';
    $("#copyRoute").addEventListener("click", () => navigator.clipboard.writeText(r.route_link).then(() => toast("Link copied."), () => toast("Could not copy. Select the link and copy it.", true)));
  } catch (e) { toast(e.message, true); } finally { btn.disabled = false; }
});

/* ---------- BUDGET TAB ---------- */
const BUDGET_FIELDS = [
  { id: "days", label: "Trip days", min: 1, max: 14, step: 1, val: 3, money: false },
  { id: "nights", label: "Hotel nights", min: 0, max: 14, step: 1, val: 2, money: false },
  { id: "travelers", label: "Travelers", min: 1, max: 20, step: 1, val: 2, money: false },
  { id: "hotel", label: "Hotel per night, per room", min: 0, max: 20000, step: 250, val: 3500, money: true },
  { id: "food", label: "Food per person, per day", min: 0, max: 4000, step: 50, val: 900, money: true },
  { id: "transport", label: "Local transport per day, whole group", min: 0, max: 6000, step: 100, val: 1200, money: true },
  { id: "activities", label: "Activities per person, per day", min: 0, max: 5000, step: 50, val: 600, money: true },
  { id: "tickets", label: "Tickets to get there and back, total", min: 0, max: 100000, step: 500, val: 0, money: true },
];
const BUDGET_PARTS = [
  { key: "hotel", label: "Hotel", color: "#7c3aed" }, { key: "food", label: "Food", color: "#f3a63b" },
  { key: "transport", label: "Local transport", color: "#3b8fd6" }, { key: "activities", label: "Activities", color: "#b45fd0" },
  { key: "tickets", label: "Tickets", color: "#e0625a" },
];

function computeBudget(v) {
  const days = +v.days || 0, nights = +v.nights || 0, tr = Math.max(1, +v.travelers || 1);
  const rooms = Math.ceil(tr / 2);
  const parts = {
    hotel: nights * rooms * (+v.hotel || 0), food: days * tr * (+v.food || 0),
    transport: days * (+v.transport || 0), activities: days * tr * (+v.activities || 0), tickets: +v.tickets || 0,
  };
  const sub = Object.values(parts).reduce((a, b) => a + b, 0);
  const total = sub * 1.1;
  return { parts, sub, buffer: sub * 0.1, total, perPerson: total / tr, rooms };
}

function buildBudgetInputs() {
  $("#budgetInputs").innerHTML = BUDGET_FIELDS.map((f) =>
    '<div class="slider-row"><label for="b_' + f.id + '_n">' + esc(f.label) + '</label>' +
    '<input type="range" id="b_' + f.id + '_r" min="' + f.min + '" max="' + f.max + '" step="' + f.step + '" value="' + f.val + '" aria-label="' + esc(f.label) + ' slider">' +
    '<input type="number" id="b_' + f.id + '_n" min="' + f.min + '" step="' + f.step + '" value="' + f.val + '"></div>').join("");
  BUDGET_FIELDS.forEach((f) => {
    const r = $("#b_" + f.id + "_r"), n = $("#b_" + f.id + "_n");
    r.addEventListener("input", () => { n.value = r.value; renderBudget(); });
    n.addEventListener("input", () => { if (+n.value > +r.max) r.max = n.value; r.value = n.value; renderBudget(); });
  });
}
function budgetValues() { const v = {}; BUDGET_FIELDS.forEach((f) => { v[f.id] = parseFloat($("#b_" + f.id + "_n").value) || 0; }); return v; }
function renderBudget() {
  const v = budgetValues(); const b = computeBudget(v);
  $("#bTotal").textContent = inr(b.total);
  $("#bPer").textContent = "About " + inr(b.perPerson) + " per person, " + b.rooms + " room" + (b.rooms > 1 ? "s" : "");
  let acc = 0; const stops = [];
  BUDGET_PARTS.forEach((p) => {
    const share = b.sub > 0 ? (b.parts[p.key] / b.sub) * 100 : 0;
    if (share > 0) { stops.push(p.color + " " + acc + "% " + (acc + share) + "%"); acc += share; }
  });
  $("#donut").style.background = stops.length ? "conic-gradient(" + stops.join(",") + ")" : "var(--line)";
  $("#bLegend").innerHTML = BUDGET_PARTS.map((p) => {
    const pct = b.sub > 0 ? Math.round((b.parts[p.key] / b.sub) * 100) : 0;
    return '<li><i style="background:' + p.color + '"></i><span>' + p.label + ' (' + pct + '%)</span><span>' + inr(b.parts[p.key]) + '</span></li>';
  }).join("") + '<li><i style="background:var(--line)"></i><span>10% buffer</span><span>' + inr(b.buffer) + '</span></li>';
}
function applyBudgetDefaults(d) {
  if (!d) return;
  BUDGET_FIELDS.forEach((f) => {
    if (d[f.id] == null) return;
    const r = $("#b_" + f.id + "_r"), n = $("#b_" + f.id + "_n");
    if (+d[f.id] > +r.max) r.max = d[f.id];
    r.value = d[f.id]; n.value = d[f.id];
  });
  renderBudget();
}
function budgetText() {
  const v = budgetValues(), b = computeBudget(v);
  return ["Trip budget (estimate)", "Days: " + v.days + ", nights: " + v.nights + ", travelers: " + v.travelers, ""]
    .concat(BUDGET_PARTS.map((p) => p.label + ": " + inr(b.parts[p.key])))
    .concat(["10% buffer: " + inr(b.buffer), "Total: " + inr(b.total), "Per person: " + inr(b.perPerson)]).join("\n");
}
$("#bCopy").addEventListener("click", () => navigator.clipboard.writeText(budgetText()).then(() => toast("Budget copied."), () => toast("Could not copy.", true)));
$("#bSave").addEventListener("click", async () => {
  try { await postJSON("/api/save", { title: "Budget " + localISO(new Date()), text: budgetText() }); toast("Budget saved."); }
  catch (e) { toast(e.message, true); }
});
buildBudgetInputs(); renderBudget();

/* ---------- PACKING TAB ---------- */
let packCity = "";
function packKey(kind) { return "pack:" + kind + ":" + packCity.toLowerCase(); }
function loadJSON(k, fallback) { try { return JSON.parse(getStore(k)) || fallback; } catch (e) { return fallback; } }

function buildPacking(p) {
  packCity = p.place || $("#pkCity").value;
  const checked = loadJSON(packKey("done"), {});
  const custom = loadJSON(packKey("custom"), []);
  const items = p.packing_list.concat(custom);
  $("#pkResult").innerHTML = '<div class="card"><h3>' + esc(p.place) + '</h3>' +
    '<p class="note" style="margin-top:0">' + esc(p.temperature_range_c) + ' °C, highest rain chance ' + p.highest_rain_chance_percent + '%. ' + esc(p.based_on) + '</p>' +
    '<div class="row" style="justify-content:space-between"><b id="pkCount"></b><button class="btn warn small" id="pkReset" type="button">Untick all</button></div>' +
    '<div class="progress" style="margin-top:8px"><i id="pkBar"></i></div>' +
    '<ul class="check-list" id="pkItems"></ul>' +
    '<div class="row" style="margin-top:14px"><input type="text" id="pkAdd" placeholder="Add your own item" style="flex:1;min-width:180px"><button class="btn alt small" id="pkAddBtn" type="button">Add item</button></div></div>';

  const ul = $("#pkItems");
  const update = () => {
    const boxes = $$("input", ul); const done = boxes.filter((b) => b.checked).length;
    $("#pkCount").textContent = done + " of " + boxes.length + " packed";
    $("#pkBar").style.width = (boxes.length ? (done / boxes.length) * 100 : 0) + "%";
    const map = {}; boxes.forEach((b) => { if (b.checked) map[b.dataset.item] = true; });
    setStore(packKey("done"), JSON.stringify(map));
  };
  const addItem = (name) => {
    const li = document.createElement("li");
    li.innerHTML = '<label><input type="checkbox" data-item="' + esc(name) + '"' + (checked[name] ? " checked" : "") + '><span>' + esc(name) + '</span></label>';
    $("input", li).addEventListener("change", update);
    ul.appendChild(li);
  };
  items.forEach(addItem); update();
  $("#pkReset").addEventListener("click", () => { $$("input", ul).forEach((b) => { b.checked = false; }); update(); });
  const add = () => {
    const name = $("#pkAdd").value.trim();
    if (!name) return;
    if ($$("input", ul).some((b) => b.dataset.item.toLowerCase() === name.toLowerCase())) return toast("That item is already on the list.", true);
    const list = loadJSON(packKey("custom"), []); list.push(name); setStore(packKey("custom"), JSON.stringify(list));
    addItem(name); $("#pkAdd").value = ""; update();
  };
  $("#pkAddBtn").addEventListener("click", add);
  $("#pkAdd").addEventListener("keydown", (e) => { if (e.key === "Enter") add(); });
}
async function loadPacking() {
  const city = $("#pkCity").value.trim();
  if (!city) return toast("Enter a destination to build the packing list.", true);
  const btn = $("#pkBtn"); btn.disabled = true; btn.textContent = "Building…";
  try { buildPacking(await api("/api/packing?city=" + encodeURIComponent(city) + "&days=7")); }
  catch (e) { $("#pkResult").innerHTML = '<div class="notice">' + esc(e.message) + '</div>'; }
  finally { btn.disabled = false; btn.textContent = "Build list"; }
}
$("#pkBtn").addEventListener("click", loadPacking);
$("#pkCity").addEventListener("keydown", (e) => { if (e.key === "Enter") loadPacking(); });
$("#pkResult").innerHTML = '<div class="card empty"><b>No checklist yet</b>Enter your destination and press Build list.</div>';

/* ---------- CURRENCY TAB ---------- */
const CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SGD", "THB", "JPY", "AUD", "CAD", "CHF", "MYR", "IDR", "LKR", "NZD", "CNY", "ZAR", "SEK", "KRW", "PHP"];
$("#cFrom").innerHTML = $("#cTo").innerHTML = CURRENCIES.map((c) => '<option>' + c + '</option>').join("");
$("#cFrom").value = "INR"; $("#cTo").value = "USD";
[["INR", "USD"], ["INR", "EUR"], ["INR", "AED"], ["INR", "THB"], ["INR", "SGD"], ["USD", "INR"]].forEach(([a, b]) => {
  const c = document.createElement("button"); c.type = "button"; c.className = "chip"; c.textContent = a + " to " + b;
  c.addEventListener("click", () => { $("#cFrom").value = a; $("#cTo").value = b; convert(); });
  $("#cQuick").appendChild(c);
});
let convTimer = null, convToken = 0;
async function convert() {
  const amt = $("#cAmt").value;
  const token = ++convToken;
  if (amt === "" || isNaN(+amt) || +amt < 0) { $("#cResult").innerHTML = '<div class="note">Enter an amount of 0 or more.</div>'; return; }
  $("#cResult").innerHTML = '<div class="note">Getting the latest rate…</div>';
  try {
    const r = await api("/api/currency?amount=" + encodeURIComponent(amt) + "&from=" + $("#cFrom").value + "&to=" + $("#cTo").value);
    if (token !== convToken) return;
    const fmt = (n, c) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(n) + " " + c;
    $("#cResult").innerHTML = '<div class="note">' + esc(fmt(r.amount, r.from)) + ' is about</div><div class="money">' + esc(fmt(r.converted_amount, r.to)) + '</div>' +
      '<div class="note">1 ' + esc(r.from) + ' = ' + esc(String(r.rate_for_1_unit)) + ' ' + esc(r.to) + ' · rate date ' + esc(r.rate_date) + '. ' + esc(r.note) + '</div>';
  } catch (e) { if (token === convToken) $("#cResult").innerHTML = '<div class="notice">' + esc(e.message) + '</div>'; }
}
const convertSoon = () => { clearTimeout(convTimer); convTimer = setTimeout(convert, 450); };
$("#cAmt").addEventListener("input", convertSoon);
$("#cFrom").addEventListener("change", convert);
$("#cTo").addEventListener("change", convert);
$("#cSwap").addEventListener("click", () => { const a = $("#cFrom").value; $("#cFrom").value = $("#cTo").value; $("#cTo").value = a; convert(); });

/* ---------- SAVED TAB ---------- */
async function loadSaved() {
  const box = $("#savedList");
  try {
    const { plans } = await api("/api/plans");
    if (!plans.length) { box.innerHTML = '<div class="card empty"><b>Nothing saved yet</b>Plan a trip and press Save this plan, or save a budget or chat answer.</div>'; return; }
    box.innerHTML = plans.map((p) => '<div class="card saved-item"><div><h3>' + (p.kind === "plan" ? "🧳 " : "") + esc(p.title) + '</h3><div class="note">Saved ' + esc(p.saved) + '</div></div>' +
      '<div class="row"><button class="btn small" data-view="' + esc(p.name) + '" type="button">View</button>' +
      '<a class="btn alt small" href="/api/plans/' + encodeURIComponent(p.name) + '/download">Download</a>' +
      '<button class="btn warn small" data-del="' + esc(p.name) + '" type="button">Delete</button></div></div>').join("");
    $$("[data-view]", box).forEach((b) => b.addEventListener("click", () => viewSaved(b.dataset.view)));
    $$("[data-del]", box).forEach((b) => b.addEventListener("click", () => delSaved(b.dataset.del)));
  } catch (e) { box.innerHTML = '<div class="notice">' + esc(e.message) + '</div>'; }
}
async function viewSaved(name) {
  try {
    const p = await api("/api/plans/" + encodeURIComponent(name));
    const actions = '<div class="row">' +
      '<a class="btn alt small" href="/api/plans/' + encodeURIComponent(name) + '/download">Download as text</a>' +
      '<button class="btn warn small" id="savedDelBtn" type="button">Delete</button></div>';
    if (p.kind === "plan" && p.data) {
      // Full structured trip: render it exactly like a freshly planned trip.
      $("#savedView").innerHTML =
        '<div class="card"><div class="result-head"><h3 style="margin:0">🧳 Saved trip</h3>' + actions + '</div></div>' +
        buildPlanHTML(p.data, { showSave: false });
      wirePlanButtons($("#savedView"), p.data);
    } else {
      // A saved budget or chat answer: just text, shown as readable prose.
      $("#savedView").innerHTML =
        '<div class="card"><div class="result-head"><h3 style="margin:0">' + esc(p.title) + '</h3>' + actions + '</div>' +
        '<div class="viewer" style="margin-top:10px">' + md(p.text) + '</div></div>';
    }
    $("#savedDelBtn").addEventListener("click", () => delSaved(name));
    $("#savedView").scrollIntoView({ behavior: "smooth" });
  } catch (e) { toast(e.message, true); }
}
async function delSaved(name) {
  if (!confirm("Delete this saved trip? This cannot be undone.")) return;
  try { await api("/api/plans/" + encodeURIComponent(name), { method: "DELETE" }); $("#savedView").innerHTML = ""; toast("Deleted."); loadSaved(); }
  catch (e) { toast(e.message, true); }
}

/* ---------- CHAT DRAWER ---------- */
function openChat() { $("#drawer").classList.add("open"); $("#scrim").classList.add("show"); $("#chatBox").focus(); }
function closeChat() { $("#drawer").classList.remove("open"); $("#scrim").classList.remove("show"); $("#fab").focus(); }
$("#fab").addEventListener("click", openChat);
$("#chatClose").addEventListener("click", closeChat);
$("#scrim").addEventListener("click", closeChat);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("#drawer").classList.contains("open")) closeChat(); });

["What should I pack?", "Best time to visit?", "Suggest a 1-day food trail", "Is it safe for solo travel?"].forEach((q) => {
  const c = document.createElement("button"); c.type = "button"; c.className = "chip"; c.textContent = q;
  c.addEventListener("click", () => { $("#chatBox").value = q; sendChat(); });
  $("#chatChips").appendChild(c);
});

function addChat(html, who) {
  const d = document.createElement("div"); d.className = "msg " + who; d.innerHTML = html;
  $("#chatLog").appendChild(d); $("#chatLog").scrollTop = $("#chatLog").scrollHeight; return d;
}
let chatBusy = false;
async function sendChat() {
  if (chatBusy) return;
  const msg = $("#chatBox").value.trim();
  if (!msg) return;
  $("#chatBox").value = ""; addChat(esc(msg), "user");
  const wait = addChat("Thinking…", "agent");
  chatBusy = true; $("#chatSend").disabled = true;
  try {
    const data = await postJSON("/chat", { message: msg });
    wait.innerHTML = md(data.reply);
    const sb = document.createElement("button");
    sb.className = "btn alt small save-btn"; sb.type = "button"; sb.textContent = "Save this answer";
    sb.addEventListener("click", async () => {
      try { await postJSON("/api/save", { title: "Chat: " + msg.slice(0, 50), text: data.reply }); sb.textContent = "Saved"; sb.disabled = true; toast("Saved to the Saved tab."); }
      catch (e) { toast(e.message, true); }
    });
    wait.appendChild(sb);
  } catch (e) { wait.innerHTML = esc(e.message); }
  chatBusy = false; $("#chatSend").disabled = false; $("#chatBox").focus();
  $("#chatLog").scrollTop = $("#chatLog").scrollHeight;
}
$("#chatSend").addEventListener("click", sendChat);
$("#chatBox").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
$("#chatNew").addEventListener("click", async () => {
  try { await api("/reset", { method: "POST" }); } catch (e) {}
  $("#chatLog").innerHTML = ""; addChat("Fresh start! What would you like to know?", "agent");
});

/* ---------- start ---------- */
convert();
showTab((location.hash || "#plan").slice(1));
window.addEventListener("hashchange", () => showTab((location.hash || "#plan").slice(1)));
</script>
</body>
</html>
"""


def find_free_port(start=5000, end=5010):
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


if __name__ == "__main__":
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    print("Travel agent is running!")
    print(f"Open this in your browser: {url}")
    print("To stop it, press Ctrl + C in this window.")
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False)

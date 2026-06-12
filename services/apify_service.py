import os
import asyncio


def _get_client():
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    if not token:
        return None
    from apify_client import ApifyClient
    return ApifyClient(token)


def _actor_id(env_key: str, default: str) -> str:
    val = os.getenv(env_key, "").strip()
    return val if val else default


def _wrap(result):
    """Normalise a raw scraper return into {data, error}."""
    if isinstance(result, Exception):
        return {"data": [], "error": str(result)}
    items = result if isinstance(result, list) else []
    errors = [i for i in items if "_error" in i]
    data = [i for i in items if "_error" not in i]
    return {"data": data, "error": errors[0]["_error"] if errors and not data else None}


# ─── Google Maps ──────────────────────────────────────────────────────────────

def _run_google_maps(queries: list, city: str = "", area: str = "") -> list:
    client = _get_client()
    if not client:
        return []
    actor = _actor_id("APIFY_GOOGLE_MAPS_ACTOR", "compass/crawler-google-places")
    try:
        # Use area+city for tighter geographic targeting (e.g. "Sector 65, Gurugram")
        location_parts = [p for p in [area, city] if p]
        location = (", ".join(location_parts) + ", India") if location_parts else "India"
        run_input = {
            "searchStringsArray": ["real estate agents", "property brokers", "real estate dealers"],
            "locationQuery": location,
            "maxCrawledPlacesPerSearch": 50,  # 50 × 3 terms = up to 150 results
            "language": "en",
            "countryCode": "in",
        }
        print(f"[Google Maps] location={location!r}, terms={run_input['searchStringsArray']}")
        run = client.actor(actor).call(run_input=run_input, timeout_secs=180)
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        print(f"[Google Maps] fetched {len(items)} raw results")
        return [_fmt_maps(i) for i in items]  # return all, let caller trim
    except Exception as e:
        return [{"_error": str(e)}]


def _fmt_maps(i: dict) -> dict:
    return {
        "name": i.get("title", ""),
        "phone": i.get("phone", ""),
        "email": "",
        "address": i.get("address", ""),
        "rating": i.get("totalScore"),
        "reviews": i.get("reviewsCount", 0),
        "website": i.get("website", ""),
        "categories": i.get("categories", []),
        "maps_url": i.get("url", ""),
        "thumbnail": i.get("imageUrl", ""),
    }


# ─── 99acres ─────────────────────────────────────────────────────────────────

def _run_realtor(city: str, area: str = "") -> list:
    client = _get_client()
    if not client:
        return []
    actor = _actor_id("APIFY_REALTOR_ACTOR", "samstorm/real-estate-lead-scraper")
    if not actor or not city:
        return []

    location = f"{area}, {city}".strip(", ") if area else city

    try:
        run = client.actor(actor).call(
            run_input={
                "businessType": "Real Estate Broker",
                "location": location,
                "maxResults": 50,
                "enrichEmails": True,
                "verifyEmails": False,
                "enrichSocials": False,
                "outputFormat": "full",
            },
            timeout_secs=500,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        return [_fmt_realtor(i) for i in items[:50]]
    except Exception as e:
        return [{"_error": str(e)}]


def _fmt_realtor(i: dict) -> dict:
    phones = i.get("phones") or []
    phone = phones[0].get("number", "") if phones else (i.get("phone") or "")
    return {
        "name": i.get("fullName") or i.get("name") or "",
        "phone": phone,
        "email": i.get("email") or "",
        "address": i.get("address") or i.get("city") or "",
        "rating": i.get("rating") or i.get("starRating"),
        "reviews": i.get("reviewCount") or i.get("reviews") or 0,
        "website": i.get("websiteUrl") or i.get("website") or "",
        "property_name": "",
        "price": "",
    }


# ─── MagicBricks ─────────────────────────────────────────────────────────────

def _run_magicbricks(city: str = "") -> list:
    client = _get_client()
    if not client:
        return []
    actor = _actor_id("APIFY_MAGICBRICKS_ACTOR", "thirdwatch/magicbricks-scraper")
    if not actor or not city:
        return []

    try:
        run = client.actor(actor).call(
            run_input={
                "city": city,
                "mode": "buy",
                "propertyType": ["apartment", "builder-floor", "penthouse", "studio"],
                "maxResults": 15,
            },
            timeout_secs=150,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        return [_fmt_magicbricks(i) for i in items[:30]]
    except Exception as e:
        return [{"_error": str(e)}]


def _fmt_magicbricks(i: dict) -> dict:
    return {
        "name": (i.get("sellerName") or i.get("agentName") or i.get("brokerName")
                 or i.get("owner") or i.get("title") or i.get("name") or ""),
        "phone": (i.get("phone") or i.get("mobile") or i.get("contact")
                  or i.get("sellerPhone") or i.get("agentPhone") or ""),
        "email": (i.get("email") or i.get("sellerEmail") or i.get("agentEmail") or ""),
        "address": (i.get("locality") or i.get("location") or i.get("area")
                    or i.get("address") or ""),
        "price": i.get("price") or i.get("Price") or i.get("priceRange") or "",
        "property_name": i.get("projectName") or i.get("title") or i.get("name") or "",
        "config": i.get("bhk") or i.get("bedrooms") or i.get("bedroom") or "",
        "area_sqft": i.get("area") or i.get("carpetArea") or i.get("superArea") or "",
        "rera": i.get("rera") or i.get("reraId") or i.get("rera_number") or "",
        "url": i.get("url") or i.get("propertyUrl") or "",
        "thumbnail": i.get("image") or i.get("photo") or "",
    }


# ─── JustDial ─────────────────────────────────────────────────────────────────

def _run_justdial(urls: list) -> list:
    client = _get_client()
    if not client:
        return []
    actor = _actor_id("APIFY_JUSTDIAL_ACTOR", "jupri/justdial-scraper")
    if not actor or not urls:
        return []
    try:
        run = client.actor(actor).call(
            run_input={"startUrls": [{"url": u} for u in urls[:3]]},
            timeout_secs=150,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        return [_fmt_justdial(i) for i in items[:40]]
    except Exception as e:
        return [{"_error": str(e)}]


def _fmt_justdial(i: dict) -> dict:
    return {
        "name": i.get("name") or i.get("title") or "",
        "phone": i.get("phone") or i.get("mobile") or i.get("telNo") or "",
        "email": i.get("email") or i.get("emailId") or "",
        "address": i.get("address") or i.get("locality") or "",
        "rating": i.get("rating") or i.get("stars") or "",
        "reviews": i.get("reviews") or i.get("ratingCount") or 0,
        "website": i.get("website") or "",
        "categories": i.get("categories") or [i.get("category") or ""],
        "maps_url": i.get("url") or "",
        "thumbnail": i.get("image") or "",
    }


# ─── Public async entry point ─────────────────────────────────────────────────

async def run_all_scrapers(analysis: dict) -> dict:
    import json as _json
    from pathlib import Path

    if os.getenv("SCRAPER_ENABLED", "true").lower() != "true":
        mock_path = Path(__file__).parent / "response.json"
        mock_data = _json.loads(mock_path.read_text(encoding="utf-8"))
        print(f"[MOCK MODE] Loaded {len(mock_data)} records from response.json")
        return {
            "google_maps": {"data": mock_data, "error": None},
            "99acres":     {"data": [], "error": None},
            "magicbricks": {"data": [], "error": None},
            "justdial":    {"data": [], "error": None},
        }

    project = analysis.get("project_details", {})
    loc = project.get("location", {})
    city = loc.get("city", "")

    queries = analysis.get("search_queries", {})
    google_queries = queries.get("google_maps", [])
    if not google_queries and city:
        google_queries = [f"real estate agents in {city}", f"property brokers in {city}"]

    loop = asyncio.get_event_loop()
    maps_task = loop.run_in_executor(None, _run_google_maps, google_queries, city)
    maps_res, = await asyncio.gather(maps_task, return_exceptions=True)

    return {
        "google_maps": _wrap(maps_res),
        "99acres":     {"data": [], "error": None},
        "magicbricks": {"data": [], "error": None},
        "justdial":    {"data": [], "error": None},
    }

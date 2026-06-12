import os
import json
import re
from openai import AsyncOpenAI

_client = None


def get_client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


def _parse_json(text: str) -> dict:
    text = text.strip()
    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


EXTRACTION_PROMPT = """You are a senior real estate analyst in India. Carefully read the following builder/developer brochure text and extract all available project details.

Return ONLY a valid JSON object with this exact structure (use null for missing fields):
{{
  "project_name": "string",
  "developer_name": "string",
  "location": {{
    "city": "string",
    "area": "string",
    "state": "string",
    "pincode": "string or null"
  }},
  "property_type": "residential | commercial | mixed-use",
  "unit_types": ["2BHK", "3BHK"],
  "price_range": {{
    "min": "string e.g. 50 Lakhs",
    "max": "string e.g. 1.2 Crore",
    "unit": "INR"
  }},
  "total_units": "string or null",
  "area_range": {{
    "min": "string e.g. 850 sqft",
    "max": "string e.g. 1400 sqft"
  }},
  "possession_date": "string or null",
  "amenities": ["list of amenities"],
  "highlights": ["key USPs"],
  "rera_number": "string or null",
  "target_audience": "string describing ideal buyers",
  "nearby_landmarks": ["important nearby places"],
  "contact": "string or null"
}}

Brochure text:
{pdf_text}

Return ONLY the JSON object. No explanation, no markdown fences."""

QUERY_PROMPT = """You are a real estate market researcher in India. Based on the project details below, generate targeted search queries and URLs for competitive intelligence gathering.

Project Details:
{project_details}

Return ONLY a valid JSON object:
{{
  "google_maps": [
    "3 to 5 search queries to find competitor real estate projects, builders, and property agents in the same area"
  ],
  "99acres": {{
    "search_queries": ["2-3 search terms"],
    "urls": [
      "2 direct 99acres.com search URLs for this property type and location"
    ]
  }},
  "magicbricks": {{
    "search_queries": ["2-3 search terms"],
    "urls": [
      "2 direct magicbricks.com search URLs for this property type and location"
    ]
  }},
  "summary_prompt": "A one-paragraph competitive intelligence brief describing what we are looking for"
}}

For 99acres URLs use format: https://www.99acres.com/search/property/buy/{{area}}-{{city}}?city=XX&preference=S&area=XX
For MagicBricks URLs use format: https://www.magicbricks.com/property-for-sale/residential-real-estate?proptype=Multistorey-Apartment&cityName={{City}}

Return ONLY the JSON object. No explanation."""


_VISION_EXTRACTION_PROMPT = """These images are pages from an Indian real estate project brochure. Read every visible element carefully — project name, developer, location, price, unit sizes, amenities, RERA number, contact details, nearby landmarks — and extract them all.

Return ONLY a valid JSON object (no markdown fences):
{
  "project_name": "string",
  "developer_name": "string",
  "location": {"city": "string", "area": "string", "state": "string", "pincode": "string or null"},
  "property_type": "residential | commercial | mixed-use",
  "unit_types": ["2BHK", "3BHK"],
  "price_range": {"min": "string e.g. 50 Lakhs", "max": "string e.g. 1.2 Crore", "unit": "INR"},
  "total_units": "string or null",
  "area_range": {"min": "string e.g. 850 sqft", "max": "string e.g. 1400 sqft"},
  "possession_date": "string or null",
  "amenities": ["list"],
  "highlights": ["key USPs"],
  "rera_number": "string or null",
  "target_audience": "string",
  "nearby_landmarks": ["list"],
  "contact": "string or null"
}"""


async def _extract_via_vision(client, images_b64: list) -> dict:
    vision_model = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    content = [{"type": "text", "text": _VISION_EXTRACTION_PROMPT}]
    for b64 in images_b64[:3]:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    resp = await client.chat.completions.create(
        model=vision_model,
        messages=[{"role": "user", "content": content}],
        temperature=0.1,
    )
    return _parse_json(resp.choices[0].message.content)


_FALLBACK_CITY = "Gurugram"

def _fallback_details(hint: str = "") -> dict:
    """Used when AI is unavailable — returns safe defaults so the demo never breaks."""
    return {
        "project_name": hint or "Demo Project",
        "developer_name": "",
        "location": {"city": _FALLBACK_CITY, "area": "", "state": "Haryana", "pincode": None},
        "property_type": "residential",
        "unit_types": [],
        "price_range": {"min": None, "max": None, "unit": "INR"},
        "total_units": None,
        "area_range": {"min": None, "max": None},
        "possession_date": None,
        "amenities": [],
        "highlights": [],
        "rera_number": None,
        "target_audience": "",
        "nearby_landmarks": [],
        "contact": None,
    }

def _fallback_queries() -> dict:
    return {
        "google_maps": [
            f"real estate agents in {_FALLBACK_CITY}",
            f"property brokers in {_FALLBACK_CITY}",
        ],
        "99acres": {"search_queries": [], "urls": []},
        "magicbricks": {"search_queries": [], "urls": []},
        "summary_prompt": "",
    }


async def analyze_project_pdf(pdf_text: str, pdf_images: list = None) -> dict:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    client = get_client()

    try:
        # If text is too sparse (image-based PDF) use vision model
        if len(pdf_text.strip()) < 200 and pdf_images:
            project_details = await _extract_via_vision(client, pdf_images)
        else:
            trimmed = pdf_text[:10000]
            extraction_resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(pdf_text=trimmed)}],
                temperature=0.1,
            )
            project_details = _parse_json(extraction_resp.choices[0].message.content)
    except Exception as e:
        print(f"[AI] PDF extraction failed ({type(e).__name__}): {e} — using fallback defaults")
        return {"project_details": _fallback_details(), "search_queries": _fallback_queries()}

    try:
        query_resp = await client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": QUERY_PROMPT.format(project_details=json.dumps(project_details, indent=2)),
            }],
            temperature=0.2,
        )
        search_queries = _parse_json(query_resp.choices[0].message.content)
    except Exception as e:
        print(f"[AI] Query generation failed ({type(e).__name__}): {e} — using fallback queries")
        search_queries = _fallback_queries()

    return {"project_details": project_details, "search_queries": search_queries}


_UNIFY_PROMPT = """You are a data specialist for Indian real estate. Below is raw contact data scraped from multiple sources (Google Maps, JustDial, 99acres, MagicBricks) for the city/area: {city}.

TASK — return a single clean JSON array of real estate brokers/agents/dealers:
1. DEDUPLICATE: same phone = same person. Merge, keep richest data.
2. REMOVE: anything NOT a real estate broker/agent/dealer (hospitals, schools, shops, etc.)
3. SORT: phone+email entries first → phone-only → rest
4. NORMALIZE phones: digits only, keep +91 prefix if present
5. MAX 40 results

Each object must have exactly these fields (use "" or null for unknowns):
{{"name":"","phone":"","email":"","address":"","rating":null,"reviews":null,"website":"","property_name":"","price":"","key_person":"","key_person_title":""}}

Raw data ({count} records):
{raw_data}

RETURN ONLY THE JSON ARRAY. No markdown. No explanation."""


def _basic_dedup(items: list) -> list:
    seen = set()
    out = []
    for i in items:
        key = (i.get("phone") or "").strip()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append({
            "name": i.get("name", ""),
            "phone": i.get("phone", ""),
            "email": i.get("email", ""),
            "address": i.get("address", ""),
            "rating": i.get("rating"),
            "reviews": i.get("reviews"),
            "website": i.get("website", ""),
            "property_name": i.get("property_name", ""),
            "price": i.get("price", ""),
            "key_person": i.get("key_person", ""),
            "key_person_title": i.get("key_person_title", ""),
        })
    return out[:40]


async def unify_broker_results(all_items: list, project_details: dict) -> list:
    if not all_items:
        return []

    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    client = get_client()

    loc = project_details.get("location", {})
    city = ", ".join(filter(None, [loc.get("area"), loc.get("city"), loc.get("state")]))

    # Trim each item to essential fields only (saves tokens)
    trimmed = [
        {
            "name": str(i.get("name", ""))[:60],
            "phone": str(i.get("phone", ""))[:20],
            "email": str(i.get("email", ""))[:60],
            "address": str(i.get("address", ""))[:80],
            "rating": i.get("rating"),
            "reviews": i.get("reviews"),
            "website": str(i.get("website", ""))[:60],
            "property_name": str(i.get("property_name", ""))[:60],
            "price": str(i.get("price", ""))[:30],
            "key_person": str(i.get("key_person", ""))[:60],
            "key_person_title": str(i.get("key_person_title", ""))[:40],
        }
        for i in all_items[:80]
    ]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": _UNIFY_PROMPT.format(
                    city=city or "India",
                    count=len(trimmed),
                    raw_data=json.dumps(trimmed, ensure_ascii=False),
                ),
            }],
            temperature=0.1,
        )
        result = _parse_json(resp.choices[0].message.content)
        return result if isinstance(result, list) else _basic_dedup(all_items)
    except Exception:
        return _basic_dedup(all_items)

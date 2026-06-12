import asyncio
import json
import os
import re
from urllib.parse import urlparse

import httpx

_ENRICH_PROMPT = """Extract the key contact person from this real estate firm's website text.
Return JSON only, no explanation:
{{"name": "...", "title": "...", "phone": "...", "email": "..."}}
Use null for any field not found. Look for: Founder, CEO, MD, Director, Owner, Proprietor.

Website text:
{text}"""

_PERSON_TITLES = (
    "Founder",
    "Co-Founder",
    "Chief Executive Officer",
    "CEO",
    "Managing Director",
    "MD",
    "Director",
    "Owner",
    "Proprietor",
    "Partner",
    "Principal Broker",
)
_TITLE_RE = "|".join(re.escape(title) for title in _PERSON_TITLES)
_NAME_RE = r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})"
_BAD_NAME_WORDS = {
    "Real Estate",
    "Privacy Policy",
    "Terms Conditions",
    "Contact Us",
    "About Us",
    "Home Loan",
}


def _domain(url: str) -> str:
    try:
        p = urlparse(url if url.startswith("http") else "https://" + url)
        return p.netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000]


async def _fetch_text(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url, timeout=6, follow_redirects=True)
        if r.status_code == 200:
            return _strip_html(r.text)
    except Exception:
        pass
    return ""


async def _fetch_best_text(client: httpx.AsyncClient, website: str) -> str:
    base = website.rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base

    texts = []
    text = await _fetch_text(client, base)
    if text:
        texts.append(text)

    for path in ["/about", "/about-us", "/team", "/contact"]:
        t = await _fetch_text(client, base + path)
        if t:
            texts.append(t)
        if sum(len(part) for part in texts) > 3500:
            break

    return " ".join(texts)[:5000]


def _first_email(text: str) -> str:
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)
    return match.group(0) if match else ""


def _first_phone(text: str) -> str:
    match = re.search(r"(?:\+91[\s-]?)?[6-9]\d{2}[\s-]?\d{3}[\s-]?\d{4}", text)
    return re.sub(r"\s+", "", match.group(0)) if match else ""


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" .,-:|")
    if name in _BAD_NAME_WORDS:
        return ""
    if any(word in name.lower() for word in ["privacy", "terms", "property", "estate"]):
        return ""
    return name


def _local_extract(text: str) -> dict:
    compact = re.sub(r"\s+", " ", text)
    result = {
        "name": "",
        "title": "",
        "phone": _first_phone(compact),
        "email": _first_email(compact),
    }

    patterns = [
        rf"\b(?P<title>{_TITLE_RE})\b\s*[:\-–|]?\s*(?P<name>{_NAME_RE})",
        rf"(?P<name>{_NAME_RE})\s*[,|\-–]?\s*(?:is\s+)?(?:the\s+)?\b(?P<title>{_TITLE_RE})\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, compact, flags=re.IGNORECASE):
            name = _clean_name(match.group("name"))
            title = match.group("title").strip()
            if name:
                result["name"] = name
                result["title"] = title
                return result

    return result


async def _groq_extract(groq_client, text: str) -> dict:
    try:
        resp = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": _ENRICH_PROMPT.format(text=text[:900])}],
            temperature=0.1,
            max_tokens=100,
        )
        raw = resp.choices[0].message.content.strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"[ENRICH] Groq error: {e}")
    return {}


async def enrich_brokers(brokers: list) -> list:
    """
    For each broker with a website, scrape public pages and extract contact
    details locally. Groq is only an optional fallback for a small number of
    unresolved websites to avoid burning the main AI token-per-minute limit.
    """
    ai_fallback_limit = int(os.getenv("ENRICH_AI_FALLBACK_LIMIT", "0"))
    enrich_top_n = int(os.getenv("ENRICH_TOP_N", "25"))
    groq_client = None
    if ai_fallback_limit > 0 and os.getenv("GROQ_API_KEY"):
        from services.grok_service import get_client
        groq_client = get_client()

    # Only enrich top records by rating to keep website scraping reasonable
    with_site = [b for b in brokers if b.get("website")]
    without_site = [b for b in brokers if not b.get("website")]

    top = sorted(with_site, key=lambda b: float(b.get("rating") or 0), reverse=True)[:enrich_top_n]
    rest = [b for b in with_site if b not in top]

    # Deduplicate by domain — one fetch per firm
    domain_to_broker: dict[str, dict] = {}
    for b in top:
        d = _domain(b["website"])
        if d and d not in domain_to_broker:
            domain_to_broker[d] = b

    sem = asyncio.Semaphore(8)
    ai_fallback_used = 0

    async def process(domain: str, broker: dict) -> tuple[str, dict]:
        nonlocal ai_fallback_used
        async with sem:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; BuilderBrokerBot/1.0)"}
            async with httpx.AsyncClient(headers=headers) as client:
                text = await _fetch_best_text(client, broker["website"])

            if len(text) < 80:
                print(f"[ENRICH] {domain}: too little text, skipping")
                return domain, {}

            person = _local_extract(text)
            if not person.get("name") and groq_client and ai_fallback_used < ai_fallback_limit:
                ai_fallback_used += 1
                person = await _groq_extract(groq_client, text)

            name = person.get("name") or ""
            method = "local" if name else "no person found"
            print(f"[ENRICH] {domain}: {name or method}")
            return domain, person

    tasks = [process(d, b) for d, b in domain_to_broker.items()]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    person_map: dict[str, dict] = {}
    for r in raw_results:
        if isinstance(r, Exception):
            continue
        domain, person = r
        person_map[domain] = person

    # Merge enrichment into broker records
    enriched = []
    for b in top:
        d = _domain(b.get("website", ""))
        person = person_map.get(d, {})
        nb = dict(b)
        if person.get("name"):
            nb["key_person"] = str(person["name"])
            nb["key_person_title"] = str(person.get("title") or "")
        if person.get("phone") and not nb.get("phone"):
            nb["phone"] = str(person["phone"])
        if person.get("email") and not nb.get("email"):
            nb["email"] = str(person["email"])
        enriched.append(nb)

    return enriched + rest + without_site

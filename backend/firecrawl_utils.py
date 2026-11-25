from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

FIRECRAWL_SEARCH_ENDPOINT = "https://api.firecrawl.dev/v1/search"
MAX_TRANSIENT_RETRIES = 2

STOPWORDS = {
    "about", "across", "after", "against", "among", "around", "based", "because",
    "before", "being", "between", "build", "built", "cannot", "could", "daily",
    "every", "find", "focus", "from", "have", "help", "helps", "into", "lack",
    "like", "lose", "make", "many", "more", "most", "need", "needs", "only",
    "other", "people", "platform", "problem", "product", "really", "should",
    "since", "small", "solution", "solve", "solving", "some", "startup",
    "struggle", "system", "target", "that", "their", "them", "there", "these",
    "they", "this", "those", "through", "time", "tool", "tools", "users",
    "using", "very", "want", "what", "when", "where", "which", "while", "will",
    "with", "without", "would", "your",
}

PRICING_PATTERNS = [
    (re.compile(r"\bfree(?:\s+plan|\s+tier|\s+trial|\s+version)?\b", re.I), "Free tier / trial"),
    (re.compile(r"\bfreemium\b", re.I), "Freemium"),
    (re.compile(r"\$\s?\d+(?:\.\d{1,2})?(?:\s*/\s*(?:mo|month|yr|year|user))?", re.I), "Paid pricing listed"),
    (re.compile(r"\b(?:subscription|monthly|annual|per\s+month|per\s+user)\b", re.I), "Subscription model"),
    (re.compile(r"\b(?:enterprise|b2b|team\s+plan|campus|clinic|agency)\b", re.I), "B2B / team tier"),
    (re.compile(r"\b(?:waitlist|early\s+access|pre-launch|beta)\b", re.I), "Early-stage / waitlist"),
]

LIMITATION_PATTERNS = [
    (re.compile(r"\b(?:waitlist|pre-launch|early\s+access)\b", re.I), "Still in waitlist or early-access stage"),
    (re.compile(r"\b(?:no\s+free|paid\s+only|expensive|costly|pricey)\b", re.I), "Paid-only or higher-cost barrier noted"),
    (re.compile(r"\b(?:manual|spreadsheet|setup|learning\s+curve|complex)\b", re.I), "Requires manual workflow or setup effort"),
    (re.compile(r"\b(?:generic|general|broad|all-in-one)\b", re.I), "Broad or generic positioning rather than niche focus"),
    (re.compile(r"\b(?:ios\s+only|android\s+only|web\s+only|desktop\s+only)\b", re.I), "Platform availability appears limited"),
    (re.compile(r"\b(?:limit|limits|limited|cap|lacks|missing)\b", re.I), "Feature or usage limits highlighted in comparison"),
]

STRENGTH_PATTERNS = [
    (re.compile(r"\b(?:ai|automated|automation|smart|personalized|adaptive)\b", re.I), "Uses AI or automation in core workflow"),
    (re.compile(r"\b(?:free\s+plan|free\s+tier|free\s+to\s+join|free\s+trial)\b", re.I), "Low-friction free tier or trial acquisition"),
    (re.compile(r"\b(?:mobile\s+app|ios|android|chrome\s+extension|web\s+app)\b", re.I), "Accessible cross-device or app-first delivery"),
    (re.compile(r"\b(?:integration|api|sync|calendar|export|whatsapp|sms)\b", re.I), "Workflow integrations and export/sync capabilities"),
    (re.compile(r"\b(?:review|ranked|compared|million|users|trusted|\d+\+?\s+companies)\b", re.I), "Visible market traction or third-party coverage"),
]


class FirecrawlError(RuntimeError):
    """Raised when live Firecrawl web search fails for a single attempt."""


def _extract_keywords(text: str, max_terms: int = 6) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9+-]{2,}", text.lower())
    seen: set[str] = set()
    terms: list[str] = []
    for token in tokens:
        if token in STOPWORDS or token.isdigit() or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= max_terms:
            break
    return terms


def build_search_queries(idea_name: str, problem_statement: str, target_audience: str) -> list[str]:
    name_clean = re.sub(r"\s+", " ", idea_name).strip()
    problem_terms = _extract_keywords(problem_statement, max_terms=5)
    audience_terms = _extract_keywords(target_audience, max_terms=3)

    primary_parts = [name_clean] + [t for t in problem_terms[:3] if t not in name_clean.lower()]
    primary_query = f"{' '.join(primary_parts)} competitors alternatives pricing".strip()

    fallback_terms = _extract_keywords(f"{idea_name} {problem_statement}", max_terms=5)
    secondary_query = f"best {' '.join(fallback_terms)} apps tools software".strip()

    tertiary_terms = (problem_terms[:3] + audience_terms[:2])[:4]
    tertiary_query = f"{' '.join(tertiary_terms)} startup tools alternatives".strip()

    queries: list[str] = []
    for q in (primary_query, secondary_query, tertiary_query):
        normalized = re.sub(r"\s+", " ", q).strip()
        if normalized and normalized not in queries:
            queries.append(normalized)
    return queries


def _clean_markdown(raw_md: str) -> str:
    if not raw_md:
        return ""
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", raw_md)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\\([.*_#\-])", r"\1", text)
    text = re.sub(r"\bSkip\s+to\s+(?:main\s+)?content\b", " ", text, flags=re.I)
    text = re.sub(r"[#>*`|_~]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _pick_summary(description: str, clean_md: str) -> str:
    desc = re.sub(r"\s+", " ", (description or "")).strip()
    if len(desc) >= 60 and not desc.startswith("##"):
        return desc[:280]
    if clean_md:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_md) if len(s.strip()) > 35]
        if sentences:
            return " ".join(sentences[:2])[:280]
        return clean_md[:280]
    return desc[:280] or "Live search result returned without description."


def _extract_signals(text: str) -> list[str]:
    found: list[str] = []
    for pattern, label in PRICING_PATTERNS:
        match = pattern.search(text)
        if match:
            if label == "Paid pricing listed":
                found.append(f"Pricing mention: {match.group(0).strip()}")
            elif label not in found:
                found.append(label)
    return found[:4]


def _extract_strength(text: str, domain: str, is_comparison: bool) -> str:
    hits = [desc for pattern, desc in STRENGTH_PATTERNS if pattern.search(text)]
    if hits:
        return "; ".join(hits[:2]) + "."
    if is_comparison:
        return f"Ranks in search for category comparisons on {domain}."
    return f"Active live web presence on {domain} addressing this problem space."


def _extract_weakness(text: str, signals: list[str], is_comparison: bool) -> str:
    hits = [desc for pattern, desc in LIMITATION_PATTERNS if pattern.search(text)]
    if hits:
        return "; ".join(hits[:2]) + "."
    if is_comparison:
        return "Roundup/comparison page rather than a single focused product; incumbents vary in depth."
    if not signals:
        return "Public pricing and clear differentiation details are not immediately obvious on the landing page."
    return "May not be tailored exclusively to your exact target segment or workflow."


def _fallback_competitors(idea_name: str, problem_statement: str, target_audience: str) -> list[dict[str, Any]]:
    """Idea-specific archetype competitors used only after live Firecrawl retries are exhausted."""
    audience_hint = target_audience.split(",")[0].strip() or "target users"
    keywords = _extract_keywords(f"{idea_name} {problem_statement}", max_terms=3)
    topic = " ".join(keywords).title() if keywords else (idea_name.strip() or "Category")

    return [
        {
            "name": f"Niche {topic} SaaS Alternative",
            "domain": "fallback",
            "summary": f"Focused point-solution targeting {audience_hint} around {keywords[0] if keywords else 'this workflow'}.",
            "strength": f"Specialized UX built for {keywords[0] if keywords else 'core'} workflows.",
            "weakness": "May require paid onboarding or lack end-to-end coverage.",
            "signals": ["Fallback benchmark", "Subscription model"],
            "source": "fallback",
            "markdown_excerpt": "",
        },
        {
            "name": f"Manual / Spreadsheet Workaround",
            "domain": "fallback",
            "summary": f"Existing DIY workflow ({audience_hint} combining general tools, sheets, or manual tracking).",
            "strength": "Zero switching cost and already familiar to users.",
            "weakness": "Time-consuming, error-prone, and lacks automation.",
            "signals": ["Fallback benchmark", "Free tier / trial"],
            "source": "fallback",
            "markdown_excerpt": "",
        },
        {
            "name": f"Broad {topic} Suite Incumbent",
            "domain": "fallback",
            "summary": f"Established multi-feature platform serving {audience_hint} alongside larger enterprise segments.",
            "strength": "Brand trust, existing distribution, and bundled integrations.",
            "weakness": f"Bloated setup and less tailored to {audience_hint}.",
            "signals": ["Fallback benchmark", "B2B / team tier"],
            "source": "fallback",
            "markdown_excerpt": "",
        },
    ]


async def _run_firecrawl_search(client: httpx.AsyncClient, api_key: str, query: str) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "query": query,
        "limit": 5,
        "scrapeOptions": {"formats": ["markdown"]},
    }
    response = await client.post(FIRECRAWL_SEARCH_ENDPOINT, headers=headers, json=payload)
    if response.status_code >= 400:
        detail = response.text[:250]
        raise FirecrawlError(f"Firecrawl API error ({response.status_code}): {detail}")

    data = response.json()
    if not data.get("success", True):
        raise FirecrawlError(f"Firecrawl search failed: {data.get('error') or 'Unknown error'}")
    items = data.get("data") or []
    return items if isinstance(items, list) else []


async def fetch_competitors(
    idea_name: str, problem_statement: str, target_audience: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    api_key = (os.getenv("FIRECRAWL_API_KEY") or "").strip()
    queries = build_search_queries(idea_name, problem_statement, target_audience)

    if not api_key:
        logger.warning("FIRECRAWL_API_KEY missing; falling back after 0 live attempts.")
        return _fallback_competitors(idea_name, problem_statement, target_audience), {
            "competitor_source": "fallback_after_retries",
            "firecrawl_query": queries[0],
            "firecrawl_attempts": 0,
            "firecrawl_results": 0,
            "firecrawl_warning": "FIRECRAWL_API_KEY is missing in backend/.env; using fallback benchmarks.",
        }

    raw_items: list[dict[str, Any]] = []
    used_query = queries[0]
    attempts = 0
    last_error = "No search results returned"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for query in queries:
                used_query = query
                for retry in range(MAX_TRANSIENT_RETRIES):
                    attempts += 1
                    try:
                        items = await _run_firecrawl_search(client, api_key, query)
                        if items:
                            raw_items = items
                            break
                        last_error = f"0 results for query '{query}'"
                        break  # Try next broader query rather than repeating empty query
                    except FirecrawlError as exc:
                        last_error = str(exc)
                        logger.warning("Firecrawl attempt %s failed (%s): %s", attempts, query, exc)
                        # Stop retrying immediately on auth/payment errors (401/402/403)
                        if any(code in last_error for code in ("(401)", "(402)", "(403)")):
                            raise
                        if retry + 1 < MAX_TRANSIENT_RETRIES:
                            await asyncio.sleep(0.8 * (retry + 1))
                    except (httpx.TimeoutException, httpx.TransportError) as exc:
                        last_error = f"{type(exc).__name__}: {exc}"
                        logger.warning("Firecrawl network attempt %s failed: %s", attempts, last_error)
                        if retry + 1 < MAX_TRANSIENT_RETRIES:
                            await asyncio.sleep(0.8 * (retry + 1))
                if raw_items:
                    break
    except FirecrawlError:
        pass
    except Exception as exc:
        last_error = str(exc)
        logger.exception("Unexpected Firecrawl failure")

    if not raw_items:
        logger.warning("Firecrawl exhausted %s attempt(s) (%s); using fallback competitors.", attempts, last_error)
        return _fallback_competitors(idea_name, problem_statement, target_audience), {
            "competitor_source": "fallback_after_retries",
            "firecrawl_query": used_query,
            "firecrawl_attempts": attempts,
            "firecrawl_results": 0,
            "firecrawl_warning": f"Firecrawl failed after {attempts} attempt(s): {last_error}. Showing fallback benchmarks.",
        }

    competitors: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in raw_items[:5]:
        url = (item.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)

        metadata = item.get("metadata") or {}
        title = (item.get("title") or metadata.get("title") or "").strip()
        domain = urlparse(url).netloc.replace("www.", "") if url else "web"
        if not title:
            title = domain

        raw_md = item.get("markdown") or ""
        clean_md = _clean_markdown(raw_md)
        description = (item.get("description") or metadata.get("description") or "").strip()
        combined_text = f"{title} {description} {clean_md[:3500]}"

        is_comparison = bool(
            re.search(r"\b(best|top\s+\d+|alternatives|vs\.?|compared|comparison|guide|ranked)\b", title, re.I)
        )
        signals = _extract_signals(combined_text)
        summary = _pick_summary(description, clean_md)

        competitors.append(
            {
                "name": title,
                "domain": domain,
                "summary": summary,
                "strength": _extract_strength(combined_text, domain, is_comparison),
                "weakness": _extract_weakness(combined_text, signals, is_comparison),
                "signals": signals,
                "source": url or "firecrawl",
                "markdown_excerpt": clean_md[:1600],
            }
        )

    meta = {
        "competitor_source": "firecrawl_live",
        "firecrawl_query": used_query,
        "firecrawl_attempts": attempts,
        "firecrawl_results": len(competitors),
        "firecrawl_warning": None,
    }
    return competitors, meta

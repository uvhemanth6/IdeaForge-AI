from __future__ import annotations

import json
import logging
import os
import re
from statistics import mean
from typing import Any

import httpx

logger = logging.getLogger(__name__)


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 1, "maximum": 10},
        "competition": {"type": "string", "enum": ["Low", "Medium", "High"]},
        "swot": {
            "type": "object",
            "properties": {
                "strengths": {"type": "array", "items": {"type": "string"}},
                "weaknesses": {"type": "array", "items": {"type": "string"}},
                "opportunities": {"type": "array", "items": {"type": "string"}},
                "threats": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["strengths", "weaknesses", "opportunities", "threats"],
            "additionalProperties": False,
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
        "suggestions": {"type": "array", "items": {"type": "string"}},
        "competitors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "domain": {"type": "string"},
                    "summary": {"type": "string"},
                    "strength": {"type": "string"},
                    "weakness": {"type": "string"},
                    "signals": {"type": "array", "items": {"type": "string"}},
                    "source": {"type": "string"},
                },
                "required": ["name", "summary", "strength", "weakness", "source"],
            },
        },
        "feasibility": {
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "go_to_market": {"type": "string"},
                "operations": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["product", "go_to_market", "operations", "notes"],
            "additionalProperties": False,
        },
        "market_pulse": {
            "type": "object",
            "properties": {
                "demand": {"type": "number"},
                "timing": {"type": "string"},
                "confidence": {"type": "string"},
            },
            "required": ["demand", "timing", "confidence"],
            "additionalProperties": False,
        },
        "memory_insights": {"type": "object"},
    },
    "required": [
        "score",
        "competition",
        "swot",
        "gaps",
        "suggestions",
        "competitors",
        "feasibility",
        "market_pulse",
        "memory_insights",
    ],
    "additionalProperties": False,
}


def _strip_internal_competitor_fields(
    competitors: list[dict[str, Any]],
    fallback_competitors: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    by_source = {
        (c.get("source") or "").strip(): c
        for c in (fallback_competitors or [])
        if c.get("source")
    }
    cleaned: list[dict[str, Any]] = []
    for idx, comp in enumerate(competitors):
        src = (comp.get("source") or "").strip()
        base = by_source.get(src) or (
            fallback_competitors[idx]
            if fallback_competitors and idx < len(fallback_competitors)
            else {}
        )
        cleaned.append(
            {
                "name": comp.get("name") or base.get("name") or "Competitor",
                "domain": comp.get("domain") or base.get("domain") or "",
                "summary": comp.get("summary") or base.get("summary") or "",
                "strength": comp.get("strength") or base.get("strength") or "",
                "weakness": comp.get("weakness") or base.get("weakness") or "",
                "signals": comp.get("signals") or base.get("signals") or [],
                "source": src or base.get("source") or "firecrawl",
            }
        )
    return cleaned


def _build_prompt(idea: dict[str, str], competitors: list[dict[str, Any]], memory_context: dict[str, Any]) -> str:
    compact_competitors = [
        {
            "name": c.get("name"),
            "domain": c.get("domain"),
            "summary": c.get("summary"),
            "signals": c.get("signals", []),
            "source": c.get("source"),
            "markdown_excerpt": (c.get("markdown_excerpt") or "")[:700],
        }
        for c in competitors
    ]
    return f"""
You are StartupScope AI, an elite startup validation analyst.
Return only valid JSON matching this schema:
{json.dumps(REPORT_SCHEMA, indent=2)}

Startup Idea:
{json.dumps(idea, indent=2)}

Live Firecrawl Web Research (real pages and markdown excerpts scraped right now):
{json.dumps(compact_competitors, indent=2)}

Historical Memory Context:
{json.dumps(memory_context, indent=2)}

Strict accuracy requirements:
- Ground your entire analysis in the live Firecrawl competitor results above and the exact startup idea details.
- Every competitor in "competitors" must be real from the Firecrawl results (preserve their real "source" URL and extract real product/company names, pricing, strengths, and weaknesses observed in their markdown_excerpt). Do NOT invent fake competitors.
- Do NOT include "markdown_excerpt" in the output JSON competitors array.
- Do NOT use generic boilerplate phrases across ideas. Every SWOT point, gap, suggestion, and feasibility note must cite specific details from this idea ({idea.get("idea_name")}) and the scraped competitors/domains.
- Score from 1 to 10 realistically based on competitive crowding, monetization clarity, and differentiation against the scraped competitors.
- competition must be "Low", "Medium", or "High".
- market_pulse.demand should be a number from 1.0 to 10.0.
- Use memory_insights to reflect historical patterns.
"""


def _parse_json_text(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return None

    return parsed if isinstance(parsed, dict) else None


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9]+", (text or "").lower())
        if len(token) > 3
    }


def _short_competitor_label(comp: dict[str, Any]) -> str:
    domain = (comp.get("domain") or "").strip()
    name = (comp.get("name") or "").strip()
    if domain and domain != "web":
        return f"{name[:48]} ({domain})" if name else domain
    return name[:55] or "Live web result"


def _extract_prices(text: str) -> list[str]:
    matches = re.findall(
        r"\$\s?\d+(?:,\d{3})*(?:\.\d{1,2})?(?:\s*/\s*(?:mo|month|yr|year|user|seat|clinic))?",
        text or "",
        flags=re.I,
    )
    seen: set[str] = set()
    prices: list[str] = []
    for m in matches:
        norm = re.sub(r"\s+", "", m)
        if norm not in seen:
            seen.add(norm)
            prices.append(norm)
    return prices[:5]


def _extract_evidence_sentence(comp: dict[str, Any], idea_tokens: set[str]) -> str:
    excerpt = comp.get("markdown_excerpt") or comp.get("summary") or ""
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!])\s+", excerpt)
        if 55 <= len(s.strip()) <= 200 and not s.strip().lower().startswith(("skip to", "sign in", "log in", "cookie"))
    ]
    if not sentences:
        return (comp.get("summary") or "")[:180]
    scored = sorted(
        sentences,
        key=lambda s: len(_tokenize(s) & idea_tokens),
        reverse=True,
    )
    return scored[0]


def _firecrawl_evidence_report(
    idea: dict[str, str],
    competitors: list[dict[str, Any]],
    memory_context: dict[str, Any],
    is_live_firecrawl: bool = True,
) -> dict[str, Any]:
    """Build an idea-specific report grounded in Firecrawl scraped pages (or fallback benchmarks if Firecrawl failed)."""
    idea_name = idea.get("idea_name", "").strip()
    problem = idea.get("problem_statement", "").strip()
    audience = idea.get("target_audience", "").strip()
    revenue = idea.get("revenue_model", "").strip()

    idea_tokens = _tokenize(f"{idea_name} {problem} {audience}")
    scraped_blob = " ".join(
        f"{c.get('name', '')} {c.get('summary', '')} {c.get('markdown_excerpt', '')}"
        for c in competitors
    )
    scraped_tokens = _tokenize(scraped_blob)

    overlap_tokens = sorted(idea_tokens & scraped_tokens)
    overlap_ratio = len(overlap_tokens) / max(len(idea_tokens), 1)

    all_signals: list[str] = []
    pricing_benchmarks: list[str] = []
    free_competitors: list[str] = []
    early_stage_competitors: list[str] = []
    direct_product_domains: list[str] = []
    evidence_quotes: list[str] = []
    numeric_prices: list[float] = []

    for comp in competitors:
        domain = comp.get("domain") or comp.get("name", "competitor")
        if domain == "fallback":
            domain = comp.get("name", "fallback benchmark")
        title = comp.get("name", "")
        is_roundup = bool(re.search(r"\b(best|top\s+\d+|alternatives|vs\.?|compared|comparison|guide|ranked|\d+\s+free)\b", title, re.I))
        if is_live_firecrawl and not is_roundup and domain not in direct_product_domains:
            direct_product_domains.append(domain)

        comp_blob = f"{title} {comp.get('summary', '')} {comp.get('markdown_excerpt', '')}"
        prices = _extract_prices(comp_blob)
        if prices:
            pricing_benchmarks.append(f"{domain} ({', '.join(prices[:2])})")
            for p in prices:
                num_match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", p)
                if num_match:
                    try:
                        numeric_prices.append(float(num_match.group(0).replace(",", "")))
                    except ValueError:
                        pass

        if is_live_firecrawl:
            quote = _extract_evidence_sentence(comp, idea_tokens)
            if quote:
                evidence_quotes.append(f"{domain}: \"{quote}\"")

        for sig in comp.get("signals") or []:
            if sig not in all_signals:
                all_signals.append(sig)
            if "Free tier" in sig or "Freemium" in sig:
                if domain not in free_competitors:
                    free_competitors.append(domain)
            if "Early-stage" in sig and domain not in early_stage_competitors:
                early_stage_competitors.append(domain)

    num_results = len(competitors) if is_live_firecrawl else 0
    has_monetization_detail = any(
        k in revenue.lower()
        for k in ["subscription", "saas", "fee", "commission", "freemium", "per ", "b2b", "monthly", "annual", "$", "₹"]
    )
    audience_specific = len(audience.split()) >= 5
    problem_specific = len(problem.split()) >= 12
    memory_penalty = 1 if memory_context.get("similar_entries") else 0
    high_incumbent_pricing = any(p >= 75 for p in numeric_prices)
    free_saturated = is_live_firecrawl and len(free_competitors) >= 3 and not high_incumbent_pricing

    if is_live_firecrawl and num_results >= 5 and (len(direct_product_domains) >= 2 or len(free_competitors) >= 3):
        competition = "High"
        comp_penalty = 1
    elif (is_live_firecrawl and num_results >= 3) or not is_live_firecrawl:
        competition = "Medium"
        comp_penalty = 0
    else:
        competition = "Low"
        comp_penalty = 0

    score = 5
    if audience_specific:
        score += 1
    if has_monetization_detail:
        score += 1
    if problem_specific:
        score += 1
    if high_incumbent_pricing:
        score += 1
    if early_stage_competitors:
        score += 1
    if free_saturated:
        score -= 1
    if is_live_firecrawl and not pricing_benchmarks and len(free_competitors) >= 2:
        score -= 1
    score -= comp_penalty
    score -= memory_penalty
    score = max(2, min(10, score))

    top_comp_names = ", ".join(
        (c.get("domain") if c.get("domain") not in (None, "", "fallback") else c.get("name", "competitor"))
        for c in competitors[:3]
    )
    focus_terms = ", ".join(overlap_tokens[:5]) or ", ".join(sorted(idea_tokens)[:4]) or idea_name

    gaps: list[str] = []
    if not is_live_firecrawl:
        gaps.append(
            f"Firecrawl live search was unavailable after retries, so '{idea_name}' still needs live competitor verification for '{audience}'."
        )
        gaps.append(
            f"Against manual/spreadsheet workarounds and incumbent suites, '{idea_name}' must prove why '{audience}' will pay via '{revenue}'."
        )
    elif high_incumbent_pricing and pricing_benchmarks:
        gaps.append(
            f"Incumbents spotted in Firecrawl ({'; '.join(pricing_benchmarks[:2])}) charge substantial rates, so '{idea_name}' must prove faster ROI or lower setup cost to justify '{revenue}'."
        )
    elif free_competitors:
        gaps.append(
            f"Multiple live web alternatives ({', '.join(free_competitors[:3])}) offer free tiers or calculators; '{idea_name}' needs a paid-only compliance, automation, or outcome hook beyond '{revenue}'."
        )
    else:
        gaps.append(
            f"Against live Firecrawl results ({top_comp_names}), '{idea_name}' must show a concrete workflow edge for '{audience}'."
        )

    if evidence_quotes:
        gaps.append(f"Live market signal — {evidence_quotes[0]}")
    if not audience_specific:
        gaps.append(f"Target audience ('{audience}') is concise/broad; narrow to a reachable first sub-segment before acquisition.")
    if not has_monetization_detail:
        gaps.append(f"Revenue model ('{revenue}') lacks concrete pricing tiers or unit economics.")
    if memory_context.get("similar_entries"):
        similar_names = ", ".join(e["idea_name"] for e in memory_context["similar_entries"][:2])
        gaps.append(f"Overlaps with prior submission(s) ({similar_names}); sharpen what is uniquely different here.")

    suggestions: list[str] = []
    if is_live_firecrawl:
        suggestions.append(
            f"Audit {competitors[0].get('domain') or competitors[0].get('name')} ({competitors[0].get('source')}) directly and build '{idea_name}' around the exact gap they miss for {audience}."
        )
    else:
        suggestions.append(
            f"Run 5 customer interviews within '{audience}' to verify how they currently handle '{problem[:80]}...' before writing code."
        )

    if high_incumbent_pricing and pricing_benchmarks:
        suggestions.append(
            f"Undercut high incumbent pricing ({pricing_benchmarks[0]}) with a self-serve tier matched to '{revenue}' and validate conversion with 5 target buyers."
        )
    elif is_live_firecrawl and free_competitors:
        suggestions.append(
            f"Avoid competing with free tools ({', '.join(free_competitors[:2])}) on basic features—monetize the verifiable output/workflow that '{audience}' will actually pay for."
        )
    else:
        suggestions.append(
            f"Validate '{revenue}' with 5–10 interviews in '{audience}' and compare willingness-to-pay against {top_comp_names}."
        )

    if len(evidence_quotes) > 1:
        suggestions.append(f"Counter live positioning noted on {evidence_quotes[1]}")
    if early_stage_competitors:
        suggestions.append(
            f"Scraped rivals ({', '.join(early_stage_competitors[:2])}) show waitlist/early-stage signals—launch a narrow paid pilot quickly."
        )

    swot = {
        "strengths": [
            f"Addresses '{audience}' with an explicit monetization plan ('{revenue}').",
            (
                f"Firecrawl confirmed active market demand around {focus_terms} across {num_results} live sources."
                if is_live_firecrawl
                else f"Focused problem framing around '{focus_terms}' provides a clear hypothesis to test."
            ),
            (
                f"High observed incumbent price points ({pricing_benchmarks[0]}) indicate proven willingness to pay."
                if high_incumbent_pricing and pricing_benchmarks
                else f"Focused problem scope ('{problem[:95]}...') supports rapid customer discovery."
            ),
        ],
        "weaknesses": gaps[:3],
        "opportunities": [
            f"Exploit competitor friction: {competitors[0].get('weakness')}",
            (
                f"Direct product rivals are still fragmented across {len(direct_product_domains)} niche site(s) vs comparison roundups."
                if is_live_firecrawl and len(direct_product_domains) <= 2
                else f"Stand out from {top_comp_names} with verticalized onboarding for '{audience}'."
            ),
            (
                f"Use the {num_results} scraped competitor URLs for direct teardown of onboarding, pricing, and messaging gaps."
                if is_live_firecrawl
                else "Re-run once Firecrawl connectivity recovers to capture live competitor URLs and pricing benchmarks."
            ),
        ],
        "threats": [
            f"{competition} competition across {top_comp_names}.",
            (
                f"Free alternatives ({', '.join(free_competitors[:2])}) compress willingness to pay for basic features."
                if is_live_firecrawl and free_competitors
                else "Established vendors or DIY habits can delay customer switching unless ROI is immediate."
            ),
            (
                f"Similarity to prior tracked ideas ({', '.join(e['idea_name'] for e in memory_context.get('similar_entries', []))}) indicates crowded positioning risk."
                if memory_context.get("similar_entries")
                else "Switching costs if target buyers already rely on manual or incumbent workflows."
            ),
        ],
    }

    demand_score = round(
        mean([
            score,
            min(9.6, 5.2 + overlap_ratio * 3.8 + (0.6 if high_incumbent_pricing else 0.0) + min(num_results, 5) * 0.25)
            if is_live_firecrawl
            else max(4.5, score - 0.5),
        ]),
        1,
    )
    timing = (
        "Strong"
        if (high_incumbent_pricing or early_stage_competitors)
        else ("Good" if (is_live_firecrawl and overlap_ratio >= 0.2) else "Moderate")
    )

    is_marketplace_or_ops = any(k in f"{problem} {revenue}".lower() for k in ["marketplace", "delivery", "clinic", "farmer", "logistics", "commission", "hardware", "manufacturing"])
    product_feasibility = "Moderate-High" if not is_marketplace_or_ops else "Moderate"
    gtm_feasibility = "Challenging" if free_saturated else ("Good" if high_incumbent_pricing else "Moderate")
    ops_feasibility = "Moderate" if is_marketplace_or_ops else "High"

    return {
        "score": score,
        "competition": competition,
        "swot": swot,
        "gaps": gaps[:5],
        "suggestions": suggestions[:5],
        "competitors": _strip_internal_competitor_fields(competitors),
        "feasibility": {
            "product": product_feasibility,
            "go_to_market": gtm_feasibility,
            "operations": ops_feasibility,
            "notes": (
                (
                    f"Grounded in {num_results} live Firecrawl sources ({top_comp_names}). "
                    + (
                        f"Observed market pricing includes {'; '.join(pricing_benchmarks[:2])}. "
                        if pricing_benchmarks
                        else f"Free-tier competition detected on {', '.join(free_competitors[:2])}. "
                        if free_competitors
                        else ""
                    )
                )
                if is_live_firecrawl
                else "Generated in fallback mode after Firecrawl retries failed. Re-run when Firecrawl is reachable for live URLs and pricing. "
            )
            + f"Main GTM hurdle for '{idea_name}' is winning '{audience}' under {competition.lower()} competition.",
        },
        "market_pulse": {
            "demand": demand_score,
            "timing": timing,
            "confidence": f"Live Web ({num_results} sources)" if is_live_firecrawl else "Fallback (no live web data)",
        },
        "memory_insights": memory_context,
    }


def contextual_top_gap(idea: dict[str, str], gap: str | None) -> str:
    cleaned = (gap or "").strip()
    if cleaned:
        return cleaned
    return f"'{idea.get('idea_name', 'This idea')}' needs clearer differentiation for '{idea.get('target_audience', 'its target users')}'."


async def _openai_report(
    idea: dict[str, str], competitors: list[dict[str, Any]], memory_context: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return None, "OPENAI_API_KEY is not configured."

    try:
        from openai import AsyncOpenAI
    except ImportError:
        return None, "openai package is not installed."

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    client = AsyncOpenAI(api_key=api_key)
    prompt = _build_prompt(idea, competitors, memory_context)

    try:
        response = await client.responses.create(
            model=model,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "startup_validation_report",
                    "schema": REPORT_SCHEMA,
                    "strict": True,
                }
            },
        )
        parsed = json.loads(response.output_text)
        return parsed, None
    except Exception as exc:
        logger.warning("OpenAI report generation failed: %s", exc)
        return None, f"OpenAI error: {exc}"


async def _gemini_report(
    idea: dict[str, str], competitors: list[dict[str, Any]], memory_context: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        return None, None, "GEMINI_API_KEY is not configured."

    configured_model = (os.getenv("GEMINI_MODEL") or "gemini-3-flash-preview").strip()
    candidate_models: list[str] = []
    for m in [
        configured_model,
        "gemini-3-flash-preview",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
    ]:
        if m and m not in candidate_models:
            candidate_models.append(m)

    prompt = _build_prompt(idea, competitors, memory_context)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}

    last_error = "Gemini request failed."
    async with httpx.AsyncClient(timeout=30.0) as client:
        for model in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    err_msg = response.text[:250]
                    try:
                        err_json = response.json()
                        err_msg = err_json.get("error", {}).get("message") or err_msg
                    except Exception:
                        pass
                    last_error = f"Gemini API ({response.status_code}, {model}): {err_msg}"
                    logger.warning("Gemini API returned %s for %s: %s", response.status_code, model, err_msg)
                    if response.status_code in (404, 429, 500, 502, 503, 504):
                        continue
                    return None, None, last_error
                data = response.json()
            except Exception as exc:
                last_error = f"Gemini request error ({model}): {exc}"
                logger.warning("Gemini request failed for %s: %s", model, exc)
                continue

            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
            parsed = _parse_json_text(text)
            if parsed:
                return parsed, model, None
            last_error = f"Gemini ({model}) returned non-JSON or empty content."

    return None, None, last_error


async def generate_validation_report(
    idea: dict[str, str],
    competitors: list[dict[str, Any]],
    memory_context: dict[str, Any],
    firecrawl_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = os.getenv("LLM_PROVIDER", "auto").strip().lower()
    if provider == "openai":
        providers = [("openai", _openai_report), ("gemini", _gemini_report)]
    else:
        providers = [("gemini", _gemini_report), ("openai", _openai_report)]

    report: dict[str, Any] | None = None
    active_engine = "firecrawl_evidence"
    llm_errors: list[str] = []

    for engine_name, provider_fn in providers:
        if engine_name == "gemini":
            candidate, model_used, error_msg = await provider_fn(idea, competitors, memory_context)
        else:
            candidate, error_msg = await provider_fn(idea, competitors, memory_context)
            model_used = os.getenv("OPENAI_MODEL", "gpt-4.1-mini") if candidate else None

        if candidate:
            report = candidate
            active_engine = f"{engine_name} ({model_used})" if model_used else engine_name
            break
        if error_msg:
            llm_errors.append(error_msg)

    is_live_firecrawl = (firecrawl_meta or {}).get("competitor_source") == "firecrawl_live"

    if report:
        cleaned_competitors = _strip_internal_competitor_fields(
            report.get("competitors") or competitors,
            fallback_competitors=competitors,
        )
        report["competitors"] = cleaned_competitors
        report["memory_insights"] = memory_context
    else:
        active_engine = "firecrawl_evidence" if is_live_firecrawl else "fallback_evidence"
        report = _firecrawl_evidence_report(
            idea,
            competitors,
            memory_context,
            is_live_firecrawl=is_live_firecrawl,
        )

    report["analysis_meta"] = {
        **(firecrawl_meta or {}),
        "analysis_engine": active_engine,
        "llm_warning": llm_errors[0] if active_engine in ("firecrawl_evidence", "fallback_evidence") and llm_errors else None,
    }
    return report

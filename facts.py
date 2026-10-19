"""
Fact checking for drafts, and learning from what the user changes before sending.

A "fact" is anything a customer could hold you to: prices, percentages, times, dates, durations,
phone numbers, links, and promises (refunds, discounts, free, guarantees). Each fact in a draft is
checked against the sources the AI was allowed to use. Supported facts show green; anything else
shows amber so the user only has to check those.
"""

import difflib
import re

import pipeline

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
            r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")

PROMISES = {
    "refund": r"\brefund(?:s|ed|able)?\b",
    "discount": r"\bdiscount(?:s|ed)?\b",
    "free": r"\b(?:free of charge|for free|at no (?:extra )?(?:charge|cost)|no charge|complimentary|free)\b",
    "guarantee": r"\bguarantee[sd]?\b",
    "waive": r"\bwaiv(?:e|ed|ing)\b",
    "same_day": r"\bsame[- ]day\b",
}

PATTERNS = [
    ("url", re.compile(r"https?://[^\s<>()]+|www\.[^\s<>()]+", re.I)),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("money", re.compile(r"[$€£]\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?:\s?[kK]\b)?|"
                         r"\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?\s?(?:USD|EUR|GBP|dollars?)\b")),
    ("percent", re.compile(r"\b\d{1,3}(?:\.\d+)?\s?(?:%|percent\b)", re.I)),
    ("phone", re.compile(r"(?<![\w$])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")),
    ("time", re.compile(r"\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s?(?:a\.m\.|p\.m\.|am|pm)(?![a-z])|\b(?:[01]?\d|2[0-3]):[0-5]\d\b|\bnoon\b|\bmidnight\b", re.I)),
    ("date", re.compile(rf"\b{MONTH_RE}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?{MONTH_RE}\b|"
                        r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", re.I)),
    ("weekday", re.compile(r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", re.I)),
    ("relative", re.compile(r"\b(?:today|tonight|tomorrow|this (?:morning|afternoon|evening|week|weekend)|next (?:week|month))\b", re.I)),
    ("duration", re.compile(r"\b\d+(?:\.\d+)?(?:\s?(?:-|–|to)\s?\d+(?:\.\d+)?)?\s?(?:hours?|hrs?|minutes?|mins?|days?|weeks?|months?|years?)\b|\b\d+h\b", re.I)),
] + [(f"promise:{name}", re.compile(rx, re.I)) for name, rx in PROMISES.items()]


def _num(text):
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    if not m:
        return ""
    value = float(m.group(0).replace(",", ""))
    if re.search(r"\d\s?[kK]\b", text):
        value *= 1000
    return f"{value:g}"


def _fact_key(kind, text):
    low = text.lower().strip()
    if kind == "money":
        return f"money:{_num(text)}"
    if kind == "percent":
        return f"pct:{_num(text)}"
    if kind == "phone":
        return "phone:" + re.sub(r"\D", "", text)[-10:]
    if kind == "url":
        return "url:" + re.sub(r"^https?://(www\.)?|^www\.|[/.,;:!?)]+$", "", low)
    if kind == "email":
        return f"email:{low}"
    if kind == "time":
        if low in ("noon", "midnight"):
            return "time:12:00" if low == "noon" else "time:00:00"
        m = re.match(r"(\d{1,2})(?::(\d{2}))?\s?([ap])?", low)
        hour, minute, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "p" and hour < 12:
            hour += 12
        if ap == "a" and hour == 12:
            hour = 0
        return f"time:{hour:02d}:{minute:02d}"
    if kind == "date":
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})$", low)
        if m:
            return f"date:{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        m = re.match(r"(\d{1,2})/(\d{1,2})", low)
        if m:
            return f"date:{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        month = next((MONTHS[k] for k in MONTHS if re.search(rf"\b{k}", low)), 0)
        day = int(re.search(r"\d{1,2}", low).group(0))
        return f"date:{month:02d}-{day:02d}"
    if kind == "weekday":
        return "day:" + low.rstrip("s")[:3]
    if kind == "relative":
        return "rel:" + re.sub(r"\s+", " ", low)
    if kind == "duration":
        unit = re.search(r"(hour|hr|h\b|minute|min|day|week|month|year)", low).group(1)
        unit = {"hr": "hour", "h": "hour", "min": "minute"}.get(unit, unit)
        return f"dur:{_num(text)} {unit}"
    if kind.startswith("promise:"):
        return kind
    return f"{kind}:{low}"


def extract_facts(text):
    text = text or ""
    found = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            surface = m.group(0).rstrip(".,;:!?)") if kind == "url" else m.group(0)
            if not surface.strip():
                continue
            found.append({"start": m.start(), "end": m.start() + len(surface), "text": surface,
                          "kind": kind.split(":")[0], "key": _fact_key(kind, surface)})
    found.sort(key=lambda f: (f["start"], -(f["end"] - f["start"])))
    out, last_end = [], -1
    for f in found:
        if f["start"] >= last_end:
            out.append(f)
            last_end = f["end"]
    return out


def _sources(conn, item, extra=None):
    email_text = f"{item.get('subject', '')}\n{item.get('body') or ''}\n" + "\n".join(
        m.get("body") or "" for m in pipeline.local_thread(conn, {"id": item.get("gmail_id"), "thread_id": item.get("thread_id")}))
    profile_text = pipeline.format_business_profile(pipeline.get_business_profile(conn))
    query = f"{item.get('subject', '')}\n{(item.get('body') or '')[:2500]}"
    files_text = "\n".join(k["text"] for k in pipeline.search_knowledge(conn, query, k=6))
    templates_text = "\n".join(t["body"] for t in pipeline.list_templates(conn)[:30])
    sign_text = f"{pipeline.get_setting(conn, 'user_signature', '')}\n{pipeline.get_setting(conn, 'user_name', '')}"
    sources = [("the email", email_text), ("your profile", profile_text), ("your files", files_text),
               ("your saved replies", templates_text), ("your signature", sign_text)]
    if extra:
        sources.append(("your instruction", extra))
    return sources


def check_text(conn, item, text, extra=None):
    """Facts in `text`, each marked supported (with where it came from) or not."""
    keys, numbers = {}, {}
    for label, source in _sources(conn, item, extra):
        for fact in extract_facts(source):
            keys.setdefault(fact["key"], label)
        for n in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", source):
            numbers.setdefault(_num(n), label)
    facts = []
    for fact in extract_facts(text):
        source = keys.get(fact["key"])
        if not source and fact["kind"] == "money":
            source = numbers.get(_num(fact["text"]))  # "$240" in the draft vs "240 for a move-out" in a file
        facts.append({**fact, "supported": bool(source), "source": source})
    return {"facts": facts, "checked": len(facts), "unverified": sum(1 for f in facts if not f["supported"])}


# ---------------------------------------------------------------------------
# Learning from edits
# ---------------------------------------------------------------------------

LEARNABLE = {"money", "percent", "time", "duration", "url", "phone", "email", "promise"}
FIELD_FOR = {"money": "services_prices", "percent": "services_prices", "time": "hours_location",
             "duration": "hours_location", "url": "links", "phone": "links", "email": "links"}
PROMISE_FIELD = {"discount": "services_prices"}


def _field_for(fact):
    if fact["kind"] == "promise":
        return PROMISE_FIELD.get(fact["key"].split(":")[1], "policies")
    return FIELD_FOR.get(fact["kind"], "faq")


def _sentence(text, start):
    bounds = [m.end() for m in re.finditer(r"[.!?]\s+|\n+", text[:start])]
    begin = bounds[-1] if bounds else 0
    end_m = re.search(r"[.!?](?=\s|$)|\n", text[start:])
    end = start + end_m.end() if end_m else len(text)
    return text[begin:end].strip()


def similarity(a, b):
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return None
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 3)


def learn_from_send(conn, item, sent_text):
    """Compare the AI's draft with what the user actually sent and suggest how to teach the app."""
    ai = item.get("ai_draft") or ""
    sim = similarity(ai, sent_text)
    if not ai:
        return {"similarity": None, "suggestions": [], "offer_template": False}
    ai_facts = check_text(conn, item, ai)["facts"]
    sent_facts = check_text(conn, item, sent_text)["facts"]
    ai_keys = {f["key"] for f in ai_facts}
    sent_keys = {f["key"] for f in sent_facts}
    removed = [f for f in ai_facts if f["key"] not in sent_keys and f["kind"] in LEARNABLE]
    added = [f for f in sent_facts if f["key"] not in ai_keys and not f["supported"] and f["kind"] in LEARNABLE]
    profile = pipeline.get_business_profile(conn)
    labels = dict(pipeline.BUSINESS_FIELDS)
    suggestions = []
    for fact in added:
        old = next((r for r in removed if r["kind"] == fact["kind"] and r["source"] == "your profile"), None)
        if old:
            removed.remove(old)
            field = next((k for k, v in profile.items() if old["text"] in v), None) or next(
                (k for k, v in profile.items() if _num(old["text"]) and _num(old["text"]) in re.findall(r"\d[\d,.]*", v)),
                _field_for(fact))
            suggestions.append({"type": "replace", "field": field, "field_label": labels.get(field, field),
                                "old": old["text"], "new": fact["text"], "context": _sentence(sent_text, fact["start"])})
        else:
            field = _field_for(fact)
            suggestions.append({"type": "add", "field": field, "field_label": labels.get(field, field),
                                "new": fact["text"], "text": _sentence(sent_text, fact["start"])})
    return {"similarity": sim, "suggestions": suggestions[:3],
            "offer_template": sim is not None and sim < 0.6 and len(sent_text.strip()) > 120}


def apply_learning(conn, suggestion):
    profile = pipeline.get_business_profile(conn)
    field = suggestion.get("field")
    if field not in profile:
        raise ValueError("Unknown profile field.")
    current = profile[field]
    if suggestion.get("type") == "replace" and suggestion.get("old") and suggestion["old"] in current:
        profile[field] = current.replace(suggestion["old"], suggestion["new"], 1)
    else:
        line = (suggestion.get("text") or suggestion.get("new") or "").strip()
        if not line:
            raise ValueError("Nothing to add.")
        profile[field] = f"{current}\n{line}".strip() if line not in current else current
    pipeline.save_business_profile(conn, profile)
    return {"field": field, "value": profile[field]}

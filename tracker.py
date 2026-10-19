"""Industry trackers: the work behind the email.

Tax & bookkeeping: each client's return, its status, and the documents they still owe, with reminder emails that
list exactly what's missing. Insurance: certificate-of-insurance requests with due dates, and policy renewals.
Everything is stored in the same local database as the rest of C.A.P.; nothing here uses AI or the network.
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Kinds and statuses
# ---------------------------------------------------------------------------

KINDS = {
    "client": {"label": "Client return", "industry": "tax", "statuses": [
        ("waiting_docs", "Waiting on documents"), ("in_prep", "In preparation"), ("ready_to_sign", "Ready to sign"),
        ("extended", "Extended"), ("filed", "Filed")]},
    "coi": {"label": "Certificate request", "industry": "insurance", "statuses": [
        ("requested", "Requested"), ("checking", "Checking with carrier"), ("issued", "Issued")]},
    "renewal": {"label": "Renewal", "industry": "insurance", "statuses": [
        ("upcoming", "Upcoming"), ("reshopping", "Re-shopping"), ("renewed", "Renewed"), ("lost", "Lost")]},
}
OPEN_STATUSES = {"waiting_docs", "in_prep", "ready_to_sign", "extended", "requested", "checking", "upcoming", "reshopping"}

# Documents a tax client may owe, and how clients usually refer to them in email.
DOC_TYPES = [
    ("W-2", r"\bw-?2s?\b"),
    ("1099-NEC", r"\b1099-?necs?\b"),
    ("1099-K", r"\b1099-?ks?\b"),
    ("1099-INT", r"\b1099-?ints?\b"),
    ("1099-DIV", r"\b1099-?divs?\b"),
    ("1099-B", r"\b1099-?bs?\b|brokerage statement"),
    ("1099-R", r"\b1099-?rs?\b"),
    ("SSA-1099", r"\bssa-?1099\b|social security (?:benefit )?statement"),
    ("1098 mortgage interest", r"\b1098\b(?!-)|mortgage (?:interest )?statement"),
    ("1098-T tuition", r"\b1098-?t\b|tuition statement"),
    ("1098-E student loan", r"\b1098-?e\b|student loan (?:interest )?statement"),
    ("1095-A", r"\b1095-?a\b|marketplace (?:insurance )?statement"),
    ("Prior-year return", r"(?:last|prior|previous) year'?s? (?:tax )?return"),
    ("Photo ID", r"photo id|copy of (?:my |our )?(?:driver'?s )?licen[cs]e|passport"),
    ("Property tax statement", r"property tax (?:statement|bill)"),
    ("Estimated payment records", r"estimated (?:tax )?payments?"),
    ("Childcare provider info", r"child ?care|daycare"),
    ("Charitable receipts", r"donation receipts?|charitable"),
    ("Business income & expenses", r"\bp&l\b|profit (?:and|&) loss|income (?:and|&) expenses?|expense (?:report|summary|spreadsheet)"),
    ("Mileage log", r"mileage"),
    ("Rental income & expenses", r"rental (?:income|expenses)|rent roll"),
    ("Signed engagement letter", r"engagement letter"),
]
DOC_NAMES = [d for d, _ in DOC_TYPES]
_DOC_RX = {d: re.compile(rx, re.I) for d, rx in DOC_TYPES}
_SENT_CONTEXT = re.compile(r"\b(?:attach\w*|upload\w*|sent|sending|here (?:is|are)|enclosed|dropped|added|scanned|"
                           r"in the portal)\b", re.I)
DEFAULT_CLIENT_DOCS = ["W-2", "1099-INT", "1098 mortgage interest", "Prior-year return", "Photo ID"]

_COI = re.compile(r"\b(?:certificate of (?:liability )?insurance|certificates? of insurance|\bcoi\b|"
                  r"additional insured|proof of (?:insurance|coverage))", re.I)
_RENEWAL = re.compile(r"\brenew(?:al|als|s|ing|ed)?\b", re.I)
_HOLDER = re.compile(r"\b(?:for|naming|to)\s+((?:[A-Z][\w&'.-]*\s?){1,5}?)(?:\s+(?:by|before|as|on|and)\b|[.,!?\n])")

# ---------------------------------------------------------------------------
# Federal tax calendar (individual and business filers), worked out for any year. A deadline that lands on a
# weekend or a Washington, D.C. legal holiday moves to the next business day, as the IRS does. State dates and IRS
# disaster postponements differ; firms add those themselves as custom dates.
# ---------------------------------------------------------------------------

def _nth_weekday(year, month, weekday, n):
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _observed(d):
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def _dc_holidays(year):
    days = {_observed(date(year, m, d)) for m, d in ((1, 1), (4, 16), (6, 19), (7, 4), (11, 11), (12, 25))}
    days |= {_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _last_monday(year, 5),
             _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 10, 0, 2), _nth_weekday(year, 11, 3, 4)}
    days.add(_observed(date(year + 1, 1, 1)))  # New Year's Day on a Saturday is observed on Dec 31
    return days


def _last_monday(year, month):
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=d.weekday())


def _business_day(d):
    holidays = _dc_holidays(d.year)
    while d.weekday() >= 5 or d in holidays:
        d += timedelta(days=1)
    return d


def federal_deadlines(year):
    """The federal deadlines that fall in a calendar year, already moved off weekends and holidays."""
    rules = [
        (1, 15, f"Q4 {year - 1} estimated tax payment due"),
        (1, 31, "W-2s and 1099-NECs due to recipients and the IRS"),
        (3, 15, "S corporation (1120-S) and partnership (1065) returns due"),
        (4, 15, f"Individual returns, extension requests and Q1 {year} estimated tax due"),
        (6, 15, f"Q2 {year} estimated tax payment due"),
        (9, 15, "Extended S corp and partnership returns; Q3 estimated tax due"),
        (10, 15, f"Extended {year - 1} individual returns due"),
    ]
    return [(_business_day(date(year, m, d)).isoformat(), label) for m, d, label in rules]


def _iso():
    return datetime.now(timezone.utc).isoformat()


def _today():
    return datetime.now().astimezone().date()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS tracker_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, name TEXT NOT NULL, email TEXT,
        status TEXT NOT NULL, due_at TEXT, data TEXT, source_email TEXT, is_demo INTEGER DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracker_email ON tracker_items(email)")
    conn.execute("""CREATE TABLE IF NOT EXISTS key_dates_custom (
        id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, label TEXT NOT NULL, client_id INTEGER,
        yearly INTEGER DEFAULT 0, created_at TEXT NOT NULL)""")


def _row(r):
    item = dict(zip(("id", "kind", "name", "email", "status", "due_at", "data", "source_email", "is_demo",
                     "created_at", "updated_at"), r))
    try:
        item["data"] = json.loads(item["data"] or "{}")
    except json.JSONDecodeError:
        item["data"] = {}
    item["is_demo"] = bool(item["is_demo"])
    item["status_label"] = dict(KINDS[item["kind"]]["statuses"]).get(item["status"], item["status"])
    item["open"] = item["status"] in OPEN_STATUSES
    if item["kind"] == "client":
        docs = item["data"].get("docs", [])
        item["docs_outstanding"] = [d["name"] for d in docs if not d.get("received")]
        item["docs_total"] = len(docs)
    if item["due_at"]:
        item["days_left"] = (date.fromisoformat(item["due_at"][:10]) - _today()).days
    return item


COLUMNS = "id, kind, name, email, status, due_at, data, source_email, is_demo, created_at, updated_at"


def list_items(conn, kind=None):
    sql = f"SELECT {COLUMNS} FROM tracker_items"
    params = ()
    if kind:
        sql, params = sql + " WHERE kind = ?", (kind,)
    sql += (" ORDER BY CASE WHEN status IN ('filed', 'issued', 'renewed', 'lost') THEN 1 ELSE 0 END, "
            "CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at, name COLLATE NOCASE")
    return [_row(r) for r in conn.execute(sql, params).fetchall()]


def get_item(conn, item_id):
    r = conn.execute(f"SELECT {COLUMNS} FROM tracker_items WHERE id = ?", (item_id,)).fetchone()
    return _row(r) if r else None


def _clean(kind, values, existing=None):
    if kind not in KINDS:
        raise ValueError("Unknown tracker type.")
    base = existing or {}
    name = (values.get("name", base.get("name")) or "").strip()[:200]
    if not name:
        raise ValueError("Give it a name.")
    status = values.get("status") or base.get("status") or KINDS[kind]["statuses"][0][0]
    if status not in dict(KINDS[kind]["statuses"]):
        raise ValueError("Unknown status.")
    due = values.get("due_at", base.get("due_at")) or None
    if due:
        try:
            due = date.fromisoformat(due[:10]).isoformat()
        except ValueError as exc:
            raise ValueError("Dates look like 2027-04-15.") from exc
    email = (values.get("email", base.get("email")) or "").strip().lower() or None
    data = {**base.get("data", {}), **(values.get("data") or {})}
    if kind == "client":
        docs = data.get("docs")
        if docs is None:
            docs = [{"name": d, "received": False} for d in DEFAULT_CLIENT_DOCS]
        data["docs"] = [{"name": str(d["name"])[:80], "received": bool(d.get("received"))} for d in docs][:40]
    return name, email, status, due, data


def save_item(conn, kind, values, item_id=None, is_demo=False):
    existing = get_item(conn, item_id) if item_id else None
    if item_id and not existing:
        raise ValueError("Not found.")
    name, email, status, due, data = _clean(kind, values, existing)
    if existing:
        conn.execute("UPDATE tracker_items SET name = ?, email = ?, status = ?, due_at = ?, data = ?, updated_at = ? "
                     "WHERE id = ?", (name, email, status, due, json.dumps(data), _iso(), item_id))
    else:
        cur = conn.execute(
            "INSERT INTO tracker_items (kind, name, email, status, due_at, data, source_email, is_demo, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, name, email, status, due, json.dumps(data), values.get("source_email"), 1 if is_demo else 0,
             _iso(), _iso()))
        item_id = cur.lastrowid
    conn.commit()
    return get_item(conn, item_id)


def delete_item(conn, item_id):
    conn.execute("DELETE FROM tracker_items WHERE id = ?", (item_id,))
    conn.execute("DELETE FROM key_dates_custom WHERE client_id = ?", (item_id,))
    conn.commit()


def set_docs(conn, item_id, names, received=True):
    item = get_item(conn, item_id)
    if not item or item["kind"] != "client":
        raise ValueError("Not a tracked client.")
    docs = item["data"].get("docs", [])
    known = {d["name"] for d in docs}
    for n in names:
        if n not in known:
            docs.append({"name": n, "received": received})
    for d in docs:
        if d["name"] in names:
            d["received"] = received
    status = item["status"]
    if received and status == "waiting_docs" and docs and all(d["received"] for d in docs):
        status = "in_prep"  # everything is in: ready for preparation
    return save_item(conn, "client", {"data": {"docs": docs}, "status": status}, item_id)


# ---------------------------------------------------------------------------
# Reading email: what does this message mean for the trackers?
# ---------------------------------------------------------------------------

def detect_documents(text, candidates=None):
    """Document types the email says were sent or uploaded (only when it says so)."""
    text = text or ""
    if not _SENT_CONTEXT.search(text):
        return []
    names = candidates if candidates is not None else DOC_NAMES
    return [n for n in names if n in _DOC_RX and _DOC_RX[n].search(text)]


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def parse_due(text, today=None):
    """A due date from phrases like 'by Friday', 'tomorrow', 'on the 28th', 'Oct 28' or '10/28'."""
    today = today or _today()
    t = (text or "").lower()
    m = re.search(r"\b(?:by|before|on|until|due)\s+(?:this\s+|next\s+)?(monday|tuesday|wednesday|thursday|friday|"
                  r"saturday|sunday)\b", t) or re.search(r"\b(monday|tuesday|wednesday|thursday|friday)\b", t)
    if re.search(r"\b(?:by |before )?end of (?:the )?day\b|\btoday\b", t) and not m:
        return today.isoformat()
    if re.search(r"\btomorrow\b", t):
        return (today + timedelta(days=1)).isoformat()
    if m:
        ahead = (_WEEKDAYS.index(m.group(1)) - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).isoformat()
    m = re.search(r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        month, day = _MONTHS.index(m.group(1)) + 1, int(m.group(2))
        return _next_date(today, month, day)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m and 1 <= int(m.group(1)) <= 12:
        if m.group(3):
            year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
            try:
                return date(year, int(m.group(1)), int(m.group(2))).isoformat()
            except ValueError:
                return None
        return _next_date(today, int(m.group(1)), int(m.group(2)))
    m = re.search(r"\bon the (\d{1,2})(?:st|nd|rd|th)\b", t)
    if m:
        day = int(m.group(1))
        for add in range(0, 2):
            month = (today.month - 1 + add) % 12 + 1
            year = today.year + (today.month - 1 + add) // 12
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if d >= today:
                return d.isoformat()
    return None


def _next_date(today, month, day):
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d >= today - timedelta(days=7):
            return d.isoformat()
    return None


def match_items(conn, address):
    if not address:
        return []
    rows = conn.execute(f"SELECT {COLUMNS} FROM tracker_items WHERE email = ?", (address.lower(),)).fetchall()
    return [_row(r) for r in rows]


def suggestions_for_email(conn, email, industry):
    """What the reader should offer for this email: tick off documents, or start tracking something."""
    name, address = parseaddr(email.get("sender") or "")
    text = f"{email.get('subject') or ''}\n{email.get('body') or ''}"
    out = {"tracked": [], "docs": None, "offers": []}
    items = match_items(conn, address)
    out["tracked"] = [{"id": i["id"], "kind": i["kind"], "name": i["name"], "status_label": i["status_label"],
                       "due_at": i["due_at"], "docs_outstanding": i.get("docs_outstanding")} for i in items]
    if industry == "tax":
        client = next((i for i in items if i["kind"] == "client" and i["open"]), None)
        if client:
            found = detect_documents(text, client["docs_outstanding"])
            if found:
                out["docs"] = {"client_id": client["id"], "client": client["name"], "names": found}
        elif email.get("action") == "DRAFT" and address:
            out["offers"].append({"kind": "client", "label": "Track this client's return",
                                  "prefill": {"name": name or address, "email": address,
                                              "data": {"docs": [{"name": d, "received": d in detect_documents(text)}
                                                                for d in DEFAULT_CLIENT_DOCS]}}})
    elif industry == "insurance":
        open_kinds = {i["kind"] for i in items if i["open"]}
        if _COI.search(text) and "coi" not in open_kinds:
            holder = _HOLDER.search(email.get("body") or "")
            out["offers"].append({"kind": "coi", "label": "Track this certificate request",
                                  "prefill": {"name": name or address, "email": address, "due_at": parse_due(text),
                                              "data": {"holder": holder.group(1).strip() if holder else "",
                                                       "subject": email.get("subject", "")}}})
        if _RENEWAL.search(text) and "renewal" not in open_kinds:
            out["offers"].append({"kind": "renewal", "label": "Track this renewal",
                                  "prefill": {"name": name or address, "email": address, "due_at": parse_due(text),
                                              "data": {"policy": "", "carrier": ""}}})
    return out


# ---------------------------------------------------------------------------
# Reminders and the key-dates card on Today
# ---------------------------------------------------------------------------

def _portal_link(links):
    urls = re.findall(r"https?://\S+", links or "")
    portal = [u for u in urls if re.search(r"portal|secure|upload", u, re.I)]
    return (portal or [None])[0]


def reminder_for(item, sign_off, profile):
    """A ready-to-send email for a tracked item."""
    first = (item["name"] or "").split()[0] if item["name"] else "there"
    portal = _portal_link(profile.get("links"))
    if item["kind"] == "client":
        missing = item.get("docs_outstanding") or []
        year = item["data"].get("tax_year") or ""
        lines = "\n".join(f"- {d}" for d in missing) or "- (nothing outstanding)"
        subject = f"Documents still needed for your {year} return".replace("  ", " ")
        body = (f"Hi {first},\n\nThanks for working with us on your {year + ' ' if year else ''}return. "
                f"To finish it, we still need:\n\n{lines}\n\nPlease upload "
                + (f"them through our secure client portal: {portal}" if portal else "them through our secure client portal")
                + "\nPlease don't send tax documents or Social Security numbers by email. We prepare returns in the "
                  f"order complete documents arrive.\n\n{sign_off}")
    elif item["kind"] == "renewal":
        when = _fmt_date(item["due_at"]) if item["due_at"] else "soon"
        policy = item["data"].get("policy") or "policy"
        carrier = item["data"].get("carrier")
        subject = f"Your {policy} renews {when}"
        body = (f"Hi {first},\n\nYour {policy}{' with ' + carrier if carrier else ''} renews on {when}. I'll review "
                "the renewal terms and, if it makes sense, compare coverage with other carriers so you have options "
                "before it renews. Has anything changed that I should know about, such as vehicles, drivers, home "
                f"updates or the business?\n\n{sign_off}")
    else:
        holder = item["data"].get("holder") or "the certificate holder"
        subject = f"Certificate of insurance for {holder}"
        body = (f"Hi {first},\n\nYour certificate of insurance for {holder} is attached. Let me know if they need "
                f"anything else.\n\n{sign_off}")
    mailto = f"mailto:{item['email'] or ''}?subject={quote(subject)}&body={quote(body)}"
    return {"to": item["email"], "subject": subject, "text": body, "mailto": mailto}


def _fmt_date(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ---------------------------------------------------------------------------
# Custom dates: state deadlines, firm dates, and each client's own timeline
# ---------------------------------------------------------------------------

def _next_yearly(d, today):
    """This year's or next year's date with the same month and day (Feb 29 falls back to Feb 28)."""
    for year in (today.year, today.year + 1):
        try:
            when = d.replace(year=year)
        except ValueError:
            when = date(year, 2, 28)
        if when >= today:
            return when
    return when


def list_dates(conn, client_id=None, today=None):
    """Custom dates with the next time each one comes round; client dates carry the client's name."""
    today = today or _today()
    sql = ("SELECT k.id, k.date, k.label, k.client_id, k.yearly, t.name FROM key_dates_custom k "
           "LEFT JOIN tracker_items t ON t.id = k.client_id WHERE (k.client_id IS NULL OR t.id IS NOT NULL)")
    params = ()
    if client_id is not None:
        sql, params = sql + " AND k.client_id = ?", (client_id,)
    out = []
    for id_, iso, label, cid, yearly, name in conn.execute(sql, params).fetchall():
        when = date.fromisoformat(iso)
        if yearly:
            when = _next_yearly(when, today)
        out.append({"id": id_, "date": when.isoformat(), "set_date": iso, "label": label, "client_id": cid,
                    "client_name": name, "yearly": bool(yearly), "days_left": (when - today).days})
    out.sort(key=lambda d: d["date"])
    return out


def get_date(conn, date_id):
    return next((d for d in list_dates(conn) if d["id"] == date_id), None)


def save_date(conn, values, date_id=None):
    label = (values.get("label") or "").strip()[:200]
    if not label:
        raise ValueError("Say what's due.")
    try:
        iso = date.fromisoformat((values.get("date") or "")[:10]).isoformat()
    except ValueError as exc:
        raise ValueError("Dates look like 2027-04-15.") from exc
    client_id = values.get("client_id") or None
    if client_id is not None:
        client = get_item(conn, int(client_id))
        if not client or client["kind"] != "client":
            raise ValueError("That client isn't tracked.")
        client_id = client["id"]
    yearly = 1 if values.get("yearly") else 0
    if date_id:
        if not conn.execute("SELECT 1 FROM key_dates_custom WHERE id = ?", (date_id,)).fetchone():
            raise ValueError("Not found.")
        conn.execute("UPDATE key_dates_custom SET date = ?, label = ?, client_id = ?, yearly = ? WHERE id = ?",
                     (iso, label, client_id, yearly, date_id))
    else:
        date_id = conn.execute("INSERT INTO key_dates_custom (date, label, client_id, yearly, created_at) "
                               "VALUES (?, ?, ?, ?, ?)", (iso, label, client_id, yearly, _iso())).lastrowid
    conn.commit()
    return get_date(conn, date_id)


def delete_date(conn, date_id):
    conn.execute("DELETE FROM key_dates_custom WHERE id = ?", (date_id,))
    conn.commit()


def key_dates(conn, industry, today=None, horizon=120, limit=6):
    today = today or _today()
    out = {"industry": industry, "deadlines": [], "items": [], "counts": {}}
    if industry == "tax":
        for year in (today.year, today.year + 1):
            for iso, label in federal_deadlines(year):
                days = (date.fromisoformat(iso) - today).days
                if 0 <= days <= horizon:
                    out["deadlines"].append({"date": iso, "label": label, "days_left": days, "source": "federal"})
        for d in list_dates(conn, today=today):
            if 0 <= d["days_left"] <= horizon:
                out["deadlines"].append({"date": d["date"], "label": d["label"], "days_left": d["days_left"],
                                         "source": "custom", "id": d["id"], "client_id": d["client_id"],
                                         "client_name": d["client_name"]})
        out["deadlines"].sort(key=lambda d: (d["date"], d["source"] != "federal"))
        out["deadlines"] = out["deadlines"][:limit]
        clients = list_items(conn, "client")
        for key, label in KINDS["client"]["statuses"]:
            out["counts"][label] = sum(1 for c in clients if c["status"] == key)
        out["items"] = [{"id": c["id"], "name": c["name"], "detail": f"owes {len(c['docs_outstanding'])} document"
                         f"{'s' if len(c['docs_outstanding']) != 1 else ''}", "days_left": c.get("days_left")}
                        for c in clients if c["status"] == "waiting_docs" and c["docs_outstanding"]][:5]
    elif industry == "insurance":
        for item in list_items(conn):
            if item["kind"] not in ("coi", "renewal") or not item["open"] or item.get("days_left") is None:
                continue
            limit = 7 if item["kind"] == "coi" else 60
            if item["days_left"] <= limit:
                what = (f"Certificate for {item['data'].get('holder') or item['name']}" if item["kind"] == "coi"
                        else f"{item['data'].get('policy') or 'Policy'} renewal")
                out["items"].append({"id": item["id"], "name": item["name"], "detail": what,
                                     "days_left": item["days_left"], "kind": item["kind"]})
        out["items"].sort(key=lambda i: i["days_left"])
        out["items"] = out["items"][:6]
    return out

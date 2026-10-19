"""
C.A.P. engine — private AI inbox assistant.

Everything here runs on the user's computer:
- Google OAuth (desktop flow) for Gmail + Calendar
- Inbox triage: needs-reply / FYI / skipped, urgency, summary, suggested tasks
- Drafts grounded in the user's Business Profile, knowledge files, saved replies,
  thread history and calendar busy times
- Follow-up radar for sent replies that got no answer
- Local tasks, knowledge files and notes in SQLite
- LLM routing through LiteLLM (local Ollama or the user's own cloud key)
"""

import base64
import html
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from demo_packs import INDUSTRY_PACKS
import industry
import fraud
import tracker

INDUSTRY_KITS = industry.KITS  # alias: some functions take a parameter named `industry`

# LiteLLM downloads a model price list from GitHub every time it loads. C.A.P. doesn't need it, and a private
# app shouldn't make calls it doesn't need, so use the copy bundled with LiteLLM.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

if getattr(sys, "frozen", False):
    os.environ["TIKTOKEN_CACHE_DIR"] = str(Path(sys._MEIPASS) / "data-gym-cache")
    import tiktoken.registry
    import tiktoken_ext.openai_public as tiktoken_openai_public

    tiktoken.registry.ENCODING_CONSTRUCTORS = dict(tiktoken_openai_public.ENCODING_CONSTRUCTORS)

APP_VERSION = "2.2.0"

# ---------------------------------------------------------------------------
# Paths & configuration
# ---------------------------------------------------------------------------

# gmail.modify = read, label (archive), trash and send. v1 only had readonly+send,
# which is why Archive/Delete never actually changed anything in Gmail.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]

APP_DIR = Path(__file__).resolve().parent
BUNDLE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR
def _default_data_dir():
    """Where an installed copy keeps its data: the usual per-user app-data folder on each system."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CAP"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CAP"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "CAP"


if getattr(sys, "frozen", False):
    _configured = os.environ.get("CAP_DATA_DIR")
    DATA_DIR = Path(_configured) if _configured else _default_data_dir()
else:
    DATA_DIR = APP_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)

TOKEN_FILE = str(DATA_DIR / "token.json")
DB_FILE = str(DATA_DIR / "triage_log.db")
_persona_candidates = [DATA_DIR / "persona.json", BUNDLE_DIR / "persona.json",
                       Path(getattr(sys, "_MEIPASS", APP_DIR)) / "persona.json"]
PERSONA_FILE = next((p for p in _persona_candidates if p.exists()), DATA_DIR / "persona.json")

DB_INIT_LOCK = threading.RLock()
INITIALIZED_DATABASES = set()

OLLAMA_MODEL = "ollama/llama3.1"
OLLAMA_API_BASE = "http://localhost:11434"
CONFIDENCE_REVIEW_THRESHOLD = 0.6
MAX_EMAILS_TO_PROCESS = 25
MINUTES_SAVED_PER_REPLY = 5
MINUTES_SAVED_PER_SORTED_EMAIL = 0.5
TONES = ("Professional", "Friendly", "Concise")

OLLAMA_MODEL_PRESETS = [
    {"model": "ollama/qwen3.5:9b", "label": "Qwen 3.5 9B — newest all-rounder, strong at structured replies (12 GB RAM+)"},
    {"model": "ollama/gemma4:12b", "label": "Gemma 4 12B — most natural writing (16 GB RAM+)"},
    {"model": "ollama/granite4.2:8b", "label": "IBM Granite 4.2 8B — built for business assistants (12 GB RAM+)"},
    {"model": "ollama/gemma3:12b", "label": "Gemma 3 12B — natural writing, no thinking delay (16 GB RAM+)"},
    {"model": "ollama/gpt-oss:20b", "label": "OpenAI gpt-oss 20B — highest quality, slowest (24 GB RAM+)"},
    {"model": "ollama/qwen3.5:4b", "label": "Qwen 3.5 4B — for 8 GB RAM PCs"},
    {"model": "ollama/gemma3:4b", "label": "Gemma 3 4B — for 8 GB RAM PCs"},
    {"model": "ollama/llama3.1:8b", "label": "Llama 3.1 8B — older, dependable (8 GB RAM+)"},
    {"model": "ollama/qwen2.5:7b", "label": "Qwen 2.5 7B — older, strong at structured replies"},
    {"model": "ollama/gemma2:9b", "label": "Gemma 2 9B — older, natural writing (12 GB RAM+)"},
    {"model": "ollama/mistral:7b", "label": "Mistral 7B — older, fast"},
    {"model": "ollama/llama3.2:3b", "label": "Llama 3.2 3B — for older/low-memory PCs"},
]

# Local models that "think" before answering. On a PC without a strong GPU that can add a minute or more to
# every draft, so C.A.P. turns thinking off (gpt-oss can't turn it off, so it is set to low).
LOCAL_THINKING_MODEL_PREFIXES = ("qwen3", "deepseek-r1", "granite4.2", "magistral", "phi4-reasoning", "gpt-oss")

CLOUD_MODEL_PRESETS = {
    "OpenAI": [
        {"model": "gpt-4o-mini", "label": "GPT-4o mini (fast, low cost)"},
        {"model": "gpt-4.1-mini", "label": "GPT-4.1 mini"},
        {"model": "gpt-4o", "label": "GPT-4o"},
    ],
    "Anthropic": [
        {"model": "anthropic/claude-haiku-5-5", "label": "Claude Haiku 5.5 (fast)"},
        {"model": "anthropic/claude-sonnet-5-5", "label": "Claude Sonnet 5.5"},
    ],
    "Google Gemini": [
        {"model": "gemini/gemini-2.5-flash", "label": "Gemini 2.5 Flash"},
        {"model": "gemini/gemini-2.5-pro", "label": "Gemini 2.5 Pro"},
    ],
    "Groq": [
        {"model": "groq/llama-3.3-70b-versatile", "label": "Llama 3.3 70B (very fast)"},
    ],
    "OpenRouter": [
        {"model": "openrouter/meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B via OpenRouter"},
    ],
}


def _is_anthropic_model(model):
    m = (model or "").lower()
    return m.startswith("anthropic/") or m.startswith("claude-")


def _complete_with_llm(**kwargs):
    # LiteLLM would otherwise download its model price list from GitHub on import and keep its telemetry flag on;
    # C.A.P. talks only to the AI provider the user picked.
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm  # imported lazily so the app window opens fast

    litellm.telemetry = False

    if _is_anthropic_model(kwargs.get("model")):
        # Claude 4.7 and later reject a non-default temperature with a 400 error; older Claude models
        # work fine without it, so never send it to Anthropic.
        kwargs.pop("temperature", None)
    local_name = (kwargs.get("model") or "").lower().removeprefix("ollama/")
    if (kwargs.get("model") or "").lower().startswith("ollama/") and local_name.startswith(LOCAL_THINKING_MODEL_PREFIXES):
        kwargs.setdefault("reasoning_effort", "low" if local_name.startswith("gpt-oss") else "none")
    if not (kwargs.get("model") or "").startswith("ollama/") and _cloud_redaction_enabled():
        kwargs["messages"], masked = _redact_messages(kwargs.get("messages") or [])
        if masked:
            _audit_detached("Privacy firewall", f"Masked {masked} sensitive number{'s' if masked != 1 else ''} before "
                                                f"sending text to {provider_name(kwargs.get('model'))}.")
    return litellm.completion(**kwargs)


# ---------------------------------------------------------------------------
# Privacy firewall: client identifiers never reach a cloud AI provider
# ---------------------------------------------------------------------------

PROVIDER_NAMES = {"anthropic": "Anthropic", "gemini": "Google (Gemini)", "groq": "Groq", "openrouter": "OpenRouter",
                  "openai": "OpenAI", "mistral": "Mistral", "deepseek": "DeepSeek", "xai": "xAI"}


def provider_name(model):
    model = model or ""
    if model.startswith("ollama/"):
        return "this computer (Ollama)"
    key = model.split("/", 1)[0] if "/" in model else "openai"
    return PROVIDER_NAMES.get(key, key)


def _redact_messages(messages):
    out, total = [], 0
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            content, n = industry.redact_text(content)
            total += n
            message = {**message, "content": content}
        out.append(message)
    return out, total


def _cloud_redaction_enabled():
    try:
        conn = init_db()
        try:
            return get_setting(conn, "cloud_redaction", "1") != "0"
        finally:
            conn.close()
    except Exception:
        return True  # fail closed: mask when unsure


def _now():
    return datetime.now(timezone.utc)


def _iso(dt=None):
    return (dt or _now()).isoformat()


# ---------------------------------------------------------------------------
# Google auth
# ---------------------------------------------------------------------------

def credentials_path():
    bundled = BUNDLE_DIR / "credentials.json"
    return str(bundled if bundled.exists() else DATA_DIR / "credentials.json")


def credentials_present():
    return os.path.exists(credentials_path())


def save_credentials_file(data: bytes):
    """Validates and stores a Google OAuth desktop-client JSON uploaded from the UI."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise ValueError("That file isn't valid JSON. Download the OAuth client JSON again from Google Cloud.") from exc
    section = payload.get("installed")
    if not isinstance(section, dict):
        if isinstance(payload.get("web"), dict):
            raise ValueError("This is a 'Web application' client. Create an OAuth client of type 'Desktop app' instead.")
        raise ValueError("This doesn't look like a Google OAuth client file (missing 'installed' section).")
    if not section.get("client_id") or not section.get("client_secret"):
        raise ValueError("The OAuth client file is missing client_id or client_secret.")
    target = DATA_DIR / "credentials.json"
    target.write_bytes(data)
    return {"saved": True, "client_id_suffix": section["client_id"][-24:]}


def get_google_service(api_name, api_version, force_login=False, interactive=False):
    """Never opens a browser unless the user clicked Connect (interactive=True)."""
    creds = _get_google_credentials(force_login=force_login, allow_interactive=interactive)
    if creds is None:
        raise RuntimeError("Google isn't connected.")
    return build(api_name, api_version, credentials=creds, cache_discovery=False)


def get_gmail_service(force_login=False, interactive=False):
    return get_google_service("gmail", "v1", force_login=force_login, interactive=interactive)


def _get_google_credentials(force_login=False, allow_interactive=True):
    creds = None
    if os.path.exists(TOKEN_FILE) and not force_login:
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            if not set(SCOPES).issubset(set(creds.scopes or [])):
                creds = None  # older token without archive/trash permission -> re-consent
        except Exception:
            creds = None

    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            Path(TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds
    if not allow_interactive:
        return None
    if not credentials_present():
        raise FileNotFoundError(
            "Google sign-in isn't set up yet. Open Settings → Google account and add your "
            "OAuth client file (credentials.json). Or try the sample inbox first."
        )
    flow = InstalledAppFlow.from_client_secrets_file(credentials_path(), SCOPES)
    kwargs = {"access_type": "offline", "open_browser": True, "prompt": "consent"}
    try:
        creds = flow.run_local_server(port=0, **kwargs)
    except OSError:
        creds = flow.run_local_server(port=8080, **kwargs)
    Path(TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
    return creds


def disconnect_google():
    try:
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        return True
    except OSError:
        return False


def check_auth_status():
    """Tests stored credentials without opening a browser."""
    base = {"connected": False, "email": None, "calendar_enabled": False,
            "needs_reconnect": False, "credentials_present": credentials_present()}
    if not os.path.exists(TOKEN_FILE):
        return base
    try:
        creds = _get_google_credentials(allow_interactive=False)
        if creds is None:
            return {**base, "needs_reconnect": True}
        gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        email = gmail.users().getProfile(userId="me").execute().get("emailAddress")
        calendar_enabled = False
        try:
            cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
            cal.events().list(calendarId="primary", maxResults=1).execute()
            calendar_enabled = True
        except Exception:
            pass
        return {**base, "connected": bool(email), "email": email, "calendar_enabled": calendar_enabled}
    except Exception as exc:
        return {**base, "error": str(exc)}


# ---------------------------------------------------------------------------
# Gmail parsing
# ---------------------------------------------------------------------------

def _parse_message(msg):
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    received_at = None
    if msg.get("internalDate"):
        try:
            received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            received_at = None
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId"),
        "message_id": headers.get("message-id"),
        "references": headers.get("references"),
        "sender": headers.get("from", "unknown"),
        "reply_to": headers.get("reply-to"),
        "attachments": _attachment_names(msg.get("payload", {})),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", "(no subject)"),
        "body": clean_email_body(_extract_body(msg.get("payload", {}))),
        "received_at": received_at or _iso(),
        "list_unsubscribe": headers.get("list-unsubscribe"),
        "precedence": headers.get("precedence"),
        "auto_submitted": headers.get("auto-submitted"),
        "label_ids": msg.get("labelIds", []),
    }


def _attachment_names(payload):
    names = []

    def walk(part):
        if part.get("filename"):
            names.append(part["filename"])
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload or {})
    return names[:20]


def _decode_part(body):
    data = (body or {}).get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data.encode() if isinstance(data, str) else data).decode("utf-8", errors="replace")


def _html_to_text(markup):
    markup = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    return re.sub(r"[ \t]+", " ", text)


def _extract_body(payload):
    """Plain text preferred; falls back to HTML (v1 returned '' for HTML-only mail)."""
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            plain.append(_decode_part(part.get("body")))
        elif mime == "text/html":
            rich.append(_decode_part(part.get("body")))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload or {})
    text = next((p for p in plain if p.strip()), "")
    if not text:
        text = next((_html_to_text(h) for h in rich if h.strip()), "")
    return text


_QUOTE_SPLIT = re.compile(
    r"(?im)^[ \t]*(on .{4,200}wrote:\s*$|-{2,}\s*original message\s*-{2,}|from:\s.+\n[ \t]*(sent|date):\s)"
)


def clean_email_body(text, limit=8000):
    """Keeps paragraph breaks, drops quoted history (the thread provides that) and noise."""
    text = (text or "").replace("\r\n", "\n").replace("\x00", " ")
    match = _QUOTE_SPLIT.search(text)
    if match and text[: match.start()].strip():
        text = text[: match.start()]
    lines = [line.rstrip() for line in text.split("\n") if not line.lstrip().startswith(">")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def normalize_text(text):
    return re.sub(r"\s+", " ", (text or "").replace("\x00", " ")).strip()


def sender_address(sender):
    return (parseaddr(sender or "")[1] or "").lower()


def extract_sender_name(sender_str):
    name, addr = parseaddr(sender_str or "")
    if name:
        parts = [p.strip("\"',") for p in name.strip().split() if p.strip("\"',")]
        if parts:
            return parts[0]
    if addr and "@" in addr:
        parts = addr.split("@")[0].replace(".", " ").replace("_", " ").title().split()
        if parts:
            return parts[0]
    return ""


def fetch_email_thread(service, thread_id, current_message_id=None):
    if not service or not thread_id:
        return []
    try:
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    except Exception:
        return []
    messages = [_parse_message(m) for m in thread.get("messages", []) if m.get("id") != current_message_id]
    return messages[-6:]


NO_REPLY_SENDER_PATTERN = re.compile(
    r"no-?reply|do-?not-?reply|notifications?@|newsletter|marketing@|mailer-daemon|"
    r"alerts?@|updates?@|billing@|receipts?@|news@|digest@|postmaster@",
    re.IGNORECASE,
)
NO_REPLY_CONTENT_PATTERN = re.compile(
    r"(thank you for (submitting|your application|applying|your order|your purchase)|"
    r"application (has been )?(received|submitted)|we (have )?received your application|"
    r"your (order|receipt|invoice|payment) (#|number|confirmation)|order confirmation|"
    r"payment (received|confirmation)|this is an automated (message|email)|"
    r"you are receiving this (email|because)|unsubscribe from (this|these))",
    re.IGNORECASE,
)


def looks_automated(email):
    """Cheap filter before any model call, so bulk mail never costs an inference."""
    if email.get("list_unsubscribe"):
        return True
    if (email.get("precedence") or "").lower() in {"bulk", "list", "junk"}:
        return True
    if (email.get("auto_submitted") or "no").lower() not in {"no", ""}:
        return True
    labels = set(email.get("label_ids") or [])
    if labels & {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS", "SPAM"}:
        return True
    if NO_REPLY_SENDER_PATTERN.search(email.get("sender", "")):
        return True
    return bool(NO_REPLY_CONTENT_PATTERN.search(f"{email.get('subject', '')}\n{(email.get('body') or '')[:2000]}"))


def send_reply(service, email, reply_text):
    to_address = parseaddr(email.get("sender", ""))[1]
    if not to_address:
        raise ValueError(f"Could not find a reply address in: {email.get('sender')!r}")
    subject = email.get("subject") or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    message = MIMEText(reply_text, "plain", "utf-8")
    message["To"] = to_address
    message["Subject"] = subject
    if email.get("message_id"):
        message["In-Reply-To"] = email["message_id"]
        refs = (email.get("references") or "").strip()
        message["References"] = f"{refs} {email['message_id']}".strip()
    body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
    if email.get("thread_id"):
        body["threadId"] = email["thread_id"]
    return service.users().messages().send(userId="me", body=body).execute().get("id")


def archive_in_gmail(service, gmail_id):
    service.users().messages().modify(userId="me", id=gmail_id, body={"removeLabelIds": ["INBOX"]}).execute()


def trash_in_gmail(service, gmail_id):
    service.users().messages().trash(userId="me", id=gmail_id).execute()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _configure_database_connection(conn):
    conn.execute("PRAGMA busy_timeout = 30000")
    for _ in range(40):
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.25)
    conn.execute("PRAGMA synchronous = NORMAL")


def _add_columns(conn, table, columns):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _initialize_db(db_path):
    conn = sqlite3.connect(db_path, timeout=30.0)
    _configure_database_connection(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gmail_id TEXT UNIQUE NOT NULL,
            sender TEXT, subject TEXT, needs_reply INTEGER, confidence REAL,
            reason TEXT, draft_reply TEXT, action TEXT NOT NULL, processed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, source TEXT NOT NULL,
            mime_type TEXT, content TEXT NOT NULL, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, content TEXT NOT NULL,
            tags TEXT, category TEXT DEFAULT 'general', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS local_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, notes TEXT, due_at TEXT,
            completed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
            uses INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    _add_columns(conn, "email_log", [
        ("sent_at", "TEXT"), ("received_at", "TEXT"), ("urgency", "TEXT DEFAULT 'INFORMATIONAL'"),
        ("summary", "TEXT"), ("suggested_replies", "TEXT"), ("is_archived", "INTEGER DEFAULT 0"),
        ("thread_id", "TEXT"), ("message_id", "TEXT"), ("references_hdr", "TEXT"), ("body", "TEXT"),
        ("suggested_tasks", "TEXT"), ("followup_at", "TEXT"), ("followup_done", "INTEGER DEFAULT 0"),
        ("is_demo", "INTEGER DEFAULT 0"), ("ai_draft", "TEXT"), ("draft_state", "TEXT"),
        ("snooze_until", "TEXT"), ("sent_similarity", "REAL"), ("reply_to", "TEXT"), ("attachments", "TEXT"),
        ("text_purged_at", "TEXT"), ("masked_at", "TEXT"),
    ])
    conn.execute("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, "
                 "action TEXT NOT NULL, detail TEXT)")
    tracker.ensure_schema(conn)
    _add_columns(conn, "local_tasks", [
        ("priority", "TEXT DEFAULT 'MEDIUM'"), ("source_email", "TEXT"), ("completed_at", "TEXT"),
    ])
    conn.execute("UPDATE email_log SET received_at = processed_at WHERE received_at IS NULL")
    conn.execute("UPDATE email_log SET urgency = 'INFORMATIONAL' WHERE urgency IS NULL")
    # v1 used AUTO-SEND / ESCALATE for "pending draft". There is no auto-send in v2.
    conn.execute(
        "UPDATE email_log SET action = 'DRAFT' WHERE action IN ('AUTO-SEND', 'ESCALATE', 'AUTO-SEND-CONSERVATIVE')"
    )
    conn.execute("UPDATE email_log SET draft_state = CASE WHEN COALESCE(draft_reply, '') = '' THEN 'pending' "
                 "ELSE 'ready' END WHERE action = 'DRAFT' AND draft_state IS NULL")
    conn.execute("UPDATE email_log SET ai_draft = draft_reply WHERE ai_draft IS NULL AND action = 'DRAFT' "
                 "AND sent_at IS NULL AND COALESCE(draft_reply, '') != ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_thread ON email_log(thread_id)")
    conn.commit()
    _migrate_v1_data(conn)
    return conn


def _migrate_v1_data(conn):
    if get_setting(conn, "migrated_v2") == "1":
        return
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    # Reminders become dated tasks (one list instead of two).
    if "reminders" in tables:
        for title, remind_at, notes, completed, created_at in conn.execute(
            "SELECT title, remind_at, notes, completed, created_at FROM reminders"
        ).fetchall():
            conn.execute(
                "INSERT INTO local_tasks (title, notes, due_at, completed, created_at) VALUES (?, ?, ?, ?, ?)",
                (title, notes, remind_at, completed, created_at or _iso()),
            )
    # v1 saved an invented "voice profile" when it had zero real samples; drafts treated it as fact.
    raw = get_setting(conn, "calibrated_voice_profile")
    if raw:
        try:
            if int(json.loads(raw).get("sample_count", 0)) < 3:
                conn.execute("DELETE FROM settings WHERE key = 'calibrated_voice_profile'")
        except Exception:
            conn.execute("DELETE FROM settings WHERE key = 'calibrated_voice_profile'")
    # Writing samples become saved replies.
    if "writing_samples" in tables:
        for title, content, created_at in conn.execute(
            "SELECT title, content, created_at FROM writing_samples"
        ).fetchall():
            conn.execute(
                "INSERT INTO templates (title, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title, content, created_at, created_at),
            )
    conn.execute("DELETE FROM settings WHERE key = 'auto_send_enabled'")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('migrated_v2', '1')")
    conn.commit()


def init_db(db_path=DB_FILE):
    normalized = str(Path(db_path).resolve())
    with DB_INIT_LOCK:
        if normalized not in INITIALIZED_DATABASES:
            for attempt in range(6):
                try:
                    _initialize_db(normalized).close()
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() or attempt == 5:
                        raise
                    time.sleep(0.5 * (attempt + 1))
            INITIALIZED_DATABASES.add(normalized)
    conn = sqlite3.connect(normalized, timeout=30.0)
    _configure_database_connection(conn)
    return conn


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] is not None else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Secrets: OS credential vault (Windows Credential Manager) when available, else the local database
# ---------------------------------------------------------------------------

KEYRING_SERVICE = "CAP Inbox Assistant"


def _keyring():
    try:
        import keyring

        backend = keyring.get_keyring()
        name = (type(backend).__module__ + type(backend).__name__).lower()
        return None if ("fail" in name or "null" in name) else keyring
    except Exception:
        return None


def secret_backend_name():
    if _keyring():
        return "Windows Credential Manager" if sys.platform == "win32" else "your system keychain"
    return "the local database (not encrypted)"


def set_secret(conn, name, value):
    kr = _keyring()
    if kr:
        try:
            if value:
                kr.set_password(KEYRING_SERVICE, name, value)
            else:
                try:
                    kr.delete_password(KEYRING_SERVICE, name)
                except Exception:
                    pass
            conn.execute("DELETE FROM settings WHERE key = ?", (f"secret:{name}",))
            conn.commit()
            return
        except Exception:
            pass
    set_setting(conn, f"secret:{name}", value or "")


def get_secret(conn, name):
    kr = _keyring()
    if kr:
        try:
            value = kr.get_password(KEYRING_SERVICE, name)
            if value:
                return value
        except Exception:
            pass
    return get_setting(conn, f"secret:{name}", "") or ""


# ---------------------------------------------------------------------------
# Business profile (the facts every draft is allowed to use)
# ---------------------------------------------------------------------------

BUSINESS_FIELDS = [
    ("business_name", "Business name"),
    ("about", "What you do / who you serve"),
    ("services_prices", "Services & prices"),
    ("hours_location", "Hours, location, service area"),
    ("policies", "Policies (booking, cancellation, refunds, payment)"),
    ("links", "Booking / payment / website links"),
    ("faq", "Common questions & your answers"),
    ("never_say", "Never promise or say"),
]


def get_business_profile(conn):
    try:
        data = json.loads(get_setting(conn, "business_profile", "{}") or "{}")
    except json.JSONDecodeError:
        data = {}
    return {key: (data.get(key) or "").strip() for key, _ in BUSINESS_FIELDS}


def save_business_profile(conn, values):
    clean = {key: (values.get(key) or "").strip()[:6000] for key, _ in BUSINESS_FIELDS}
    set_setting(conn, "business_profile", json.dumps(clean))
    return clean


def format_business_profile(profile):
    lines = [f"{label}: {profile[key]}" for key, label in BUSINESS_FIELDS if profile.get(key)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Saved replies (templates)
# ---------------------------------------------------------------------------

def list_templates(conn):
    rows = conn.execute(
        "SELECT id, title, body, uses, updated_at FROM templates ORDER BY uses DESC, title COLLATE NOCASE"
    ).fetchall()
    return [{"id": r[0], "title": r[1], "body": r[2], "uses": r[3], "updated_at": r[4]} for r in rows]


def save_template(conn, title, body, template_id=None):
    title, body = (title or "").strip(), (body or "").strip()
    if not title or not body:
        raise ValueError("A saved reply needs a name and text.")
    if template_id:
        conn.execute("UPDATE templates SET title = ?, body = ?, updated_at = ? WHERE id = ?",
                     (title, body, _iso(), template_id))
        conn.commit()
        return template_id
    cur = conn.execute("INSERT INTO templates (title, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
                       (title, body, _iso(), _iso()))
    conn.commit()
    return cur.lastrowid


def delete_template(conn, template_id):
    conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()


def render_template(conn, template_id, gmail_id=None):
    row = conn.execute("SELECT body FROM templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        raise ValueError("Saved reply not found.")
    email = get_email(conn, gmail_id) if gmail_id else None
    profile = get_business_profile(conn)
    values = {
        "first_name": extract_sender_name(email["sender"]) if email else "",
        "subject": (email or {}).get("subject", ""),
        "my_name": get_setting(conn, "user_name", ""),
        "business_name": profile.get("business_name", ""),
        "booking_link": profile.get("links", ""),
    }
    text = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)) or "", row[0])
    text = re.sub(r"(?m)^(Hi|Hello|Hey|Dear)\s+,", r"\1 there,", text)
    conn.execute("UPDATE templates SET uses = uses + 1 WHERE id = ?", (template_id,))
    conn.commit()
    return text


# ---------------------------------------------------------------------------
# Knowledge: files + notes, chunked keyword retrieval with sources
# ---------------------------------------------------------------------------

STOP_WORDS = set(
    "the a an is are was were be been being have has had do does did will would shall should may might can "
    "could must of in to for with on at by from as or and but if not no so up out about into over after re fw "
    "fwd hi hello dear thanks thank you your my me we our this that it its i he she they them their am just also "
    "very here there when what how all each which who whom these those then than more some any other new one "
    "two get please let know see need want like make best regards cheers sent from".split()
)


def _tokens(text):
    return [w for w in re.findall(r"[a-z0-9$€£][a-z0-9$€£'\-]+", (text or "").lower()) if w not in STOP_WORDS]


def chunk_text(text, size=900, overlap=150):
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(". ", start, end), text.rfind("\n", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def search_knowledge(conn, query, k=4):
    """Returns the best-matching passages from files and notes, with their source name."""
    passages = []
    for name, content in conn.execute("SELECT name, content FROM documents ORDER BY added_at DESC LIMIT 200"):
        for chunk in chunk_text(content):
            passages.append((f"File: {name}", chunk))
    for title, content in conn.execute("SELECT title, content FROM notes ORDER BY updated_at DESC LIMIT 300"):
        for chunk in chunk_text(f"{title}\n{content}"):
            passages.append((f"Note: {title}", chunk))
    query_terms = set(_tokens(query))
    if not passages or not query_terms:
        return []
    passage_terms = [set(_tokens(text)) for _, text in passages]
    doc_freq = Counter()
    for terms in passage_terms:
        doc_freq.update(terms & query_terms)
    total = len(passages)
    min_hits = 1 if len(query_terms) <= 2 else (2 if len(query_terms) <= 12 else 3)
    scored = []
    for (source, text), terms in zip(passages, passage_terms):
        hits = terms & query_terms
        if len(hits) < min_hits:
            continue
        score = sum(math.log(1 + total / doc_freq[w]) for w in hits)
        if score < 2.5 and len(hits) < len(query_terms):
            continue  # a couple of common words in common isn't relevance
        scored.append({"source": source, "text": text, "score": round(score, 3)})
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:k]


GOOGLE_EXPORT_MIMES = {}  # Drive import removed in v2; kept name for compatibility.


def add_document(conn, name, source, mime_type, content):
    content = re.sub(r"[ \t]+", " ", (content or "").replace("\x00", " "))
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    if not content:
        raise ValueError("No readable text was found in that file.")
    cur = conn.execute(
        "INSERT INTO documents (name, source, mime_type, content, added_at) VALUES (?, ?, ?, ?, ?)",
        (name, source, mime_type, content, _iso()),
    )
    conn.commit()
    return cur.lastrowid


def list_documents(conn):
    rows = conn.execute(
        "SELECT id, name, source, mime_type, length(content), added_at FROM documents ORDER BY added_at DESC"
    ).fetchall()
    return [{"id": r[0], "name": r[1], "source": r[2], "mime_type": r[3], "characters": r[4], "added_at": r[5]}
            for r in rows]


def delete_document(conn, document_id):
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()


def extract_text_from_bytes(filename, data, mime_type=None):
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json", ".log"} or (mime_type or "").startswith("text/plain"):
        return data.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        return _html_to_text(data.decode("utf-8", errors="replace"))
    if suffix in {".docx", ".pptx", ".xlsx", ".pdf"}:
        try:
            return _extract_with_docling(filename, data)
        except Exception:
            pass
    if suffix == ".docx":
        return _extract_docx_text(data)
    if suffix == ".pptx":
        return _extract_pptx_text(data)
    if suffix == ".xlsx":
        return _extract_xlsx_text(data)
    if suffix == ".pdf":
        return _extract_pdf_text(data)
    raise ValueError(f"{filename}: unsupported file type. Use PDF, Word, Excel, PowerPoint, text or HTML.")


def _extract_with_docling(filename, data):
    from docling.document_converter import DocumentConverter
    from docling_core.types.io import DocumentStream

    source = DocumentStream(name=Path(filename).name, stream=BytesIO(data))
    return DocumentConverter().convert(source).document.export_to_markdown()


def _extract_docx_text(data):
    with zipfile.ZipFile(BytesIO(data)) as docx:
        root = ElementTree.fromstring(docx.read("word/document.xml"))
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for para in root.iter(f"{ns}p"):
        text = "".join(node.text or "" for node in para.iter(f"{ns}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_pptx_text(data):
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    with zipfile.ZipFile(BytesIO(data)) as deck:
        names = sorted(n for n in deck.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        slides = []
        for name in names:
            root = ElementTree.fromstring(deck.read(name))
            text = " ".join(node.text or "" for node in root.iter(f"{ns}t")).strip()
            if text:
                slides.append(text)
    return "\n\n".join(slides)


def _extract_xlsx_text(data):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(BytesIO(data)) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            shared = [" ".join(t.text or "" for t in si.iter(f"{ns}t")) for si in root.iter(f"{ns}si")]
        out = []
        for name in sorted(n for n in book.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")):
            root = ElementTree.fromstring(book.read(name))
            for row in root.iter(f"{ns}row"):
                values = []
                for cell in row.iter(f"{ns}c"):
                    v = next(cell.iter(f"{ns}v"), None)
                    if v is None or v.text is None:
                        continue
                    if cell.get("t") == "s" and v.text.isdigit() and int(v.text) < len(shared):
                        values.append(shared[int(v.text)])
                    else:
                        values.append(v.text)
                if values:
                    out.append(" | ".join(values))
    return "\n".join(out)


def _extract_pdf_text(data):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF support needs the pypdf package.") from exc
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(data)).pages)


def list_notes(conn):
    rows = conn.execute("SELECT id, title, content, updated_at FROM notes ORDER BY updated_at DESC").fetchall()
    return [{"id": r[0], "title": r[1], "content": r[2], "updated_at": r[3]} for r in rows]


def save_note(conn, title, content, note_id=None):
    title, content = (title or "").strip(), (content or "").strip()
    if not title or not content:
        raise ValueError("A note needs a title and some text.")
    if note_id:
        conn.execute("UPDATE notes SET title = ?, content = ?, updated_at = ? WHERE id = ?",
                     (title, content, _iso(), note_id))
        conn.commit()
        return note_id
    cur = conn.execute(
        "INSERT INTO notes (title, content, tags, category, created_at, updated_at) VALUES (?, ?, '', 'general', ?, ?)",
        (title, content, _iso(), _iso()),
    )
    conn.commit()
    return cur.lastrowid


def delete_note(conn, note_id):
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()


ASK_SYSTEM_PROMPT = (
    "You answer questions for a small-business owner using ONLY the numbered sources provided "
    "(their business profile, files and notes). Cite sources inline like [1] or [2]. If the sources "
    "don't contain the answer, say so plainly and suggest what to add to Knowledge. Never use outside "
    "knowledge or guess numbers. Be concise."
)


def answer_question(conn, question):
    question = (question or "").strip()
    if not question:
        raise ValueError("Type a question first.")
    sources = []
    profile_text = format_business_profile(get_business_profile(conn))
    if profile_text:
        sources.append({"source": "Business Profile", "text": profile_text})
    sources.extend(search_knowledge(conn, question, k=6))
    if not sources:
        return {"answer": "There's nothing in Knowledge yet that matches. Fill in your Business Profile or add a "
                          "file (price list, FAQ, policies), then ask again.", "sources": []}
    numbered = "\n\n".join(f"[{i}] {s['source']}\n{s['text'][:1800]}" for i, s in enumerate(sources, 1))
    response = _complete_with_llm(**_model_kwargs(conn, [
        {"role": "system", "content": ASK_SYSTEM_PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nSources:\n{numbered}"},
    ], temperature=0.1, max_tokens=1200))
    answer = response["choices"][0]["message"]["content"].strip()
    return {"answer": answer, "sources": [s["source"] for s in sources]}


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def _normalize_calendar_datetime(val, default_time="00:00:00"):
    if not val:
        return None
    if isinstance(val, datetime):
        return (val if val.tzinfo else val.astimezone()).isoformat()
    s = str(val).strip()
    if len(s) == 10 and s.count("-") == 2:
        s = f"{s}T{default_time}"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid date/time: {val!r}") from None
    return (dt if dt.tzinfo else dt.astimezone()).isoformat()


def list_calendar_events(time_min=None, time_max=None, max_results=50):
    service = get_google_service("calendar", "v3")
    params = {
        "calendarId": "primary",
        "timeMin": _normalize_calendar_datetime(time_min) if time_min else _iso(),
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_max:
        params["timeMax"] = _normalize_calendar_datetime(time_max, "23:59:59")
    return service.events().list(**params).execute().get("items", [])


def create_calendar_event(summary, start_at, end_at=None, description=None):
    start_iso = _normalize_calendar_datetime(start_at, "09:00:00")
    start_dt = datetime.fromisoformat(start_iso)
    end_iso = _normalize_calendar_datetime(end_at, "10:00:00") if end_at else None
    if not end_iso or datetime.fromisoformat(end_iso) <= start_dt:
        end_iso = (start_dt + timedelta(hours=1)).isoformat()
    event = {"summary": summary, "description": description or "",
             "start": {"dateTime": start_iso}, "end": {"dateTime": end_iso}}
    return get_google_service("calendar", "v3").events().insert(calendarId="primary", body=event).execute()


def _busy_times_context(days=7):
    """Busy slots only (no titles) so drafts never propose a time you're already booked."""
    try:
        now = datetime.now().astimezone()
        events = list_calendar_events(now.isoformat(), (now + timedelta(days=days)).isoformat(), max_results=60)
    except Exception:
        return ""
    slots = []
    for ev in events:
        start = (ev.get("start") or {}).get("dateTime")
        end = (ev.get("end") or {}).get("dateTime")
        if not start or not end:
            day = (ev.get("start") or {}).get("date")
            if day:
                slots.append(f"- {day}: all day")
            continue
        s, e = datetime.fromisoformat(start.replace("Z", "+00:00")), datetime.fromisoformat(end.replace("Z", "+00:00"))
        slots.append(f"- {s.strftime('%a %b %d %H:%M')}–{e.strftime('%H:%M')}")
    return "\n".join(slots[:40])


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def create_task(conn, title, notes=None, due_at=None, priority="MEDIUM", source_email=None):
    title = (title or "").strip()
    if not title:
        raise ValueError("A task needs a title.")
    priority = priority if priority in {"HIGH", "MEDIUM", "LOW"} else "MEDIUM"
    cur = conn.execute(
        "INSERT INTO local_tasks (title, notes, due_at, priority, source_email, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (title[:300], notes, due_at or None, priority, source_email, _iso()),
    )
    conn.commit()
    return cur.lastrowid


def list_tasks(conn, include_completed=True):
    sql = "SELECT id, title, notes, due_at, completed, priority, source_email, created_at, completed_at FROM local_tasks"
    if not include_completed:
        sql += " WHERE completed = 0"
    sql += (" ORDER BY completed ASC, CASE WHEN due_at IS NULL OR due_at = '' THEN 1 ELSE 0 END, due_at ASC, "
            "CASE priority WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END, created_at DESC")
    return [
        {"id": r[0], "title": r[1], "notes": r[2] or "", "due_at": r[3] or "", "completed": bool(r[4]),
         "priority": r[5] or "MEDIUM", "source_email": r[6], "created_at": r[7], "completed_at": r[8]}
        for r in conn.execute(sql).fetchall()
    ]


def update_task(conn, task_id, title=None, notes=None, due_at=None, priority=None):
    row = conn.execute("SELECT title, notes, due_at, priority FROM local_tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise ValueError("Task not found.")
    conn.execute(
        "UPDATE local_tasks SET title = ?, notes = ?, due_at = ?, priority = ? WHERE id = ?",
        (title if title is not None else row[0], notes if notes is not None else row[1],
         (due_at or None) if due_at is not None else row[2], priority or row[3], task_id),
    )
    conn.commit()


def set_task_completed(conn, task_id, completed=True):
    conn.execute("UPDATE local_tasks SET completed = ?, completed_at = ? WHERE id = ?",
                 (1 if completed else 0, _iso() if completed else None, task_id))
    conn.commit()


def delete_task(conn, task_id):
    conn.execute("DELETE FROM local_tasks WHERE id = ?", (task_id,))
    conn.commit()


def delete_completed_tasks(conn):
    count = conn.execute("DELETE FROM local_tasks WHERE completed = 1").rowcount
    conn.commit()
    return count


def task_from_email(conn, gmail_id, index=None):
    """Adds the AI-suggested task (or a generic follow-up task) for an email."""
    email = get_email(conn, gmail_id)
    if not email:
        raise ValueError("Email not found.")
    suggestions = email.get("suggested_tasks") or []
    picked = suggestions[index] if index is not None and 0 <= index < len(suggestions) else None
    name = extract_sender_name(email["sender"]) or sender_address(email["sender"])
    title = (picked or {}).get("title") or f"Follow up with {name}: {email['subject']}"
    due = (picked or {}).get("due") or None
    if due and not re.match(r"^\d{4}-\d{2}-\d{2}", due):
        due = None
    return create_task(conn, title, notes=f"From email: {email['subject']} — {email['sender']}",
                       due_at=due, source_email=gmail_id)


# ---------------------------------------------------------------------------
# Email log: inbox views, sending state, follow-ups, time saved
# ---------------------------------------------------------------------------

EMAIL_COLUMNS = ("gmail_id, sender, subject, needs_reply, confidence, reason, draft_reply, action, processed_at, "
                 "received_at, urgency, summary, suggested_replies, is_archived, sent_at, thread_id, message_id, "
                 "references_hdr, body, suggested_tasks, followup_at, followup_done, is_demo, ai_draft, draft_state, "
                 "snooze_until, sent_similarity, reply_to, attachments, text_purged_at")

ACTIVE = "is_archived = 0 AND (snooze_until IS NULL OR snooze_until <= ?)"


def _email_row(r):
    keys = [k.strip() for k in EMAIL_COLUMNS.split(",")]
    d = dict(zip(keys, r))
    for key in ("suggested_replies", "suggested_tasks", "attachments"):
        try:
            d[key] = json.loads(d[key] or "[]")
        except (TypeError, json.JSONDecodeError):
            d[key] = []
    d["references"] = d.pop("references_hdr")
    d["sender_name"] = extract_sender_name(d["sender"])
    d["sender_email"] = sender_address(d["sender"])
    d["confidence"] = d["confidence"] or 0
    d["draft_state"] = d["draft_state"] or ("ready" if d["draft_reply"] else "pending")
    d["review_carefully"] = (d["action"] == "DRAFT" and d["draft_state"] == "ready"
                             and d["confidence"] < CONFIDENCE_REVIEW_THRESHOLD)
    d["is_demo"] = bool(d["is_demo"])
    d["snoozed"] = bool(d["snooze_until"] and d["snooze_until"] > _iso())
    return d


def get_email(conn, gmail_id):
    row = conn.execute(f"SELECT {EMAIL_COLUMNS} FROM email_log WHERE gmail_id = ?", (gmail_id,)).fetchone()
    return _email_row(row) if row else None


def already_processed(conn, gmail_id):
    return conn.execute(
        "SELECT 1 FROM email_log WHERE gmail_id = ? AND action != 'DRAFT_ERROR'", (gmail_id,)
    ).fetchone() is not None


def log_email(conn, email, action, result=None, is_demo=False):
    result = result or {}
    reply = result.get("reply", "") or ""
    draft_state = ("ready" if reply else "pending") if action == "DRAFT" else None
    conn.execute(
        """
        INSERT INTO email_log (gmail_id, sender, subject, needs_reply, confidence, reason, draft_reply, action,
            processed_at, received_at, urgency, summary, suggested_replies, is_archived, thread_id, message_id,
            references_hdr, body, suggested_tasks, is_demo, ai_draft, draft_state, reply_to, attachments)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(gmail_id) DO UPDATE SET
            needs_reply = excluded.needs_reply, confidence = excluded.confidence, reason = excluded.reason,
            draft_reply = excluded.draft_reply, action = excluded.action, processed_at = excluded.processed_at,
            urgency = excluded.urgency, summary = excluded.summary, suggested_replies = excluded.suggested_replies,
            suggested_tasks = excluded.suggested_tasks, body = COALESCE(excluded.body, email_log.body),
            ai_draft = excluded.ai_draft, draft_state = excluded.draft_state,
            reply_to = COALESCE(excluded.reply_to, email_log.reply_to),
            attachments = COALESCE(excluded.attachments, email_log.attachments)
        """,
        (
            email["id"], email.get("sender"), email.get("subject"),
            int(bool(result.get("needs_reply"))) if result else None,
            result.get("confidence"), result.get("reason") or result.get("error"), reply,
            action, _iso(), email.get("received_at") or _iso(), result.get("urgency", "INFORMATIONAL"),
            result.get("summary", ""), json.dumps(result.get("suggested_replies", [])),
            email.get("thread_id"), email.get("message_id"), email.get("references"), email.get("body"),
            json.dumps(result.get("tasks", [])), 1 if is_demo else 0, reply or None, draft_state,
            email.get("reply_to") or None, json.dumps(email["attachments"]) if email.get("attachments") else None,
        ),
    )
    conn.commit()


def list_inbox(conn, view="reply"):
    now = _iso()
    where = {
        "reply": f"action = 'DRAFT' AND sent_at IS NULL AND {ACTIVE}",
        "fyi": f"action IN ('NO_REPLY_NEEDED', 'SKIPPED') AND {ACTIVE} AND COALESCE(received_at, processed_at) >= ?",
        "sent": "sent_at IS NOT NULL",
        "snoozed": "is_archived = 0 AND sent_at IS NULL AND snooze_until > ?",
        "errors": f"action = 'DRAFT_ERROR' AND {ACTIVE}",
    }.get(view)
    if where is None:
        raise ValueError("Unknown inbox view.")
    order = {
        "sent": "sent_at DESC",
        "snoozed": "snooze_until ASC",
        "reply": "CASE urgency WHEN 'URGENT' THEN 1 WHEN 'IMPORTANT' THEN 2 ELSE 3 END, "
                 "COALESCE(received_at, processed_at) DESC",
    }.get(view, "COALESCE(received_at, processed_at) DESC")
    params = {"reply": (now,), "fyi": (now, (_now() - timedelta(days=14)).isoformat()), "sent": (),
              "snoozed": (now,), "errors": (now,)}[view]
    rows = conn.execute(f"SELECT {EMAIL_COLUMNS} FROM email_log WHERE {where} ORDER BY {order} LIMIT 200",
                        params).fetchall()
    return [_email_row(r) for r in rows]


def inbox_counts(conn):
    now = _iso()

    def count(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    return {
        "reply": count(f"SELECT COUNT(*) FROM email_log WHERE action = 'DRAFT' AND sent_at IS NULL AND {ACTIVE}", (now,)),
        "urgent": count(f"SELECT COUNT(*) FROM email_log WHERE action = 'DRAFT' AND sent_at IS NULL AND {ACTIVE} "
                        "AND urgency = 'URGENT'", (now,)),
        "drafting": count(f"SELECT COUNT(*) FROM email_log WHERE action = 'DRAFT' AND sent_at IS NULL AND {ACTIVE} "
                          "AND draft_state = 'pending'", (now,)),
        "fyi": count(f"SELECT COUNT(*) FROM email_log WHERE action IN ('NO_REPLY_NEEDED','SKIPPED') AND {ACTIVE} "
                     "AND COALESCE(received_at, processed_at) >= ?", (now, (_now() - timedelta(days=14)).isoformat())),
        "snoozed": count("SELECT COUNT(*) FROM email_log WHERE is_archived = 0 AND sent_at IS NULL AND snooze_until > ?",
                         (now,)),
        "errors": count(f"SELECT COUNT(*) FROM email_log WHERE action = 'DRAFT_ERROR' AND {ACTIVE}", (now,)),
        "followups": len(list_followups(conn)),
    }


def mark_sent(conn, gmail_id, text, followup_days=None, similarity=None):
    followup_at = (_now() + timedelta(days=followup_days)).isoformat() if followup_days else None
    conn.execute(
        "UPDATE email_log SET sent_at = ?, draft_reply = ?, followup_at = ?, followup_done = 0, sent_similarity = ?, "
        "snooze_until = NULL WHERE gmail_id = ?",
        (_iso(), text, followup_at, similarity, gmail_id),
    )
    conn.commit()


def mark_done(conn, gmail_id):
    conn.execute("UPDATE email_log SET is_archived = 1, snooze_until = NULL WHERE gmail_id = ?", (gmail_id,))
    conn.commit()


def move_to_reply(conn, gmail_id):
    """'This does need a reply' on an FYI item: queue it for drafting."""
    conn.execute("UPDATE email_log SET action = 'DRAFT', needs_reply = 1, draft_state = "
                 "CASE WHEN COALESCE(draft_reply, '') = '' THEN 'pending' ELSE 'ready' END WHERE gmail_id = ?",
                 (gmail_id,))
    conn.commit()


SNOOZE_PRESETS = {"later": "Later today", "tomorrow": "Tomorrow morning", "monday": "Next Monday",
                  "week": "In a week"}


def snooze_time(preset, now=None):
    now = (now or datetime.now().astimezone())
    morning = lambda d: d.replace(hour=8, minute=0, second=0, microsecond=0)  # noqa: E731
    if preset == "later":
        target = now + timedelta(hours=3)
    elif preset == "tomorrow":
        target = morning(now + timedelta(days=1))
    elif preset == "monday":
        days = (7 - now.weekday()) % 7 or 7
        target = morning(now + timedelta(days=days))
    elif preset == "week":
        target = morning(now + timedelta(days=7))
    else:
        target = datetime.fromisoformat(preset)
        target = target if target.tzinfo else target.astimezone()
    return target.astimezone(timezone.utc).isoformat()


def snooze(conn, gmail_id, preset):
    until = snooze_time(preset)
    if until <= _iso():
        raise ValueError("Pick a time in the future.")
    conn.execute("UPDATE email_log SET snooze_until = ? WHERE gmail_id = ?", (until, gmail_id))
    conn.commit()
    return until


def unsnooze(conn, gmail_id):
    conn.execute("UPDATE email_log SET snooze_until = NULL WHERE gmail_id = ?", (gmail_id,))
    conn.commit()


def list_followups(conn, include_upcoming=False):
    """Sent replies that asked for a response and haven't had one yet."""
    rows = conn.execute(
        f"SELECT {EMAIL_COLUMNS} FROM email_log WHERE sent_at IS NOT NULL AND followup_at IS NOT NULL "
        "AND COALESCE(followup_done, 0) = 0 ORDER BY followup_at"
    ).fetchall()
    now = _iso()
    out = []
    for r in rows:
        item = _email_row(r)
        newer = None
        if item["thread_id"]:
            newer = conn.execute(
                "SELECT 1 FROM email_log WHERE thread_id = ? AND gmail_id != ? AND received_at > ? LIMIT 1",
                (item["thread_id"], item["gmail_id"], item["sent_at"]),
            ).fetchone()
        if newer:
            conn.execute("UPDATE email_log SET followup_done = 1 WHERE gmail_id = ?", (item["gmail_id"],))
            conn.commit()
            continue
        if item["followup_at"] > now and not include_upcoming:
            continue
        sent = datetime.fromisoformat(item["sent_at"])
        item["days_waiting"] = max(0, (_now() - sent).days)
        item["due"] = item["followup_at"] <= now
        out.append(item)
    return out


def dismiss_followup(conn, gmail_id):
    conn.execute("UPDATE email_log SET followup_done = 1 WHERE gmail_id = ?", (gmail_id,))
    conn.commit()


def time_saved(conn):
    """Honest, simple estimate. The assumptions are returned so the UI can show them."""
    week_ago = (_now() - timedelta(days=7)).isoformat()
    month_ago = (_now() - timedelta(days=30)).isoformat()

    def count(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    replies_all = count("SELECT COUNT(*) FROM email_log WHERE sent_at IS NOT NULL AND COALESCE(is_demo,0) = 0")
    replies_week = count("SELECT COUNT(*) FROM email_log WHERE sent_at >= ? AND COALESCE(is_demo,0) = 0", (week_ago,))
    sorted_all = count("SELECT COUNT(*) FROM email_log WHERE action IN ('SKIPPED','NO_REPLY_NEEDED') "
                       "AND COALESCE(is_demo,0) = 0")
    sorted_week = count("SELECT COUNT(*) FROM email_log WHERE action IN ('SKIPPED','NO_REPLY_NEEDED') "
                        "AND processed_at >= ? AND COALESCE(is_demo,0) = 0", (week_ago,))
    measured = count("SELECT COUNT(*) FROM email_log WHERE sent_at >= ? AND sent_similarity IS NOT NULL "
                     "AND COALESCE(is_demo,0) = 0", (month_ago,))
    light_edits = count("SELECT COUNT(*) FROM email_log WHERE sent_at >= ? AND sent_similarity >= 0.85 "
                        "AND COALESCE(is_demo,0) = 0", (month_ago,))

    def minutes(replies, sorted_):
        return round(replies * MINUTES_SAVED_PER_REPLY + sorted_ * MINUTES_SAVED_PER_SORTED_EMAIL)

    return {
        "minutes_week": minutes(replies_week, sorted_week),
        "minutes_total": minutes(replies_all, sorted_all),
        "replies_week": replies_week,
        "sorted_week": sorted_week,
        "drafts_measured_30d": measured,
        "drafts_light_edit_30d": light_edits,
        "assumption": f"Estimate: {MINUTES_SAVED_PER_REPLY} min per reply you sent from a draft, "
                      f"{int(MINUTES_SAVED_PER_SORTED_EMAIL * 60)} s per email sorted for you.",
    }


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------

def load_persona_config():
    config = {}
    if PERSONA_FILE.exists():
        try:
            loaded = json.loads(PERSONA_FILE.read_text(encoding="utf-8"))
            config = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            config = {}
    config.setdefault("style_rules", [])
    return config


def calibrate_user_voice(conn, mailbox=None):
    """Learns greeting, sign-off and typical length from real sent mail. Saves nothing if < 3 samples."""
    texts = []
    if mailbox is not None:
        try:
            texts = [t for t in mailbox.sent_samples(40) if len(t) > 30]
        except Exception:
            texts = []
    if len(texts) < 5:
        for (reply,) in conn.execute(
            "SELECT draft_reply FROM email_log WHERE sent_at IS NOT NULL AND LENGTH(draft_reply) > 30 "
            "AND COALESCE(is_demo,0) = 0 ORDER BY sent_at DESC LIMIT 40"
        ):
            texts.append(reply)
    if len(texts) < 3:
        return {"calibrated": False, "sample_count": len(texts),
                "message": "Need at least 3 sent emails to learn your style. Send a few replies first."}

    greetings, signoffs = Counter(), Counter()
    for text in texts:
        first = text.strip().split("\n")[0]
        m = re.match(r"^(Hi|Hey|Hello|Dear|Good morning|Good afternoon)\b", first, re.I)
        if m:
            greetings[m.group(1).title()] += 1
        tail = " ".join([l.strip() for l in text.strip().split("\n") if l.strip()][-3:])
        for so in ("Best regards", "Kind regards", "Warm regards", "Thanks", "Thank you", "Cheers", "Best", "Regards",
                   "Sincerely"):
            if re.search(rf"\b{so}\b", tail, re.I):
                signoffs[so] += 1
                break
    words = [len(t.split()) for t in texts]
    avg = int(sum(words) / len(words))
    profile = {
        "sample_count": len(texts),
        "greeting": greetings.most_common(1)[0][0] if greetings else "",
        "signoff": signoffs.most_common(1)[0][0] if signoffs else "",
        "avg_words": avg,
        "length_label": "short" if avg < 50 else ("medium" if avg < 120 else "detailed"),
        "calibrated_at": _iso(),
    }
    set_setting(conn, "calibrated_voice_profile", json.dumps(profile))
    return {"calibrated": True, **profile}


def get_voice_profile(conn):
    """Returns the learned voice profile (also reads the v1 key names)."""
    raw = get_setting(conn, "calibrated_voice_profile")
    if not raw:
        return None
    try:
        p = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if "preferred_greeting" in p and "greeting" not in p:
        greeting = re.sub(r"\s*\{name\},?", "", p.get("preferred_greeting") or "").strip()
        p["greeting"] = greeting if greeting in {"Hi", "Hey", "Hello", "Dear"} else ""
        p["signoff"] = (p.get("preferred_signoff") or "").rstrip(",")
        p["avg_words"] = p.get("avg_words_per_reply", 0)
        avg = p["avg_words"] or 0
        p["length_label"] = "short" if avg < 50 else ("medium" if avg < 120 else "detailed")
    return p if int(p.get("sample_count", 0) or 0) >= 3 else None


def _voice_context(conn):
    lines = []
    p = get_voice_profile(conn)
    if p:
        try:
            if p.get("greeting"):
                lines.append(f"- Usually opens with: \"{p['greeting']} <first name>,\"")
            if p.get("signoff"):
                lines.append(f"- Usually signs off with: \"{p['signoff']}\"")
            if p.get("avg_words"):
                lines.append(f"- Typical reply length: about {p['avg_words']} words ({p.get('length_label', '')})")
        except (TypeError, ValueError):
            pass
    for (reply,) in conn.execute(
        "SELECT draft_reply FROM email_log WHERE sent_at IS NOT NULL AND LENGTH(draft_reply) > 40 "
        "AND COALESCE(is_demo,0) = 0 ORDER BY sent_at DESC LIMIT 2"
    ):
        lines.append(f"- Example of a reply they sent:\n\"\"\"\n{reply[:500]}\n\"\"\"")
    return "\n".join(lines)


def _relevant_templates(conn, email, k=2):
    query = set(_tokens(f"{email.get('subject', '')} {(email.get('body') or '')[:1500]}"))
    scored = []
    for t in list_templates(conn):
        hits = query & set(_tokens(f"{t['title']} {t['body']}"))
        if len(hits) >= 2:
            scored.append((len(hits), t))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored[:k]]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def normalize_model_name(model):
    m = (model or "").strip()
    if not m:
        return OLLAMA_MODEL
    if m.startswith("claude-"):
        return f"anthropic/{m}"
    if m.startswith("gemini-"):
        return f"gemini/{m}"
    return m


def normalize_api_base(model, api_base):
    base = (api_base or "").strip().rstrip("/")
    if not base:
        return None
    if not (model or "").startswith("ollama/") and any(x in base.lower() for x in ("11434", "localhost", "127.0.0.1")):
        return None  # never send a cloud request to the local Ollama port
    return base


_PROVIDER_ENV = [
    (("gemini",), "GEMINI_API_KEY"), (("anthropic", "claude"), "ANTHROPIC_API_KEY"), (("groq",), "GROQ_API_KEY"),
    (("mistral",), "MISTRAL_API_KEY"), (("deepseek",), "DEEPSEEK_API_KEY"), (("openrouter",), "OPENROUTER_API_KEY"),
    (("xai", "grok"), "XAI_API_KEY"), (("together",), "TOGETHERAI_API_KEY"),
    (("gpt", "o1", "o3", "o4", "openai"), "OPENAI_API_KEY"),
]


def _set_provider_env_key(model, api_key):
    if not api_key:
        return
    lowered = (model or "").lower()
    for markers, env in _PROVIDER_ENV:
        if any(m in lowered for m in markers):
            os.environ[env] = api_key.strip()
            return


def get_model_settings(conn):
    model = normalize_model_name(get_setting(conn, "model", OLLAMA_MODEL))
    api_base = get_setting(conn, "api_base", OLLAMA_API_BASE if model.startswith("ollama/") else None)
    if model.startswith("ollama/") and not api_base:
        api_base = OLLAMA_API_BASE
    api_base = normalize_api_base(model, api_base)
    legacy = (get_setting(conn, "api_key", "") or "").strip()
    if legacy:  # v1/v2.0 kept the key in plain settings; move it to the secret store
        set_secret(conn, "api_key", legacy)
        conn.execute("DELETE FROM settings WHERE key = 'api_key'")
        conn.commit()
    api_key = (get_secret(conn, "api_key") or "").strip() or None
    if api_key:
        _set_provider_env_key(model, api_key)
    return model, api_base, api_key


def _model_kwargs(conn, messages, **extra):
    model, api_base, api_key = get_model_settings(conn)
    kwargs = {"model": model, "messages": messages, **extra}
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
    return kwargs


def list_local_models(api_base=OLLAMA_API_BASE):
    """Installed Ollama models only (v1 also listed a model that wasn't installed)."""
    url = (api_base or OLLAMA_API_BASE).rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CAP"}), timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return [f"ollama/{item['name']}" for item in payload.get("models", [])]
    except Exception:
        return []


def detect_ollama(api_base=OLLAMA_API_BASE):
    url = (api_base or OLLAMA_API_BASE).rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CAP"}), timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        models = [f"ollama/{item['name']}" for item in payload.get("models", [])]
        return {"running": True, "models": models}
    except Exception:
        return {"running": False, "models": []}


MODEL_TEST_TIMEOUT_LOCAL = 180  # first load of a local model into memory can take a minute or two
MODEL_TEST_TIMEOUT_CLOUD = 45


class FriendlyModelError(Exception):
    """An error whose message is already written for the user."""


def total_ram_bytes():
    if sys.platform == "win32":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        return stat.ullTotalPhys if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)) else None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def local_model_size(model, api_base=OLLAMA_API_BASE):
    """Download size in bytes of an installed Ollama model, or None if unknown."""
    name = normalize_model_name(model).removeprefix("ollama/")
    wanted = {name, name.removesuffix(":latest"), f"{name}:latest"}
    url = (api_base or OLLAMA_API_BASE).rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "CAP"}), timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    return next((item.get("size") for item in payload.get("models", []) if item.get("name") in wanted), None)


def check_local_model_fits(model, api_base=OLLAMA_API_BASE):
    """Refuse models that can't fit in RAM: Ollama would page to disk and never answer."""
    size, ram = local_model_size(model, api_base), total_ram_bytes()
    if size and ram and size > ram * 0.75:
        name = normalize_model_name(model).removeprefix("ollama/")
        raise FriendlyModelError(
            f"'{name}' needs about {size / 1e9:.0f} GB of memory, but this PC has {ram / 2**30:.0f} GB. "
            f"Pick a smaller model (7–9B, such as llama3.1:8b, qwen2.5:7b or gemma2:9b).")


def test_model_connection(model, api_base=None, api_key=None):
    model = normalize_model_name(model)
    local = model.startswith("ollama/")
    if local:
        check_local_model_fits(model, api_base)
    kwargs = {"model": model, "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
              "max_tokens": 10, "temperature": 0.0,
              "timeout": MODEL_TEST_TIMEOUT_LOCAL if local else MODEL_TEST_TIMEOUT_CLOUD}
    base = normalize_api_base(model, api_base)
    if base:
        kwargs["api_base"] = base
    if api_key:
        kwargs["api_key"] = api_key.strip()
        _set_provider_env_key(model, api_key)
    started = time.time()
    response = _complete_with_llm(**kwargs)
    return {"status": "ok", "reply": response["choices"][0]["message"]["content"].strip(),
            "latency_ms": int((time.time() - started) * 1000)}


def friendly_model_error(exc, model, api_base=None):
    if isinstance(exc, FriendlyModelError):
        return str(exc)
    message, lowered = str(exc), str(exc).lower()
    name = (model or "").removeprefix("ollama/")
    timed_out = type(exc).__name__ in ("Timeout", "APITimeoutError", "TimeoutError", "ReadTimeout")
    if (model or "").startswith("ollama/"):
        if "not found" in lowered and "model" in lowered:
            return f"The Ollama model '{name}' isn't installed. Open a terminal and run: ollama pull {name}"
        if timed_out:
            return (f"'{name}' didn't answer within {MODEL_TEST_TIMEOUT_LOCAL // 60} minutes. It's probably too large "
                    f"for this PC. Try a smaller model such as llama3.2:3b or llama3.1:8b.")
        if any(m in lowered for m in ("connection refused", "failed to connect", "connection error", "timed out",
                                       "timeout", "cannot connect", "connecterror")):
            return f"Couldn't reach Ollama at {(api_base or OLLAMA_API_BASE)}. Start the Ollama app and try again."
    if any(m in lowered for m in ("insufficient_quota", "credit_balance", "exceeded your current quota", "billing")):
        return f"Your account for '{model}' is out of credits. Add credits with your AI provider and retry."
    if any(m in lowered for m in ("authenticationerror", "invalid api key", "invalid_api_key", "incorrect api key",
                                   "unauthorized", "api key not valid", "401")):
        return f"The API key for '{model}' was rejected. Check it in Settings → AI engine."
    if "api_key" in lowered and any(m in lowered for m in ("missing", "not provided", "provide an api key")):
        return f"'{model}' needs an API key. Add it in Settings → AI engine."
    if any(m in lowered for m in ("ratelimit", "rate limit", "too many requests", "429")):
        return f"'{model}' is rate-limited right now. Wait a minute and retry."
    if any(m in lowered for m in ("model_not_found", "model not found", "does not exist", "unknown model",
                                   "not_found_error", "is not found", "notfounderror")):
        return f"Model '{model}' wasn't found for your account. Pick another model in Settings."
    if "invalid triage response" in lowered:
        return "The model didn't return the expected format. Try a larger model in Settings."
    cleaned = re.sub(r"^(litellm\.)?(exceptions\.)?([A-Za-z0-9_]+Error:\s*)+", "", message).strip()
    return f"AI request failed ({model}): {cleaned[:300]}"


def is_fatal_model_error(message):
    lowered = (message or "").lower()
    return any(m in lowered for m in ("couldn't reach ollama", "isn't installed", "out of credits", "was rejected",
                                       "needs an api key", "wasn't found for your account"))


# ---------------------------------------------------------------------------
# Drafting
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are C.A.P., an email assistant for a busy professional or small-business owner. You triage each incoming email and, when a reply is needed, draft it on the user's behalf for them to review.

1. Decide needs_reply first.
   - true: the latest message asks a question, makes a request, needs a decision, asks to schedule, complains, or is a real person writing to the user personally.
   - false: receipts, newsletters, marketing, automated notifications, FYI updates, and confirmations of things already done.

2. urgency: "URGENT" (deadline within ~48h, something blocked, unhappy customer, money at risk), "IMPORTANT" (needs action soon, customer or business inquiry), otherwise "INFORMATIONAL".

3. Write the reply (only when needs_reply is true):
   - Greet the sender by first name when known.
   - Answer every question and request in the LATEST message. If the latest message changes something said earlier in the thread, the latest message wins.
   - Use facts ONLY from the email/thread, the Business Profile, Knowledge excerpts and the user's instructions. Never invent prices, availability, dates, attachments, policies, discounts or promises. If a needed fact is missing, ask one focused question or say you will confirm shortly.
   - Do not propose meeting times that overlap the Busy times list, and never mention what those busy events are.
   - Never use placeholders like [Your Company], [date] or <name>.
   - Match the requested tone and the user's voice. Plain text only, no markdown. End with the provided sign-off exactly once.

4. confidence (0.0-1.0) = how safe the draft is to send after a quick glance. Use below 0.5 for legal, refunds, discounts, complaints, negotiations, or when key facts were missing.

5. tasks: concrete things the USER must do because of this email (not "reply to email"). Empty list if none.

Respond with ONLY this JSON object:
{
  "needs_reply": true or false,
  "urgency": "URGENT" | "IMPORTANT" | "INFORMATIONAL",
  "summary": "<one sentence: who wants what>",
  "suggested_replies": ["<short one-line reply option>", "<different option>", "<different option>"],
  "reply": "<full reply text, or empty string if needs_reply is false>",
  "confidence": <number 0.0-1.0>,
  "reason": "<one short sentence explaining the triage>",
  "tasks": [{"title": "<verb-first action>", "due": "<YYYY-MM-DD or empty>"}]
}"""

TONE_GUIDE = {
    "Professional": "Polished, courteous, clear and well-structured.",
    "Friendly": "Warm and conversational while staying clear and helpful.",
    "Concise": "Very brief: 2-4 short sentences, no filler.",
}


def _sign_off(conn):
    signature = (get_setting(conn, "user_signature", "") or "").strip()
    if signature:
        return signature
    name = (get_setting(conn, "user_name", "") or "").strip()
    return f"Best regards,\n{name}" if name else "Best regards"


def _related_history(conn, email, limit=3):
    address = sender_address(email.get("sender"))
    if not address:
        return []
    rows = conn.execute(
        "SELECT subject, summary, draft_reply, sent_at, received_at FROM email_log "
        "WHERE gmail_id != ? AND sender LIKE ? AND COALESCE(thread_id,'') != COALESCE(?, '#') "
        "ORDER BY COALESCE(received_at, processed_at) DESC LIMIT ?",
        (email.get("id", ""), f"%{address}%", email.get("thread_id"), limit),
    ).fetchall()
    return [{"subject": r[0], "summary": r[1] or "", "reply": r[2] if r[3] else "", "date": (r[4] or "")[:10]}
            for r in rows]


def build_draft_messages(conn, email, tone="Professional", instruction=None, thread_messages=None,
                         include_calendar=True, force_reply=False):
    """Assembles exactly what the model sees. Separate from the call so it can be tested."""
    tone = tone if tone in TONE_GUIDE else "Professional"
    sections = [f"Today's date: {datetime.now().astimezone().strftime('%A %Y-%m-%d')}"]
    user_name = (get_setting(conn, "user_name", "") or "").strip()
    if user_name:
        sections.append(f"You are writing as: {user_name}")

    kit = industry.KITS.get(get_industry(conn))
    if kit:
        sections.append(f"INDUSTRY GUIDANCE ({kit['label']}):\n{kit['guidance']}")

    profile_text = format_business_profile(get_business_profile(conn))
    if profile_text:
        sections.append(f"BUSINESS PROFILE (facts you may use):\n{profile_text}")

    query = f"{email.get('subject', '')}\n{(email.get('body') or '')[:2500]}"
    knowledge = search_knowledge(conn, query, k=4)
    if knowledge:
        sections.append("KNOWLEDGE EXCERPTS (facts you may use):\n" + "\n---\n".join(
            f"[{k['source']}]\n{k['text']}" for k in knowledge))

    templates = _relevant_templates(conn, email)
    if templates:
        sections.append("SAVED REPLIES the user wrote for similar emails (reuse their wording and facts where they fit):\n"
                        + "\n---\n".join(f"{t['title']}:\n{t['body'][:1200]}" for t in templates))

    voice = _voice_context(conn)
    if voice:
        sections.append(f"USER'S WRITING VOICE:\n{voice}")
    rules = load_persona_config().get("style_rules", [])
    if rules:
        sections.append("STYLE RULES:\n" + "\n".join(f"- {r}" for r in rules))

    if include_calendar:
        busy = _busy_times_context()
        if busy:
            sections.append(f"BUSY TIMES (next 7 days, local time):\n{busy}")

    history = _related_history(conn, email)
    if history:
        sections.append("EARLIER CONVERSATIONS WITH THIS SENDER (background only):\n" + "\n".join(
            f"- {h['date']} \"{h['subject']}\": {h['summary']}" + (f" | You replied: {h['reply'][:300]}" if h["reply"] else "")
            for h in history))

    thread_messages = thread_messages or []
    if thread_messages:
        sections.append("EARLIER MESSAGES IN THIS THREAD (oldest first):\n" + "\n---\n".join(
            f"From: {m.get('sender', '')}\n{(m.get('body') or '')[:1500]}" for m in thread_messages))

    sections.append(
        "LATEST MESSAGE (reply to this one):\n"
        f"From: {email.get('sender', '')}\nSubject: {email.get('subject', '')}\n"
        f"Received: {email.get('received_at', '')}\n\n{(email.get('body') or '')[:6000] or '(empty body)'}"
    )
    warning = fraud.prompt_warning(fraud_check(conn, email))
    if warning:
        sections.append(warning)
    sensitive = industry.find_sensitive(email.get("body"))
    if sensitive:
        sections.append(
            f"SENSITIVE DATA WARNING: the latest message contains {industry.describe_sensitive(sensitive)}. Never "
            "repeat, quote or confirm these numbers in the reply. If the information was needed, thank the sender and "
            "ask them to use the secure method in the Business Profile (or a phone call) instead of email.")
    sections.append(f"Sender first name: {extract_sender_name(email.get('sender', '')) or 'unknown'}")
    sections.append(f"Tone: {tone} — {TONE_GUIDE[tone]}")
    if instruction:
        sections.append(f"USER INSTRUCTION FOR THIS REPLY (follow it): {instruction.strip()[:1000]}")
    if force_reply:
        sections.append("The user has decided to reply to this email: set needs_reply to true and write the reply.")
    sections.append(f"Sign-off to end with:\n{_sign_off(conn)}")
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(sections)}]


def _parse_model_json(raw):
    cleaned = re.sub(r"```(?:json)?", "", raw or "").strip()
    data = None
    for candidate in (cleaned, (re.search(r"\{[\s\S]*\}", cleaned) or [None])[0]):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            break
        except (json.JSONDecodeError, TypeError):
            continue
    if not isinstance(data, dict):
        raise ValueError("The model returned an invalid triage response.")
    needs_reply = data.get("needs_reply") is True or str(data.get("needs_reply")).lower() == "true"
    urgency = str(data.get("urgency", "INFORMATIONAL")).upper()
    if urgency not in {"URGENT", "IMPORTANT", "INFORMATIONAL"}:
        urgency = "INFORMATIONAL"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    suggestions = [str(s).strip() for s in (data.get("suggested_replies") or []) if str(s).strip()][:3]
    tasks = []
    for t in data.get("tasks") or []:
        if isinstance(t, dict) and str(t.get("title", "")).strip():
            tasks.append({"title": str(t["title"]).strip()[:200], "due": str(t.get("due") or "")[:10]})
    any_reply = re.sub(r"\[(your|company|date|name)[^\]]*\]", "", str(data.get("reply") or "").strip(), flags=re.I)
    reply = any_reply if needs_reply else ""
    return {"needs_reply": needs_reply, "_any_reply": any_reply, "urgency": urgency, "summary": str(data.get("summary", ""))[:400],
            "suggested_replies": suggestions if needs_reply else [], "reply": reply, "confidence": confidence,
            "reason": str(data.get("reason", ""))[:300], "tasks": tasks[:5]}


def _quality_pass(conn, result, messages):
    """Optional second pass. Off by default: it doubles the time per email on local models."""
    review = messages + [
        {"role": "assistant", "content": json.dumps(result)},
        {"role": "user", "content": (
            "Review your draft against the LATEST MESSAGE. Fix: any unanswered question, any fact not supported by "
            "the sources, generic filler, missing next step, wrong sign-off. Return ONLY the improved reply text.")},
    ]
    try:
        response = _complete_with_llm(**_model_kwargs(conn, review, temperature=0.15, max_tokens=1500))
        refined = response["choices"][0]["message"]["content"].strip()
        if len(refined) > 20 and not refined.lower().startswith(("i cannot", "i'm sorry", "{")):
            result["reply"] = refined
    except Exception:
        pass
    return result


def draft_reply(conn, email, tone=None, instruction=None, thread_messages=None, include_calendar=True,
                force_reply=False):
    tone = tone or get_setting(conn, "default_tone", "Professional")
    messages = build_draft_messages(conn, email, tone, instruction, thread_messages, include_calendar, force_reply)
    model, api_base, _ = get_model_settings(conn)
    try:
        response = _complete_with_llm(**_model_kwargs(conn, messages, temperature=0.25, max_tokens=1800))
        result = _parse_model_json(response["choices"][0]["message"]["content"])
        if result["needs_reply"] and get_setting(conn, "quality_pass", "0") == "1":
            result = _quality_pass(conn, result, messages)
        return result
    except Exception as exc:
        error = friendly_model_error(exc, model, api_base)
        return {"needs_reply": False, "urgency": "IMPORTANT", "summary": email.get("subject", ""),
                "suggested_replies": [], "reply": "", "confidence": 0.0, "reason": error, "error": error, "tasks": []}


class DraftBusy(Exception):
    """Another worker is already writing this draft."""


_DRAFTING = set()
_DRAFTING_LOCK = threading.Lock()


def is_drafting(gmail_id):
    return gmail_id in _DRAFTING


def local_thread(conn, email_item):
    """Earlier messages in the same conversation from the local log, plus the user's own sent replies."""
    thread_id = email_item.get("thread_id")
    if not thread_id:
        return []
    rows = conn.execute(
        "SELECT sender, body, draft_reply, sent_at, received_at FROM email_log WHERE thread_id = ? AND gmail_id != ? "
        "ORDER BY COALESCE(received_at, processed_at)", (thread_id, email_item.get("id", "")),
    ).fetchall()
    out = []
    for sender, body, reply, sent_at, received_at in rows:
        if body:
            out.append({"sender": sender, "body": body, "received_at": received_at})
        if sent_at and reply:
            out.append({"sender": "You (sent)", "body": reply, "received_at": sent_at})
    return out[-6:]


def _email_for_drafting(conn, gmail_id, mailbox=None):
    stored = get_email(conn, gmail_id)
    if not stored:
        raise ValueError("Email not found.")
    email = {"id": gmail_id, "sender": stored["sender"], "subject": stored["subject"], "body": stored["body"] or "",
             "thread_id": stored["thread_id"], "message_id": stored["message_id"],
             "references": stored["references"], "received_at": stored["received_at"]}
    thread = []
    if mailbox is not None and not stored["is_demo"]:
        if (not email["body"] or not email["message_id"]) and hasattr(mailbox, "refresh"):
            try:
                fresh = mailbox.refresh(email)
                email.update({k: fresh[k] for k in ("body", "thread_id", "message_id", "references")})
                conn.execute("UPDATE email_log SET body = ?, thread_id = ?, message_id = ?, references_hdr = ? "
                             "WHERE gmail_id = ?", (fresh["body"], fresh["thread_id"], fresh["message_id"],
                                                    fresh["references"], gmail_id))
                conn.commit()
            except Exception:
                pass
        try:
            thread = mailbox.fetch_thread(conn, email)
        except Exception:
            thread = local_thread(conn, email)
    else:
        thread = local_thread(conn, email)
    if not email["body"]:
        email["body"] = stored["summary"] or stored["subject"] or ""
    return email, thread


def google_calendar_ready():
    return os.path.exists(TOKEN_FILE)


def generate_draft(conn, gmail_id, mailbox=None, tone=None, instruction=None):
    """Writes or rewrites the draft for one email (background queue, open-to-draft, and Rewrite all use this)."""
    with _DRAFTING_LOCK:
        if gmail_id in _DRAFTING:
            raise DraftBusy(gmail_id)
        _DRAFTING.add(gmail_id)
    try:
        email, thread = _email_for_drafting(conn, gmail_id, mailbox)
        stored = get_email(conn, gmail_id)
        tone = tone or get_setting(conn, "default_tone", "Professional")
        result = draft_reply(conn, email, tone, instruction, thread,
                             include_calendar=google_calendar_ready() and not stored["is_demo"], force_reply=True)
        if not result.get("error"):
            result["reply"] = result["reply"] or result.get("_any_reply", "")
            if not result["reply"]:
                result["error"] = "The AI didn't return a reply this time. Try again, or pick a larger model in Settings."
        if result.get("error"):
            if stored["draft_state"] != "ready":
                conn.execute("UPDATE email_log SET draft_state = 'failed', reason = ? WHERE gmail_id = ?",
                             (result["error"], gmail_id))
                conn.commit()
            raise RuntimeError(result["error"])
        conn.execute(
            "UPDATE email_log SET draft_reply = ?, ai_draft = ?, draft_state = 'ready', reason = ?, confidence = ?, "
            "urgency = ?, summary = ?, suggested_replies = ?, suggested_tasks = ?, action = 'DRAFT', needs_reply = 1 "
            "WHERE gmail_id = ?",
            (result["reply"], result["reply"], result["reason"], result["confidence"],
             stored["urgency"] if stored["urgency"] and stored["summary"] else result["urgency"],
             stored["summary"] or result["summary"], json.dumps(result["suggested_replies"]),
             json.dumps(result["tasks"] or stored["suggested_tasks"]), gmail_id),
        )
        conn.commit()
        return get_email(conn, gmail_id)
    finally:
        with _DRAFTING_LOCK:
            _DRAFTING.discard(gmail_id)


def regenerate_draft(conn, gmail_id, tone="Professional", instruction=None, mailbox=None):
    return generate_draft(conn, gmail_id, mailbox, tone, instruction)


def draft_followup(conn, gmail_id):
    item = get_email(conn, gmail_id)
    if not item or not item["sent_at"]:
        raise ValueError("That reply hasn't been sent yet.")
    name = item["sender_name"] or "there"
    fallback = (f"Hi {name},\n\nJust following up on my note below about \"{item['subject']}\". "
                f"Let me know if you have any questions or if there's anything else you need from me.\n\n{_sign_off(conn)}")
    messages = [
        {"role": "system", "content": "You write short, polite follow-up emails. Plain text only. 2-4 sentences plus "
                                      "the sign-off. No guilt-tripping, no invented facts."},
        {"role": "user", "content": f"I emailed {item['sender']} about \"{item['subject']}\" "
                                    f"{item.get('days_waiting', '')} days ago and got no answer.\n\nMy message was:\n"
                                    f"{(item['draft_reply'] or '')[:1500]}\n\nWrite a gentle nudge to {name}. "
                                    f"End with this sign-off exactly:\n{_sign_off(conn)}"},
    ]
    try:
        response = _complete_with_llm(**_model_kwargs(conn, messages, temperature=0.3, max_tokens=400))
        text = response["choices"][0]["message"]["content"].strip()
        return text if len(text) > 20 else fallback
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Inbox check: a fast sorting pass first, then drafts (urgent first) or drafts on open
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """You sort incoming email for a busy professional or small-business owner. Decide quickly. Do NOT write a reply.
- needs_reply: true if the latest message asks a question, makes a request, needs a decision, asks to schedule, complains, or is a real person writing to the user personally. false for receipts, newsletters, marketing, automated notifications, FYI updates and confirmations of things already done.
- urgency: "URGENT" (deadline within ~48h, something blocked, unhappy customer, money at risk), "IMPORTANT" (needs action soon, customer or business inquiry), otherwise "INFORMATIONAL".
- tasks: concrete things the USER must do because of this email (not "reply"). Empty list if none.
Respond with ONLY this JSON:
{"needs_reply": true or false, "urgency": "...", "summary": "<one sentence: who wants what>", "reason": "<short>", "tasks": [{"title": "<verb-first action>", "due": "<YYYY-MM-DD or empty>"}]}"""


def classify_email(conn, email):
    messages = [
        {"role": "system", "content": CLASSIFY_PROMPT},
        {"role": "user", "content": f"Today: {datetime.now().astimezone().strftime('%A %Y-%m-%d')}\n"
                                    + (f"Business type: {industry.KITS[get_industry(conn)]['sort_guidance']}\n"
                                       if get_industry(conn) else "")
                                    + f"From: {email.get('sender', '')}\nSubject: {email.get('subject', '')}\n\n"
                                    f"{(email.get('body') or '')[:3000] or '(empty body)'}"},
    ]
    model, api_base, _ = get_model_settings(conn)
    try:
        response = _complete_with_llm(**_model_kwargs(conn, messages, temperature=0.1, max_tokens=350))
        result = _parse_model_json(response["choices"][0]["message"]["content"])
        result.update({"reply": "", "suggested_replies": [], "confidence": None})
        risk = fraud_check(conn, email)
        if risk["level"] == "high":
            result["urgency"] = "URGENT"
            result["reason"] = "Possible fraud: " + risk["signals"][0]["label"]
        return result
    except Exception as exc:
        error = friendly_model_error(exc, model, api_base)
        return {"needs_reply": False, "urgency": "IMPORTANT", "summary": email.get("subject", ""), "reply": "",
                "suggested_replies": [], "confidence": 0.0, "reason": error, "error": error, "tasks": []}


def pending_drafts(conn, limit=25):
    rows = conn.execute(
        "SELECT gmail_id, subject FROM email_log WHERE action = 'DRAFT' AND draft_state = 'pending' "
        "AND sent_at IS NULL AND is_archived = 0 AND COALESCE(is_demo, 0) = 0 "
        "ORDER BY CASE urgency WHEN 'URGENT' THEN 1 WHEN 'IMPORTANT' THEN 2 ELSE 3 END, "
        "COALESCE(received_at, processed_at) DESC LIMIT ?", (limit,),
    ).fetchall()
    return [{"gmail_id": r[0], "subject": r[1]} for r in rows]


def run_triage(conn, mailbox, max_results=MAX_EMAILS_TO_PROCESS, on_progress=None, draft_mode=None):
    """Phase 1 sorts every new email with a short AI call (seconds each). Phase 2 writes full drafts,
    urgent first, unless the user chose to draft only when an email is opened."""
    progress = on_progress or (lambda *a: None)
    draft_mode = draft_mode or get_setting(conn, "draft_mode", "background")
    progress("fetching", 0, 0, "Fetching new mail…")
    new = mailbox.fetch_recent(max_results, is_known=lambda gid: already_processed(conn, gid))
    stats = {"new": len(new), "needs_reply": 0, "drafted": 0, "fyi": 0, "skipped": 0, "errors": [],
             "stopped_early": False}
    for index, email in enumerate(new):
        progress("sorting", index, len(new), email.get("subject") or "(no subject)")
        if looks_automated(email):
            log_email(conn, email, "SKIPPED", {"summary": "", "reason": "Automated or bulk email"})
            stats["skipped"] += 1
            continue
        result = classify_email(conn, email)
        if result.get("error"):
            log_email(conn, email, "DRAFT_ERROR", result)
            stats["errors"].append({"subject": email.get("subject"), "message": result["error"]})
            if is_fatal_model_error(result["error"]):
                stats["stopped_early"] = True
                return stats
            continue
        if result["needs_reply"]:
            log_email(conn, email, "DRAFT", result)
            stats["needs_reply"] += 1
        else:
            log_email(conn, email, "NO_REPLY_NEEDED", result)
            stats["fyi"] += 1
    if draft_mode == "background":
        queue = pending_drafts(conn)
        for index, item in enumerate(queue):
            progress("drafting", index, len(queue), item["subject"] or "(no subject)")
            try:
                generate_draft(conn, item["gmail_id"], mailbox)
                stats["drafted"] += 1
            except DraftBusy:
                continue
            except (RuntimeError, ValueError) as exc:
                stats["errors"].append({"subject": item["subject"], "message": str(exc)})
                if is_fatal_model_error(str(exc)):
                    stats["stopped_early"] = True
                    break
    progress("done", 0, 0, "")
    return stats


# ---------------------------------------------------------------------------
# Demo inbox (see the value before connecting anything)
# ---------------------------------------------------------------------------

DEMO_EMAILS = [
    {
        "id": "demo-quote", "reason": "Pricing question answered from your profile.", "sender": "Maria Lopez <maria.lopez@example.com>",
        "subject": "Quote for move-out cleaning next Friday?", "urgency": "URGENT", "confidence": 0.82,
        "body": "Hi there,\n\nI'm moving out of a 2-bedroom apartment and need a deep clean before my landlord's "
                "inspection next Saturday. Could you do Friday afternoon? How much would that cost, and do you "
                "bring your own supplies?\n\nThanks,\nMaria",
        "summary": "Maria needs a move-out deep clean of a 2-bed apartment, ideally Friday afternoon, and wants a price.",
        "suggested_replies": ["Yes, Friday afternoon works — I'll send a quote today.",
                              "Friday is booked; could Thursday work instead?",
                              "Could you share the square footage so I can quote accurately?"],
        "reply": "Hi Maria,\n\nThanks for reaching out — happy to help you get the apartment inspection-ready.\n\n"
                 "A move-out deep clean for a 2-bedroom is $240, and yes, we bring all our own supplies and "
                 "equipment. Friday afternoon works: we can start at 1:00 PM and are usually done in about 4 hours.\n\n"
                 "If that suits you, just reply to confirm and I'll send the booking link. A 25% deposit holds the slot.\n\n"
                 "Best regards,\nSam\nBrightside Home Cleaning",
        "tasks": [{"title": "Hold Friday 1–5 PM for Maria's move-out clean", "due": ""}],
    },
    {
        "id": "demo-complaint", "reason": "Complaint: check the re-clean offer matches your guarantee before sending.", "sender": "Derek Shaw <derek.shaw@example.com>",
        "subject": "Bathroom wasn't finished yesterday", "urgency": "URGENT", "confidence": 0.45,
        "body": "Hello,\n\nYour team came yesterday but the upstairs bathroom was skipped entirely. I paid for the "
                "full house. What are you going to do about this?\n\nDerek",
        "summary": "Derek says the upstairs bathroom was skipped during yesterday's full-house clean.",
        "suggested_replies": ["I'm sorry — we'll come back this week to finish it at no charge.",
                              "Could you send a photo so I can check with the team?",
                              "Can I call you this afternoon to sort it out?"],
        "reply": "Hi Derek,\n\nI'm sorry about this — you paid for the full house and the upstairs bathroom should "
                 "have been done. That's on us.\n\nUnder our satisfaction guarantee we'll come back and finish it at "
                 "no charge. Would tomorrow or Thursday morning suit you better?\n\n"
                 "Best regards,\nSam\nBrightside Home Cleaning",
        "tasks": [{"title": "Schedule free re-clean of Derek's upstairs bathroom", "due": ""}],
    },
    {
        "id": "demo-question", "reason": "Simple question answered from your profile.", "sender": "Priya Nair <priya@example.com>",
        "subject": "Do you clean ovens?", "urgency": "IMPORTANT", "confidence": 0.9,
        "body": "Hi, quick question — is inside-the-oven cleaning included in your standard clean, or is that "
                "extra? Also, are you insured?\n\nPriya",
        "summary": "Priya asks whether oven cleaning is included and whether the business is insured.",
        "suggested_replies": ["Oven cleaning is a $35 add-on, and yes, we're fully insured.",
                              "It's included in deep cleans; standard cleans can add it.",
                              "Happy to include it on your next visit — want me to add it?"],
        "reply": "Hi Priya,\n\nGreat questions. Inside-the-oven cleaning isn't part of the standard clean, but you "
                 "can add it for $35. And yes — we're fully insured and bonded.\n\nWould you like me to add it to "
                 "your next booking?\n\nBest regards,\nSam\nBrightside Home Cleaning",
        "tasks": [],
    },
    {
        "id": "demo-reschedule", "reason": "Reschedule request; confirm the new time is free.", "sender": "Tom Becker <tom.becker@example.com>",
        "subject": "Re: Tuesday cleaning", "urgency": "IMPORTANT", "confidence": 0.78,
        "body": "Hi Sam,\n\nSorry for the change — can we move Tuesday's 10am clean to Wednesday? Any time after 12 "
                "is fine.\n\nTom",
        "summary": "Tom wants to move Tuesday's 10am clean to Wednesday afternoon.",
        "suggested_replies": ["No problem — Wednesday at 1 PM works.",
                              "Wednesday is full; is Thursday OK?",
                              "Sure, I'll confirm a Wednesday time by tonight."],
        "reply": "Hi Tom,\n\nNo problem at all. I've pencilled you in for Wednesday at 1:00 PM instead of Tuesday. "
                 "Just let me know if a different afternoon time suits you better.\n\n"
                 "Best regards,\nSam\nBrightside Home Cleaning",
        "tasks": [{"title": "Move Tom's clean from Tuesday 10am to Wednesday 1pm", "due": ""}],
    },
]

DEMO_FYI = [
    ("demo-receipt", "Stripe <receipts@stripe.example>", "Your payout of $1,240.00 is on the way", "SKIPPED"),
    ("demo-news", "Local Business Weekly <news@lbw.example>", "5 ways to get more reviews this month", "SKIPPED"),
    ("demo-thanks", "Ana Ruiz <ana.ruiz@example.com>", "Thanks — the place looks amazing!", "NO_REPLY_NEEDED"),
]


DEMO_PROFILE = {
    "business_name": "Brightside Home Cleaning",
    "about": "Residential cleaning for homes and small offices. Owner: Sam.",
    "services_prices": "Standard clean $140. Deep clean $220. Move-out deep clean (2-bed) $240, usually about 4 hours. "
                       "Oven add-on $35. Weekly office cleaning from $320/month.",
    "hours_location": "Mon–Sat 8am–6pm.",
    "policies": "A 25% deposit holds a booking. 24 hours' notice to cancel. Satisfaction guarantee: free re-clean "
                "within 48 hours. Fully insured and bonded. We bring all supplies.",
    "links": "",
    "faq": "",
    "never_say": "Never promise same-day service.",
}


DEMO_SENT = {
    "id": "demo-sent", "sender": "Jordan Park <jordan@example.com>",
    "subject": "Monthly office cleaning proposal", "body": "Could you send a proposal?",
    "summary": "Jordan asked for a monthly office cleaning proposal.",
    "reply": "Hi Jordan,\n\nAttached is our proposal for weekly office cleaning at $320/month. "
             "Would you like to start on the 1st?\n\nBest regards,\nSam",
}

DEFAULT_DEMO = "cleaning"
DEMO_PACKS = {
    "cleaning": {"label": "Home cleaning business", "profile": DEMO_PROFILE, "emails": DEMO_EMAILS,
                 "fyi": DEMO_FYI, "sent": DEMO_SENT, "templates": [], "documents": [],
                 "signature": None, "user_name": None},
    **INDUSTRY_PACKS,
}


def demo_industries():
    return [{"id": key, "label": pack["label"], "business": pack["profile"]["business_name"]}
            for key, pack in DEMO_PACKS.items()]


def _stash_setting(conn, key, stash_key):
    """Remember a setting's current value (None if unset) so clear_demo can put it back."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    set_setting(conn, stash_key, json.dumps(row[0] if row else None))


def _restore_setting(conn, key, stash_key):
    raw = get_setting(conn, stash_key)
    if raw is None:
        return
    value = json.loads(raw)
    if value is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        set_setting(conn, key, value)
    conn.execute("DELETE FROM settings WHERE key = ?", (stash_key,))
    conn.commit()


def seed_demo_inbox(conn, industry=None):
    """Load a sample inbox. Your own profile, sign-off, saved replies and documents are kept and come back
    untouched when the samples are removed."""
    key = industry or DEFAULT_DEMO
    pack = DEMO_PACKS.get(key)
    if not pack:
        raise ValueError(f"Unknown sample business: {industry}")
    if has_demo(conn) or get_setting(conn, "demo_industry"):
        clear_demo(conn)  # switching between sample businesses
    now = _now()
    # The sample firm's profile replaces yours while the demo is loaded, so drafts and fact checks use it.
    _stash_setting(conn, "business_profile", "demo_saved_profile")
    save_business_profile(conn, pack["profile"])
    set_setting(conn, "demo_profile", json.dumps(get_business_profile(conn)))
    _stash_setting(conn, "industry", "demo_saved_industry")
    set_setting(conn, "industry", key if key in INDUSTRY_KITS else "")
    if pack.get("signature"):
        _stash_setting(conn, "user_signature", "demo_saved_signature")
        _stash_setting(conn, "user_name", "demo_saved_user_name")
        set_setting(conn, "user_signature", pack["signature"])
        set_setting(conn, "user_name", pack["user_name"])
    for i, item in enumerate(pack["emails"]):
        email = {"id": item["id"], "sender": item["sender"], "subject": item["subject"], "body": item["body"],
                 "thread_id": f"{item['id']}-thread", "received_at": (now - timedelta(minutes=35 * (i + 1))).isoformat()}
        log_email(conn, email, "DRAFT", {
            "needs_reply": True, "urgency": item["urgency"], "summary": item["summary"],
            "suggested_replies": item["suggested_replies"], "reply": item["reply"],
            "confidence": item["confidence"], "reason": item["reason"], "tasks": item["tasks"]}, is_demo=True)
    for i, entry in enumerate(pack["fyi"]):
        gid, sender, subject, action = entry[:4]
        body, reason = (entry[4], entry[5]) if len(entry) > 4 else ("", None)
        log_email(conn, {"id": gid, "sender": sender, "subject": subject, "body": body,
                         "received_at": (now - timedelta(hours=1 + i)).isoformat()},
                  action, {"summary": reason or ("Thank-you note, no action needed." if action == "NO_REPLY_NEEDED" else ""),
                           "reason": reason or ("A thank-you with nothing to answer." if action == "NO_REPLY_NEEDED"
                                                else "")}, is_demo=True)
    # One sent reply that is waiting on an answer, so the follow-up radar has something to show.
    sent = pack["sent"]
    log_email(conn, {"id": sent["id"], "sender": sent["sender"], "subject": sent["subject"], "body": sent["body"],
                     "thread_id": f"{sent['id']}-thread", "received_at": (now - timedelta(days=5)).isoformat()},
              "DRAFT", {"needs_reply": True, "summary": sent["summary"], "reply": sent["reply"], "confidence": 0.8},
              is_demo=True)
    conn.execute("UPDATE email_log SET sent_at = ?, followup_at = ?, followup_done = 0 WHERE gmail_id = ?",
                 ((now - timedelta(days=4)).isoformat(), (now - timedelta(days=1)).isoformat(), sent["id"]))
    conn.commit()
    for entry in pack.get("tracker", []):
        due = entry["due_in"]
        if due == "friday":
            due_at = tracker.parse_due("by Friday")
        else:
            due_at = (datetime.now().astimezone().date() + timedelta(days=due)).isoformat() if due is not None else None
        tracker.save_item(conn, entry["kind"], {"name": entry["name"], "email": entry["email"], "status": entry["status"],
                                                "due_at": due_at, "data": entry["data"]}, is_demo=True)
    template_ids = [save_template(conn, title, body) for title, body in pack["templates"]]
    document_ids = [add_document(conn, name, "sample", "text/plain", text) for name, text in pack["documents"]]
    set_setting(conn, "demo_template_ids", json.dumps(template_ids))
    set_setting(conn, "demo_document_ids", json.dumps(document_ids))
    set_setting(conn, "demo_industry", key)
    return {"seeded": True, "industry": key, "business": pack["profile"]["business_name"],
            "emails": len(pack["emails"]) + len(pack["fyi"]) + 1}


def clear_demo(conn):
    count = conn.execute("DELETE FROM email_log WHERE COALESCE(is_demo, 0) = 1").rowcount
    if get_setting(conn, "demo_saved_profile") is not None:
        _restore_setting(conn, "business_profile", "demo_saved_profile")
    else:  # sample loaded by an older version: remove its profile only if untouched
        demo_profile = get_setting(conn, "demo_profile")
        if demo_profile and json.loads(demo_profile) == get_business_profile(conn):
            conn.execute("DELETE FROM settings WHERE key = 'business_profile'")
    _restore_setting(conn, "industry", "demo_saved_industry")
    _restore_setting(conn, "user_signature", "demo_saved_signature")
    _restore_setting(conn, "user_name", "demo_saved_user_name")
    for setting, table in (("demo_template_ids", "templates"), ("demo_document_ids", "documents")):
        for row_id in json.loads(get_setting(conn, setting, "[]") or "[]"):
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    conn.execute("DELETE FROM settings WHERE key IN ('demo_profile', 'demo_template_ids', 'demo_document_ids', "
                 "'demo_industry')")
    conn.execute("DELETE FROM local_tasks WHERE source_email LIKE 'demo-%'")
    conn.execute("DELETE FROM tracker_items WHERE is_demo = 1")
    conn.execute("DELETE FROM key_dates_custom WHERE client_id IS NOT NULL AND client_id NOT IN (SELECT id FROM tracker_items)")
    conn.commit()
    return count


def demo_industry(conn):
    key = get_setting(conn, "demo_industry") or (DEFAULT_DEMO if has_demo(conn) else None)
    if key not in DEMO_PACKS:
        return None
    return {"id": key, "label": DEMO_PACKS[key]["label"], "business": DEMO_PACKS[key]["profile"]["business_name"]}


# ---------------------------------------------------------------------------
# Industry mode & starter kits
# ---------------------------------------------------------------------------

def get_industry(conn):
    key = (get_setting(conn, "industry", "") or "").strip()
    return key if key in industry.KITS else ""


def set_industry(conn, key):
    key = (key or "").strip()
    if key and key not in industry.KITS:
        raise ValueError("Unknown industry.")
    set_setting(conn, "industry", key)
    return key


def _sample_ids(conn, setting):
    return set(json.loads(get_setting(conn, setting, "[]") or "[]"))


def industry_options(conn):
    sample = _sample_ids(conn, "demo_template_ids")
    titles = {t["title"].strip().lower() for t in list_templates(conn) if t["id"] not in sample}
    return {"industry": get_industry(conn), "kits": [
        {"id": key, "label": kit["label"], "templates": len(kit["templates"]),
         "installed": sum(1 for title, _ in kit["templates"] if title.lower() in titles),
         "checklist": kit["checklist"][0]}
        for key, kit in industry.KITS.items()]}


def install_starter_kit(conn, key):
    """Adds the kit's saved replies, safety rules and checklist. Never overwrites or duplicates what's there."""
    kit = industry.KITS.get(key)
    if not kit:
        raise ValueError("Unknown industry.")
    # While a sample inbox is loaded, the kit still goes into the user's own setup: anything the sample added is
    # removed when the sample is cleared, so a sample reply or document with a kit name is kept for good instead.
    sample_loaded = get_setting(conn, "demo_industry") is not None
    sample_templates = _sample_ids(conn, "demo_template_ids")
    mine = {t["title"].strip().lower() for t in list_templates(conn) if t["id"] not in sample_templates}
    from_sample = {t["title"].strip().lower(): t["id"] for t in list_templates(conn) if t["id"] in sample_templates}
    added = 0
    for title, body in kit["templates"]:
        if title.lower() in mine:
            continue
        if title.lower() in from_sample:
            save_template(conn, title, body, from_sample[title.lower()])
            sample_templates.discard(from_sample[title.lower()])
        else:
            save_template(conn, title, body)
        added += 1
    if sample_loaded:
        set_setting(conn, "demo_template_ids", json.dumps(sorted(sample_templates)))

    stashed = get_setting(conn, "demo_saved_profile") if sample_loaded else None
    if stashed is not None:  # the sample's profile is showing; the user's own one waits in the stash
        raw = json.loads(stashed)
        profile = {key_: (v or "") for key_, v in (json.loads(raw) if raw else {}).items()}
    else:
        profile = get_business_profile(conn)
    rules_added = not (profile.get("never_say") or "").strip()
    if rules_added:
        profile["never_say"] = kit["never_say"]
        if stashed is not None:
            clean = {k: (profile.get(k) or "").strip()[:6000] for k, _ in BUSINESS_FIELDS}
            set_setting(conn, "demo_saved_profile", json.dumps(json.dumps(clean)))
        else:
            save_business_profile(conn, profile)

    name, text = kit["checklist"]
    sample_docs = _sample_ids(conn, "demo_document_ids")
    docs = conn.execute("SELECT id FROM documents WHERE name = ?", (name,)).fetchall()
    checklist_added = not any(d[0] not in sample_docs for d in docs)
    if checklist_added:
        if docs:  # only the sample's copy exists: keep it, with the kit's text
            conn.execute("UPDATE documents SET content = ?, source = 'starter kit' WHERE id = ?", (text, docs[0][0]))
            sample_docs.discard(docs[0][0])
            set_setting(conn, "demo_document_ids", json.dumps(sorted(sample_docs)))
            conn.commit()
        else:
            add_document(conn, name, "starter kit", "text/plain", text)

    if get_setting(conn, "demo_saved_industry") is not None:
        set_setting(conn, "demo_saved_industry", json.dumps(key))  # applies once the sample is cleared
    else:
        set_industry(conn, key)
    return {"industry": key, "label": kit["label"], "templates_added": added,
            "templates_skipped": len(kit["templates"]) - added, "rules_added": rules_added,
            "checklist_added": checklist_added, "checklist": name}


# ---------------------------------------------------------------------------
# Security-plan summary: what C.A.P. does with client data, from the actual settings
# ---------------------------------------------------------------------------

def security_summary(conn, account=None):
    account = account or {}
    model, api_base, _ = get_model_settings(conn)
    local = model.startswith("ollama/")
    provider = model.split("/", 1)[0] if "/" in model else "openai"
    provider_name = {"anthropic": "Anthropic", "gemini": "Google (Gemini)", "groq": "Groq", "openrouter": "OpenRouter",
                     "openai": "OpenAI", "mistral": "Mistral", "deepseek": "DeepSeek", "xai": "xAI"}.get(provider, provider)
    business = get_business_profile(conn).get("business_name") or "Our office"
    kind = account.get("kind")
    email_line = {
        "gmail_api": "Gmail, using Google sign-in (OAuth). C.A.P. holds a sign-in token, not the password.",
        "microsoft": "Outlook / Microsoft 365, using Microsoft sign-in (OAuth). C.A.P. holds a sign-in token, not the password.",
    }.get(kind, f"{account.get('label') or 'Email'} over encrypted IMAP/SMTP with an app password." if kind
          else "No mailbox connected yet.")
    secret_store = secret_backend_name()
    lines = [
        f"{business} — how C.A.P. handles client email",
        f"Prepared {datetime.now().astimezone().strftime('%B %d, %Y')} for our written information security plan. "
        f"Describes C.A.P. {APP_VERSION} as currently configured on this computer.",
        "",
        "WHERE DATA IS KEPT",
        f"- Email text, drafts, tasks, saved replies and uploaded files are stored in a database on this computer: {DATA_DIR}",
        "- C.A.P. has no server of its own. Nothing is uploaded to the makers of C.A.P.",
        "- The database is not encrypted by C.A.P. itself. Turn on BitLocker (Windows full-disk encryption) and use a "
        "Windows sign-in password on this computer.",
        f"- Passwords, API keys and sign-in tokens are kept in {secret_store}.",
        "",
        "WHAT CONNECTS TO THE INTERNET",
        f"- Email: {email_line}",
        "- AI drafting: " + (
            f"a local model ({model.removeprefix('ollama/')}) running on this computer through Ollama. Email text is "
            "not sent to any AI company." if local else
            f"{provider_name} ({model}), using our own API account. The text needed to sort and draft each email is "
            f"sent to {provider_name} under that account's data terms."),
        "- Google Calendar, only if connected, to avoid proposing times that are already booked.",
        "- C.A.P. makes no other outside connections for its own purposes.",
        "",
        "SAFEGUARDS IN THE APP",
        "- Nothing is sent, archived or deleted without a person clicking to do it. There is no automatic sending.",
        "- Every email is checked locally for signs of fraud and impersonation (payment or bank-detail changes, "
        "lookalike sender addresses, fake IRS messages, credential requests, risky attachments), with a reminder "
        "to verify by phone.",
        ("- When a cloud AI model is used, Social Security, account, card and license numbers are masked before "
         "any text leaves this computer." if get_setting(conn, "cloud_redaction", "1") != "0" else
         "- Masking of sensitive numbers before cloud AI is turned OFF."),
        (f"- Email text is removed {get_setting(conn, 'retention_days')} days after it's handled."
         if int(get_setting(conn, "retention_days", "0") or 0) else "- Email text is currently kept until deleted."),
        ("- Sensitive numbers in handled emails are masked to their last 4 digits."
         if get_setting(conn, "mask_handled", "0") == "1" else None),
        ("- C.A.P. is protected by a PIN and locks itself after inactivity." if get_setting(conn, "lock_pin")
         else "- C.A.P. is not protected by its own PIN; rely on the Windows sign-in."),
        "- An audit log records sends, deletions, exports and security changes, with times.",
        "- Emails containing what look like Social Security, tax ID, bank, card or driver's license numbers are flagged, "
        "and C.A.P. warns before sending a reply that contains one.",
        "- AI drafts only use facts from the email, our business profile and our own documents, and highlight "
        "anything that isn't backed by them for review.",
        "- The local app only accepts requests from this computer, protected by a per-launch access token.",
        "- Email history can be exported or deleted from Settings at any time.",
        "",
        "OUR RESPONSIBILITIES",
        "- Keep this computer's operating system and C.A.P. up to date.",
        "- Limit who can sign in to this computer, and lock it when unattended.",
        "- Ask clients to send sensitive documents through our secure portal, not email.",
        "- Review this summary when settings change, and at least once a year.",
        "",
        "This summary describes the software's behavior. It is not legal or compliance advice; our full security "
        "plan must cover the whole practice.",
    ]
    return "\n".join(line for line in lines if line is not None)


# ---------------------------------------------------------------------------
# Fraud & impersonation shield (see fraud.py): who the office already knows comes from its own mail history
# ---------------------------------------------------------------------------

def known_contacts(conn):
    counts, names = {}, {}
    for (sender,) in conn.execute("SELECT sender FROM email_log WHERE sender IS NOT NULL"):
        name, address = parseaddr(sender)
        address = address.lower()
        if not address:
            continue
        counts[address] = counts.get(address, 0) + 1
        if name.strip():
            names.setdefault(name.strip().lower(), set()).add(address)
    return fraud.Contacts(counts, names)


def fraud_check(conn, email, contacts=None):
    """Assess one email. Works whether or not the email is already stored."""
    contacts = contacts or known_contacts(conn)
    stored = conn.execute("SELECT 1 FROM email_log WHERE gmail_id = ?", (email.get("id") or email.get("gmail_id"),)
                          ).fetchone() if (email.get("id") or email.get("gmail_id")) else None
    return fraud.assess(email, contacts, self_count=1 if stored else 0)


def fraud_alerts(conn, days=30, limit=300):
    contacts = known_contacts(conn)
    since = (_now() - timedelta(days=days)).isoformat()
    rows = conn.execute(f"SELECT {EMAIL_COLUMNS} FROM email_log WHERE is_archived = 0 AND sent_at IS NULL "
                        "AND COALESCE(received_at, processed_at) >= ? ORDER BY COALESCE(received_at, processed_at) DESC "
                        "LIMIT ?", (since, limit)).fetchall()
    out = []
    for row in rows:
        item = _email_row(row)
        risk = fraud.assess(item, contacts, self_count=1)
        if risk["level"]:
            out.append({"gmail_id": item["gmail_id"], "sender": item["sender"], "subject": item["subject"],
                        "level": risk["level"], "reason": risk["signals"][0]["label"]})
    return out


# ---------------------------------------------------------------------------
# Audit log: who did what, when (kept on this computer)
# ---------------------------------------------------------------------------

def log_audit(conn, action, detail=""):
    conn.execute("INSERT INTO audit_log (at, action, detail) VALUES (?, ?, ?)", (_iso(), action, (detail or "")[:500]))
    conn.commit()


def _audit_detached(action, detail=""):
    try:
        conn = init_db()
        try:
            log_audit(conn, action, detail)
        finally:
            conn.close()
    except Exception:
        pass


def list_audit(conn, limit=200):
    rows = conn.execute("SELECT at, action, detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{"at": r[0], "action": r[1], "detail": r[2] or ""} for r in rows]


def audit_csv(conn):
    import csv
    import io
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["time", "action", "detail"])
    for row in conn.execute("SELECT at, action, detail FROM audit_log ORDER BY id"):
        writer.writerow(row)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Retention & secure disposal
# ---------------------------------------------------------------------------

RETENTION_CHOICES = (0, 30, 90, 180, 365, 730)
HANDLED = "(sent_at IS NOT NULL OR is_archived = 1 OR action IN ('SKIPPED', 'NO_REPLY_NEEDED'))"


def apply_retention(conn):
    """Remove stored email text past the retention period and mask sensitive numbers in handled emails."""
    days = int(get_setting(conn, "retention_days", "0") or 0)
    result = {"purged": 0, "masked": 0}
    if days > 0:
        cutoff = (_now() - timedelta(days=days)).isoformat()
        cur = conn.execute(
            f"UPDATE email_log SET body = NULL, ai_draft = NULL, draft_reply = NULL, suggested_replies = '[]', "
            f"text_purged_at = ? WHERE text_purged_at IS NULL AND {HANDLED} "
            "AND COALESCE(received_at, processed_at) < ?", (_iso(), cutoff))
        result["purged"] = cur.rowcount
    if get_setting(conn, "mask_handled", "0") == "1":
        result["masked"] = mask_handled_emails(conn, log=False)
    conn.commit()
    set_setting(conn, "last_retention_at", _iso())
    if result["purged"]:
        log_audit(conn, "Retention", f"Removed the text of {result['purged']} emails older than {days} days.")
    if result["masked"]:
        log_audit(conn, "Masking", f"Masked sensitive numbers in {result['masked']} handled emails.")
    return result


def mask_handled_emails(conn, log=True):
    count = 0
    rows = conn.execute(f"SELECT gmail_id, body, draft_reply FROM email_log WHERE masked_at IS NULL AND {HANDLED} "
                        "AND body IS NOT NULL").fetchall()
    for gid, body, reply in rows:
        new_body, n1 = industry.mask_text(body)
        new_reply, n2 = industry.mask_text(reply or "")
        if n1 or n2:
            conn.execute("UPDATE email_log SET body = ?, draft_reply = ?, masked_at = ? WHERE gmail_id = ?",
                         (new_body, new_reply or reply, _iso(), gid))
            count += 1
    conn.commit()
    if log and count:
        log_audit(conn, "Masking", f"Masked sensitive numbers in {count} handled emails.")
    return count


def sensitive_stored(conn, limit=3000):
    rows = conn.execute("SELECT gmail_id, body, sent_at, is_archived, action FROM email_log WHERE body IS NOT NULL "
                        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    flagged = [r for r in rows if industry.find_sensitive(r[1])]
    handled = sum(1 for r in flagged if r[2] or r[3] or r[4] in ("SKIPPED", "NO_REPLY_NEEDED"))
    return {"total": len(flagged), "handled": handled}


def retention_due(conn, every_minutes=60):
    last = get_setting(conn, "last_retention_at")
    return not last or last < (_now() - timedelta(minutes=every_minutes)).isoformat()


# ---------------------------------------------------------------------------
# Security Center
# ---------------------------------------------------------------------------

def security_center(conn, lock_enabled=False):
    model, _, _ = get_model_settings(conn)
    local = model.startswith("ollama/")
    firewall = get_setting(conn, "cloud_redaction", "1") != "0"
    days = int(get_setting(conn, "retention_days", "0") or 0)
    mask = get_setting(conn, "mask_handled", "0") == "1"
    vault = secret_backend_name() != "the local database (not encrypted)"
    bitlocker = get_setting(conn, "bitlocker_confirmed", "0") == "1"
    rules = bool(get_industry(conn) or get_business_profile(conn).get("never_say"))
    checks = [
        {"id": "ai", "weight": 15, "ok": local or firewall, "title": "Client email stays private from AI companies",
         "detail": f"Drafting runs on this computer ({model.removeprefix('ollama/')})." if local else
         (f"Cloud AI ({provider_name(model)}); sensitive numbers are masked before anything is sent." if firewall else
          f"Cloud AI ({provider_name(model)}) receives full email text, including client numbers.")},
        {"id": "lock", "weight": 15, "ok": lock_enabled, "title": "App lock",
         "detail": "C.A.P. asks for a PIN when opened and after time away." if lock_enabled else
         "Anyone at this computer can open client email in C.A.P."},
        {"id": "vault", "weight": 10, "ok": vault, "title": "Passwords and keys in the Windows vault",
         "detail": f"Stored in {secret_backend_name()}."},
        {"id": "retention", "weight": 10, "ok": days > 0, "title": "Old email text is disposed of",
         "detail": f"Email text is removed {days} days after it's handled." if days else
         "Email text is kept forever. The FTC Safeguards Rule expects disposal within 2 years of last use."},
        {"id": "mask", "weight": 10, "ok": mask, "title": "Numbers masked in handled emails",
         "detail": "Once an email is handled, SSNs and account numbers in it are masked to the last 4 digits." if mask
         else "Handled emails still hold full SSNs and account numbers."},
        {"id": "fraud", "weight": 10, "ok": True, "title": "Fraud & impersonation shield",
         "detail": "Checks every email for payment changes, lookalike senders, fake IRS messages and phishing."},
        {"id": "sensitive", "weight": 10, "ok": True, "title": "Sensitive-number guard",
         "detail": "Flags emails with SSNs, account, card and license numbers, and warns before sending them."},
        {"id": "audit", "weight": 5, "ok": True, "title": "Audit log",
         "detail": "Sends, deletions, exports and security changes are recorded on this computer."},
        {"id": "bitlocker", "weight": 10, "ok": bitlocker, "title": "Disk encryption (BitLocker)",
         "detail": "You've confirmed BitLocker is on for this computer." if bitlocker else
         "C.A.P. can't check this itself. Turn on BitLocker in Windows Settings, then confirm here."},
        {"id": "rules", "weight": 5, "ok": rules, "title": "Industry safety rules",
         "detail": "Drafts follow your industry's never-promise rules." if rules else
         "Pick your industry in Settings so drafts follow its safety rules."},
    ]
    score = round(100 * sum(c["weight"] for c in checks if c["ok"]) / sum(c["weight"] for c in checks))
    return {"score": score, "checks": checks,
            "settings": {"cloud_redaction": firewall, "retention_days": days, "mask_handled": mask,
                         "bitlocker_confirmed": bitlocker, "lock_idle_minutes": int(get_setting(conn, "lock_idle_minutes", "10") or 10),
                         "is_local_model": local},
            "alerts": {"fraud": fraud_alerts(conn), "sensitive": sensitive_stored(conn)},
            "audit": list_audit(conn, 50)}


def has_demo(conn):
    return conn.execute("SELECT 1 FROM email_log WHERE COALESCE(is_demo,0) = 1 LIMIT 1").fetchone() is not None


# ---------------------------------------------------------------------------
# Privacy: export & purge (never includes API keys or Google tokens)
# ---------------------------------------------------------------------------

def export_local_data(conn):
    def rows(sql):
        cur = conn.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    settings = {k: v for k, v in conn.execute("SELECT key, value FROM settings")
                if k not in {"api_key", "ms_token_cache", "lock_pin", "lock_recovery"} and not k.startswith("secret:")}
    return {
        "exported_at": _iso(), "app_version": APP_VERSION, "settings": settings,
        "emails": rows("SELECT gmail_id, sender, subject, action, urgency, summary, draft_reply, received_at, sent_at "
                       "FROM email_log"),
        "tasks": rows("SELECT * FROM local_tasks"), "notes": rows("SELECT * FROM notes"),
        "templates": rows("SELECT * FROM templates"),
        "documents": rows("SELECT id, name, source, added_at, length(content) AS characters FROM documents"),
    }


def purge_email_history(conn):
    count = conn.execute("DELETE FROM email_log").rowcount
    conn.commit()
    return count

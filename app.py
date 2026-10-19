"""
C.A.P. local server. Serves the desktop UI (ui/) and a small JSON API on 127.0.0.1.

Safety:
- Binds to localhost only (see launcher.py).
- Every state-changing request must carry the per-launch token injected into the page,
  so other websites open in your browser can't drive the app.
- Requests with a foreign Host header are rejected (DNS-rebinding guard).
- Nothing is ever sent, archived or deleted without an explicit click.
"""

import hashlib
import hmac
import json
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import facts
import fraud
import tracker
import industry
import mail_accounts
import pipeline

UI_DIR = (Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent) / "ui"
APP_TOKEN = secrets.token_urlsafe(32)
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

app = FastAPI(title="C.A.P.", docs_url=None, redoc_url=None, openapi_url=None)
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")

# Google (API sign-in) state is cached because checking it means network calls.
state = {"google_connected": False, "google_email": None, "calendar_enabled": False, "needs_reconnect": False,
         "checked_at": 0.0}
JOB_LOCK = threading.Lock()
job = {"running": False, "phase": "", "done": 0, "total": 0, "current": "", "started_at": None,
       "finished_at": None, "result": None, "error": None}


# App lock: enforced here on the server, so a locked C.A.P. serves no client data to anyone.
LOCK = {"locked": None, "failures": 0, "retry_at": 0.0}
LOCK_OPEN_PATHS = {"/api/lock/status", "/api/lock/unlock"}


def _lock_enabled(conn):
    return bool(pipeline.get_setting(conn, "lock_pin"))


def _is_locked():
    if LOCK["locked"] is None:  # first request after start: locked if a PIN is set
        conn = db()
        try:
            LOCK["locked"] = _lock_enabled(conn)
        finally:
            conn.close()
    return LOCK["locked"]


# Other websites open in the user's browser must not be able to frame C.A.P. or have its responses sniffed as
# another type; API answers hold client data, so they are never cached.
SECURITY_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def local_only_guard(request: Request, call_next):
    response = await _guarded(request, call_next)
    response.headers.update(SECURITY_HEADERS)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    elif request.url.path.startswith("/ui/") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-cache"  # re-check on load, so an update never runs old scripts
    return response


async def _guarded(request, call_next):
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get("x-cap-token") != APP_TOKEN:
        return JSONResponse({"detail": "Missing or invalid app token"}, status_code=403)
    path = request.url.path
    if path.startswith("/api/") and path not in LOCK_OPEN_PATHS and _is_locked():
        return JSONResponse({"detail": "C.A.P. is locked. Enter your PIN to continue."}, status_code=423)
    return await call_next(request)


def db():
    return pipeline.init_db()


def refresh_connection(force=False):
    if not force and time.time() - state["checked_at"] < 300:
        return state
    auth = pipeline.check_auth_status()
    state.update({"google_connected": auth.get("connected", False), "google_email": auth.get("email"),
                  "calendar_enabled": auth.get("calendar_enabled", False),
                  "needs_reconnect": auth.get("needs_reconnect", False), "checked_at": time.time()})
    return state


def _gmail_service_or_none():
    if not refresh_connection().get("google_connected"):
        return None
    try:
        return pipeline.get_gmail_service()
    except Exception:
        return None


def open_mailbox(conn):
    """Email-password / Microsoft account if set up; otherwise Gmail via Google sign-in; otherwise None."""
    return mail_accounts.open_mailbox(conn, gmail_service_factory=_gmail_service_or_none)


def require_mailbox(conn):
    box = open_mailbox(conn)
    if box is None:
        raise HTTPException(400, "Connect your email first (Settings → Email account).")
    return box


def account_summary(conn):
    account = mail_accounts.get_account(conn)
    if account:
        return {"connected": True, "kind": account["kind"], "email": account["address"],
                "provider": account.get("provider"),
                "label": mail_accounts.PROVIDERS.get(account.get("provider"), {}).get("label", "Email")}
    refresh_connection()
    if state["google_connected"]:
        return {"connected": True, "kind": "gmail_api", "email": state["google_email"], "provider": "gmail",
                "label": "Gmail (Google sign-in)"}
    return {"connected": False, "kind": None, "email": None, "provider": None, "label": None}


# ---------------------------------------------------------------------------
# Page & status
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    page = (UI_DIR / "index.html").read_text(encoding="utf-8")
    return page.replace("__CAP_TOKEN__", APP_TOKEN).replace("__CAP_VERSION__", pipeline.APP_VERSION)


@app.get("/status")
def health():
    return {"ok": True}


@app.get("/api/status")
def status():
    conn = db()
    try:
        if pipeline.retention_due(conn):
            pipeline.apply_retention(conn)
        account = account_summary(conn)
        model, _, _ = pipeline.get_model_settings(conn)
        profile = pipeline.get_business_profile(conn)
        setup = {
            "ai": pipeline.get_setting(conn, "model_tested", "0") == "1",
            "email": account["connected"],
            "business": bool(profile.get("business_name") or profile.get("about"))
                        and pipeline.get_setting(conn, "demo_profile") != json.dumps(profile),
            "name": bool(pipeline.get_setting(conn, "user_name", "")),
        }
        return {
            "connected": account["connected"], "email": account["email"], "account": account,
            "google_connected": state["google_connected"], "calendar_enabled": state["calendar_enabled"],
            "needs_reconnect": state["needs_reconnect"] and not account["connected"],
            "credentials_present": pipeline.credentials_present(),
            "microsoft_ready": bool(mail_accounts.microsoft_client_id()),
            "secret_store": pipeline.secret_backend_name(),
            "model": model, "is_local_model": model.startswith("ollama/"),
            "setup": setup, "setup_complete": setup["ai"] and (setup["email"] or pipeline.has_demo(conn)),
            "onboarding_dismissed": pipeline.get_setting(conn, "onboarding_dismissed", "0") == "1",
            "has_demo": pipeline.has_demo(conn), "demo": pipeline.demo_industry(conn),
            "industry": pipeline.get_industry(conn),
            "demo_industries": pipeline.demo_industries(), "counts": pipeline.inbox_counts(conn),
            "auto_check_minutes": int(pipeline.get_setting(conn, "auto_check_minutes", "10") or 0),
            "job": job_snapshot(), "version": pipeline.APP_VERSION,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Email account: app password (IMAP/SMTP), Microsoft sign-in, or Google sign-in
# ---------------------------------------------------------------------------

class DetectRequest(BaseModel):
    address: str


class PasswordAccount(BaseModel):
    address: str
    password: str
    imap_host: str | None = None
    imap_port: int | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None


@app.get("/api/account")
def account_get():
    conn = db()
    try:
        return {**account_summary(conn), "secret_store": pipeline.secret_backend_name(),
                "microsoft_ready": bool(mail_accounts.microsoft_client_id())}
    finally:
        conn.close()


@app.post("/api/account/detect")
def account_detect(payload: DetectRequest):
    try:
        return mail_accounts.detect_provider(payload.address)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/account/password")
def account_password(payload: PasswordAccount):
    conn = db()
    try:
        account = mail_accounts.connect_with_password(conn, payload.address, payload.password, payload.imap_host,
                                                      payload.imap_port, payload.smtp_host, payload.smtp_port)
        pipeline.log_audit(conn, "Email connected", account["address"])
        return {"connected": True, "email": account["address"], "secret_store": pipeline.secret_backend_name()}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.post("/api/account/microsoft/start")
def account_microsoft_start():
    try:
        return mail_accounts.microsoft_start()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Couldn't reach Microsoft sign-in: {exc}")


@app.get("/api/account/microsoft/status")
def account_microsoft_status():
    return mail_accounts.microsoft_status()


@app.delete("/api/account")
def account_remove():
    conn = db()
    try:
        mail_accounts.remove_account(conn)
        pipeline.log_audit(conn, "Email disconnected")
        return {"connected": False}
    finally:
        conn.close()


@app.post("/api/google/credentials")
async def upload_credentials(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 100_000:
        raise HTTPException(400, "That file is too large to be an OAuth client file.")
    try:
        return pipeline.save_credentials_file(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/google/connect")
def google_connect():
    try:
        service = pipeline.get_gmail_service(interactive=True)
        email = service.users().getProfile(userId="me").execute().get("emailAddress")
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Google sign-in didn't complete: {exc}")
    refresh_connection(force=True)
    pipeline._audit_detached("Email connected", f"{email} (Google sign-in)")
    return {"connected": True, "email": email, "calendar_enabled": state["calendar_enabled"]}


@app.post("/api/google/disconnect")
def google_disconnect():
    pipeline.disconnect_google()
    pipeline._audit_detached("Email disconnected", "Google sign-in")
    state.update({"google_connected": False, "google_email": None, "calendar_enabled": False,
                  "checked_at": time.time()})
    return {"connected": False}


# ---------------------------------------------------------------------------
# Inbox check (background job with progress): sort first, then draft urgent-first
# ---------------------------------------------------------------------------

def job_snapshot():
    return {k: job[k] for k in ("running", "phase", "done", "total", "current", "finished_at", "result", "error")}


def _run_job():
    conn = db()
    box = None
    try:
        box = require_mailbox(conn)

        def progress(phase, done, total, current):
            job.update({"phase": phase, "done": done, "total": total, "current": current})

        job["result"] = pipeline.run_triage(conn, box, on_progress=progress)
    except HTTPException as exc:
        job["error"] = exc.detail
    except Exception as exc:
        job["error"] = mail_accounts.friendly_mail_error(exc, getattr(box, "provider", None))
    finally:
        if box:
            box.close()
        conn.close()
        job.update({"running": False, "phase": "", "finished_at": datetime.now().astimezone().isoformat(),
                    "current": ""})


@app.post("/api/inbox/check")
def inbox_check():
    conn = db()
    try:
        require_mailbox(conn).close()
    finally:
        conn.close()
    with JOB_LOCK:
        if job["running"]:
            return job_snapshot()
        job.update({"running": True, "phase": "fetching", "done": 0, "total": 0, "current": "Fetching new mail…",
                    "result": None, "error": None, "started_at": time.time()})
    threading.Thread(target=_run_job, daemon=True).start()
    return job_snapshot()


@app.get("/api/inbox/job")
def inbox_job():
    return job_snapshot()


@app.get("/api/inbox")
def inbox(view: str = "reply"):
    conn = db()
    try:
        items = pipeline.list_inbox(conn, view)
        contacts = pipeline.known_contacts(conn)
        for item in items:
            item["sensitive"] = bool(industry.find_sensitive(item.get("body")))
            item["fraud"] = fraud.assess(item, contacts, self_count=1)["level"]
            item.pop("body", None)
            item.pop("ai_draft", None)
            item["drafting_now"] = pipeline.is_drafting(item["gmail_id"])
        return {"items": items, "counts": pipeline.inbox_counts(conn)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


def _with_check(conn, item):
    item["drafting_now"] = pipeline.is_drafting(item["gmail_id"])
    hits = industry.find_sensitive(item.get("body"))
    item["sensitive"] = {"items": [{"label": h["label"], "masked": h["masked"]} for h in hits],
                         "summary": industry.describe_sensitive(hits)} if hits else None
    risk = pipeline.fraud_check(conn, item)
    item["fraud"] = risk if risk["level"] else None
    item["tracker"] = tracker.suggestions_for_email(conn, item, pipeline.get_industry(conn))
    if item["draft_reply"] and not item["sent_at"]:
        item["check"] = facts.check_text(conn, item, item["draft_reply"])
    return item


@app.get("/api/email/{gmail_id}")
def email_detail(gmail_id: str):
    conn = db()
    try:
        item = pipeline.get_email(conn, gmail_id)
        if not item:
            raise HTTPException(404, "Email not found.")
        return _with_check(conn, item)
    finally:
        conn.close()


class DraftRequest(BaseModel):
    tone: str | None = None
    instruction: str | None = None


class TextRequest(BaseModel):
    text: str
    followup: bool = False
    allow_sensitive: bool = False


def _draft(gmail_id, tone=None, instruction=None):
    conn = db()
    box = None
    try:
        item = pipeline.get_email(conn, gmail_id)
        if not item:
            raise HTTPException(404, "Email not found.")
        if not item["is_demo"]:
            try:
                box = open_mailbox(conn)
            except Exception:
                box = None
        result = pipeline.generate_draft(conn, gmail_id, box, tone, instruction)
        return _with_check(conn, result)
    except pipeline.DraftBusy:
        raise HTTPException(409, "This draft is already being written. It will appear in a moment.")
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    finally:
        if box:
            box.close()
        conn.close()


@app.post("/api/email/{gmail_id}/regenerate")
def email_regenerate(gmail_id: str, payload: DraftRequest):
    return _draft(gmail_id, payload.tone, payload.instruction)


@app.post("/api/email/{gmail_id}/draft-now")
def email_draft_now(gmail_id: str):
    """Opened an email whose draft isn't written yet: write it now, ahead of the background queue."""
    return _draft(gmail_id)


@app.post("/api/email/{gmail_id}/facts")
def email_facts(gmail_id: str, payload: TextRequest):
    conn = db()
    try:
        item = pipeline.get_email(conn, gmail_id)
        if not item:
            raise HTTPException(404, "Email not found.")
        return facts.check_text(conn, item, payload.text)
    finally:
        conn.close()


@app.post("/api/email/{gmail_id}/save-draft")
def email_save_draft(gmail_id: str, payload: TextRequest):
    conn = db()
    try:
        conn.execute("UPDATE email_log SET draft_reply = ? WHERE gmail_id = ? AND sent_at IS NULL",
                     (payload.text, gmail_id))
        conn.commit()
        return {"saved": True}
    finally:
        conn.close()


def _send(conn, gmail_id, text, followup):
    item = pipeline.get_email(conn, gmail_id)
    if not item:
        raise HTTPException(404, "Email not found.")
    text = (text or "").strip()
    if not text:
        raise HTTPException(400, "The reply is empty.")
    days = int(pipeline.get_setting(conn, "followup_days", "3") or 3) if followup else None
    if item["is_demo"]:
        return item, days
    box = require_mailbox(conn)
    try:
        email, _ = pipeline._email_for_drafting(conn, gmail_id, box)
        box.send_reply(email, text, from_name=pipeline.get_setting(conn, "user_name", ""))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Your email server didn't accept the message. "
                                 f"{mail_accounts.friendly_mail_error(exc, getattr(box, 'provider', None))}")
    finally:
        box.close()
    return item, days


@app.post("/api/email/{gmail_id}/send")
def email_send(gmail_id: str, payload: TextRequest):
    conn = db()
    try:
        item = pipeline.get_email(conn, gmail_id)
        if item and item["sent_at"]:
            raise HTTPException(400, "This reply was already sent.")
        hits = industry.find_sensitive(payload.text)
        if hits and not payload.allow_sensitive:
            raise HTTPException(409, f"This reply contains what looks like {industry.describe_sensitive(hits)} "
                                     f"({', '.join(h['masked'] for h in hits)}). Email isn't a safe place for it.")
        item, days = _send(conn, gmail_id, payload.text, payload.followup)
        pipeline.log_audit(conn, "Reply sent" + (" (sample)" if item["is_demo"] else ""),
                           f"To {item['sender']}: {item['subject']}"
                           + (" — sent despite the sensitive-number warning" if hits else ""))
        learn = facts.learn_from_send(conn, item, payload.text.strip())
        pipeline.mark_sent(conn, gmail_id, payload.text.strip(), days, learn["similarity"])
        return {"sent": True, "demo": item["is_demo"], "followup_days": days, "learn": learn,
                "subject": item["subject"]}
    finally:
        conn.close()


class LearnRequest(BaseModel):
    type: str
    field: str
    new: str | None = None
    old: str | None = None
    text: str | None = None


@app.post("/api/learn")
def learn_apply(payload: LearnRequest):
    conn = db()
    try:
        return facts.apply_learning(conn, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


def _mail_action(gmail_id, action):
    conn = db()
    try:
        item = pipeline.get_email(conn, gmail_id)
        if not item:
            raise HTTPException(404, "Email not found.")
        on_server = False
        if not item["is_demo"]:
            box = open_mailbox(conn)
            if box is None and action == "trash":
                raise HTTPException(400, "Connect your email first (Settings → Email account).")
            if box is not None:
                try:
                    email, _ = pipeline._email_for_drafting(conn, gmail_id, None)
                    (box.archive if action == "done" else box.trash)(email)
                    on_server = True
                except Exception as exc:
                    verb = "archive it" if action == "done" else "move it to Trash"
                    raise HTTPException(502, f"Couldn't {verb} on your mail server: "
                                             f"{mail_accounts.friendly_mail_error(exc, getattr(box, 'provider', None))}")
                finally:
                    box.close()
        if action == "done":
            pipeline.mark_done(conn, gmail_id)
        else:
            conn.execute("UPDATE email_log SET action = 'TRASHED', is_archived = 1 WHERE gmail_id = ?", (gmail_id,))
            conn.commit()
        return {"ok": True, "on_server": on_server}
    finally:
        conn.close()


@app.post("/api/email/{gmail_id}/done")
def email_done(gmail_id: str):
    r = _mail_action(gmail_id, "done")
    return {"done": True, "archived_in_gmail": r["on_server"], "archived_on_server": r["on_server"]}


@app.post("/api/email/{gmail_id}/trash")
def email_trash(gmail_id: str):
    _mail_action(gmail_id, "trash")
    conn = db()
    try:
        item = pipeline.get_email(conn, gmail_id)
        pipeline.log_audit(conn, "Email deleted", f"{(item or {}).get('subject', gmail_id)}")
    finally:
        conn.close()
    return {"trashed": True}


class SnoozeRequest(BaseModel):
    until: str


@app.post("/api/email/{gmail_id}/snooze")
def email_snooze(gmail_id: str, payload: SnoozeRequest):
    conn = db()
    try:
        return {"snoozed_until": pipeline.snooze(conn, gmail_id, payload.until)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.post("/api/email/{gmail_id}/unsnooze")
def email_unsnooze(gmail_id: str):
    conn = db()
    try:
        pipeline.unsnooze(conn, gmail_id)
        return {"snoozed": False}
    finally:
        conn.close()


@app.post("/api/email/{gmail_id}/needs-reply")
def email_needs_reply(gmail_id: str):
    conn = db()
    try:
        pipeline.move_to_reply(conn, gmail_id)
        return {"moved": True}
    finally:
        conn.close()


class TaskFromEmail(BaseModel):
    index: int | None = None


@app.post("/api/email/{gmail_id}/task")
def email_task(gmail_id: str, payload: TaskFromEmail):
    conn = db()
    try:
        return {"task_id": pipeline.task_from_email(conn, gmail_id, payload.index)}
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Follow-ups
# ---------------------------------------------------------------------------

@app.get("/api/followups")
def followups():
    conn = db()
    try:
        return pipeline.list_followups(conn, include_upcoming=True)
    finally:
        conn.close()


@app.post("/api/followups/{gmail_id}/draft")
def followup_draft(gmail_id: str):
    conn = db()
    try:
        return {"text": pipeline.draft_followup(conn, gmail_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.post("/api/followups/{gmail_id}/send")
def followup_send(gmail_id: str, payload: TextRequest):
    conn = db()
    try:
        hits = industry.find_sensitive(payload.text)
        if hits and not payload.allow_sensitive:
            raise HTTPException(409, f"This follow-up contains what looks like {industry.describe_sensitive(hits)}. "
                                     "Remove it before sending.")
        _send(conn, gmail_id, payload.text, False)
        pipeline.log_audit(conn, "Follow-up sent", gmail_id)
        pipeline.dismiss_followup(conn, gmail_id)
        return {"sent": True}
    finally:
        conn.close()


@app.post("/api/followups/{gmail_id}/dismiss")
def followup_dismiss(gmail_id: str):
    conn = db()
    try:
        pipeline.dismiss_followup(conn, gmail_id)
        return {"dismissed": True}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Today
# ---------------------------------------------------------------------------

def _events_between(start, end):
    if not (refresh_connection().get("google_connected") and state.get("calendar_enabled")):
        return []
    try:
        return [
            {"id": ev.get("id"), "summary": ev.get("summary") or "(no title)",
             "start": (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"),
             "end": (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date"),
             "all_day": "date" in (ev.get("start") or {}), "link": ev.get("htmlLink")}
            for ev in pipeline.list_calendar_events(start.isoformat(), end.isoformat())
        ]
    except Exception:
        return []


@app.get("/api/today")
def today():
    now = datetime.now().astimezone()
    today_key = now.date().isoformat()
    conn = db()
    try:
        tasks = pipeline.list_tasks(conn, include_completed=False)
        reply_items = pipeline.list_inbox(conn, "reply")
        contacts = pipeline.known_contacts(conn)
        added = {(r[0], r[1]) for r in conn.execute("SELECT source_email, title FROM local_tasks")}
        suggested = []
        for item in reply_items:
            for i, task in enumerate(item["suggested_tasks"]):
                if (item["gmail_id"], task["title"]) not in added:
                    suggested.append({"gmail_id": item["gmail_id"], "index": i, "title": task["title"],
                                      "due": task.get("due"), "from": item["sender_name"] or item["sender_email"]})
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return {
            "date": now.strftime("%A, %B %d").replace(" 0", " "),
            "hour": now.hour,
            "user_name": pipeline.get_setting(conn, "user_name", ""),
            "counts": pipeline.inbox_counts(conn),
            "replies": [{**{k: i[k] for k in ("gmail_id", "sender_name", "sender_email", "subject", "summary",
                                              "urgency", "review_carefully", "is_demo", "draft_state")},
                         "fraud": fraud.assess(i, contacts, self_count=1)["level"]} for i in reply_items[:6]],
            "followups": [{k: f[k] for k in ("gmail_id", "sender_name", "sender_email", "subject", "days_waiting")}
                          for f in pipeline.list_followups(conn)[:5]],
            "tasks": [t for t in tasks if t["due_at"] and t["due_at"][:10] <= today_key][:8],
            "open_tasks": len(tasks),
            "suggested_tasks": suggested[:5],
            "events": _events_between(start, start + timedelta(days=1)),
            "time_saved": pipeline.time_saved(conn),
            "key_dates": tracker.key_dates(conn, pipeline.get_industry(conn)),
            "last_check": job.get("finished_at"),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tasks & calendar
# ---------------------------------------------------------------------------

class TaskRequest(BaseModel):
    title: str
    notes: str | None = None
    due_at: str | None = None
    priority: str = "MEDIUM"
    add_to_calendar: bool = False


@app.get("/api/tasks")
def tasks():
    conn = db()
    try:
        return pipeline.list_tasks(conn)
    finally:
        conn.close()


@app.post("/api/tasks")
def task_create(payload: TaskRequest):
    conn = db()
    try:
        task_id = pipeline.create_task(conn, payload.title, payload.notes, payload.due_at, payload.priority)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()
    calendar_note = None
    if payload.add_to_calendar and payload.due_at and "T" in payload.due_at:
        try:
            pipeline.create_calendar_event(payload.title, payload.due_at, description=payload.notes)
            calendar_note = "added"
        except Exception as exc:
            calendar_note = f"not added: {exc}"
    return {"id": task_id, "calendar": calendar_note}


@app.put("/api/tasks/{task_id}")
def task_update(task_id: int, payload: TaskRequest):
    conn = db()
    try:
        pipeline.update_task(conn, task_id, payload.title, payload.notes, payload.due_at or "", payload.priority)
        return {"updated": True}
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


class ToggleRequest(BaseModel):
    completed: bool = True


@app.post("/api/tasks/{task_id}/toggle")
def task_toggle(task_id: int, payload: ToggleRequest):
    conn = db()
    try:
        pipeline.set_task_completed(conn, task_id, payload.completed)
        return {"completed": payload.completed}
    finally:
        conn.close()


@app.delete("/api/tasks/completed")
def tasks_clear_completed():
    conn = db()
    try:
        return {"deleted": pipeline.delete_completed_tasks(conn)}
    finally:
        conn.close()


@app.delete("/api/tasks/{task_id}")
def task_delete(task_id: int):
    conn = db()
    try:
        pipeline.delete_task(conn, task_id)
        return {"deleted": True}
    finally:
        conn.close()


@app.get("/api/calendar/upcoming")
def calendar_upcoming(days: int = 7):
    now = datetime.now().astimezone()
    if not refresh_connection().get("google_connected"):
        return {"available": False, "reason": "Calendar needs Google sign-in (Settings → Google Calendar).",
                "events": []}
    if not state.get("calendar_enabled"):
        return {"available": False, "reason": "Turn on the Google Calendar API in your Google Cloud project, "
                                              "then reconnect.", "events": []}
    return {"available": True, "events": _events_between(now, now + timedelta(days=max(1, min(days, 31))))}


class EventRequest(BaseModel):
    summary: str
    start_at: str
    end_at: str | None = None
    description: str | None = None


@app.post("/api/calendar/events")
def calendar_create(payload: EventRequest):
    if not refresh_connection().get("google_connected"):
        raise HTTPException(400, "Calendar needs Google sign-in (Settings → Google Calendar).")
    try:
        event = pipeline.create_calendar_event(payload.summary, payload.start_at, payload.end_at, payload.description)
        return {"created": True, "id": event.get("id")}
    except Exception as exc:
        raise HTTPException(400, f"Couldn't create the event: {exc}")


# ---------------------------------------------------------------------------
# Knowledge: business profile, saved replies, files, notes, ask
# ---------------------------------------------------------------------------

@app.get("/api/profile")
def profile_get():
    conn = db()
    try:
        return {"fields": [{"key": k, "label": label} for k, label in pipeline.BUSINESS_FIELDS],
                "values": pipeline.get_business_profile(conn)}
    finally:
        conn.close()


@app.put("/api/profile")
def profile_put(values: dict):
    conn = db()
    try:
        return pipeline.save_business_profile(conn, values)
    finally:
        conn.close()


class TemplateRequest(BaseModel):
    title: str
    body: str


@app.get("/api/templates")
def templates():
    conn = db()
    try:
        return pipeline.list_templates(conn)
    finally:
        conn.close()


@app.post("/api/templates")
def template_create(payload: TemplateRequest):
    conn = db()
    try:
        return {"id": pipeline.save_template(conn, payload.title, payload.body)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.put("/api/templates/{template_id}")
def template_update(template_id: int, payload: TemplateRequest):
    conn = db()
    try:
        return {"id": pipeline.save_template(conn, payload.title, payload.body, template_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.delete("/api/templates/{template_id}")
def template_delete(template_id: int):
    conn = db()
    try:
        pipeline.delete_template(conn, template_id)
        return {"deleted": True}
    finally:
        conn.close()


class RenderRequest(BaseModel):
    gmail_id: str | None = None


@app.post("/api/templates/{template_id}/render")
def template_render(template_id: int, payload: RenderRequest):
    conn = db()
    try:
        return {"text": pipeline.render_template(conn, template_id, payload.gmail_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


@app.get("/api/documents")
def documents():
    conn = db()
    try:
        return pipeline.list_documents(conn)
    finally:
        conn.close()


@app.post("/api/documents")
async def documents_upload(files: list[UploadFile] = File(...)):
    added, errors = [], []
    conn = db()
    try:
        for file in files:
            name = Path(file.filename or "upload").name
            data = await file.read()
            if len(data) > MAX_UPLOAD_BYTES:
                errors.append(f"{name}: larger than 25 MB")
                continue
            try:
                text = pipeline.extract_text_from_bytes(name, data, file.content_type)
                added.append({"id": pipeline.add_document(conn, name, "computer", file.content_type, text), "name": name})
            except Exception as exc:
                errors.append(str(exc) if str(exc).startswith(f"{name}:") else f"{name}: {exc}")
    finally:
        conn.close()
    if errors and not added:
        raise HTTPException(400, "; ".join(errors))
    return {"added": added, "errors": errors}


@app.delete("/api/documents/{document_id}")
def document_delete(document_id: int):
    conn = db()
    try:
        pipeline.delete_document(conn, document_id)
        return {"deleted": True}
    finally:
        conn.close()


class NoteRequest(BaseModel):
    title: str
    content: str


@app.get("/api/notes")
def notes():
    conn = db()
    try:
        return pipeline.list_notes(conn)
    finally:
        conn.close()


@app.post("/api/notes")
def note_create(payload: NoteRequest):
    conn = db()
    try:
        return {"id": pipeline.save_note(conn, payload.title, payload.content)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.put("/api/notes/{note_id}")
def note_update(note_id: int, payload: NoteRequest):
    conn = db()
    try:
        return {"id": pipeline.save_note(conn, payload.title, payload.content, note_id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.delete("/api/notes/{note_id}")
def note_delete(note_id: int):
    conn = db()
    try:
        pipeline.delete_note(conn, note_id)
        return {"deleted": True}
    finally:
        conn.close()


class AskRequest(BaseModel):
    question: str


@app.post("/api/ask")
def ask(payload: AskRequest):
    conn = db()
    try:
        return pipeline.answer_question(conn, payload.question)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        model, api_base, _ = pipeline.get_model_settings(conn)
        raise HTTPException(400, pipeline.friendly_model_error(exc, model, api_base))
    finally:
        conn.close()



# ---------------------------------------------------------------------------
# Settings & AI engine
# ---------------------------------------------------------------------------

class SettingsUpdate(BaseModel):
    user_name: str | None = None
    user_signature: str | None = None
    default_tone: str | None = None
    followup_days: int | None = None
    auto_check_minutes: int | None = None
    quality_pass: bool | None = None
    draft_mode: str | None = None
    onboarding_dismissed: bool | None = None
    model: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False


@app.get("/api/settings")
def settings_get():
    conn = db()
    try:
        model, api_base, api_key = pipeline.get_model_settings(conn)
        voice = pipeline.get_voice_profile(conn)
        return {
            "user_name": pipeline.get_setting(conn, "user_name", ""),
            "user_signature": pipeline.get_setting(conn, "user_signature", ""),
            "default_tone": pipeline.get_setting(conn, "default_tone", "Professional"),
            "followup_days": int(pipeline.get_setting(conn, "followup_days", "3") or 3),
            "auto_check_minutes": int(pipeline.get_setting(conn, "auto_check_minutes", "10") or 0),
            "quality_pass": pipeline.get_setting(conn, "quality_pass", "0") == "1",
            "draft_mode": pipeline.get_setting(conn, "draft_mode", "background"),
            "secret_store": pipeline.secret_backend_name(),
            "model": model, "api_base": api_base or "", "api_key_configured": bool(api_key),
            "voice": voice,
            "data_dir": str(pipeline.DATA_DIR), "version": pipeline.APP_VERSION,
        }
    finally:
        conn.close()


@app.post("/api/settings")
def settings_update(payload: SettingsUpdate):
    conn = db()
    try:
        set_ = lambda k, v: pipeline.set_setting(conn, k, v)  # noqa: E731
        if payload.user_name is not None:
            set_("user_name", payload.user_name.strip()[:100])
        if payload.user_signature is not None:
            set_("user_signature", payload.user_signature.strip()[:1000])
        if payload.default_tone in pipeline.TONES:
            set_("default_tone", payload.default_tone)
        if payload.followup_days is not None:
            set_("followup_days", str(max(1, min(payload.followup_days, 30))))
        if payload.auto_check_minutes is not None:
            set_("auto_check_minutes", str(payload.auto_check_minutes if payload.auto_check_minutes in (0, 5, 10, 15, 30, 60) else 10))
        if payload.quality_pass is not None:
            set_("quality_pass", "1" if payload.quality_pass else "0")
        if payload.draft_mode in ("background", "on_open"):
            set_("draft_mode", payload.draft_mode)
        if payload.onboarding_dismissed is not None:
            set_("onboarding_dismissed", "1" if payload.onboarding_dismissed else "0")
        if payload.model:
            model = pipeline.normalize_model_name(payload.model)
            if model != pipeline.get_setting(conn, "model"):
                set_("model_tested", "0")
            set_("model", model)
        model = pipeline.normalize_model_name(pipeline.get_setting(conn, "model", pipeline.OLLAMA_MODEL))
        if payload.api_base is not None:
            set_("api_base", pipeline.normalize_api_base(model, payload.api_base) or "")
        if payload.clear_api_key:
            pipeline.set_secret(conn, "api_key", "")
            pipeline.log_audit(conn, "API key removed")
        elif payload.api_key and payload.api_key.strip():
            pipeline.set_secret(conn, "api_key", payload.api_key.strip())
            pipeline.log_audit(conn, "API key saved", pipeline.provider_name(model))
        return {"saved": True}
    finally:
        conn.close()


@app.get("/api/models")
def models():
    conn = db()
    try:
        _, api_base, _ = pipeline.get_model_settings(conn)
    finally:
        conn.close()
    ollama = pipeline.detect_ollama(api_base if api_base and "11434" in api_base else pipeline.OLLAMA_API_BASE)
    return {"ollama": ollama, "recommended_local": pipeline.OLLAMA_MODEL_PRESETS,
            "cloud": pipeline.CLOUD_MODEL_PRESETS}


class ModelTest(BaseModel):
    model: str
    api_base: str | None = None
    api_key: str | None = None


@app.post("/api/models/test")
def models_test(payload: ModelTest):
    model = pipeline.normalize_model_name(payload.model)
    api_base = pipeline.normalize_api_base(model, payload.api_base) or (
        pipeline.OLLAMA_API_BASE if model.startswith("ollama/") else None)
    conn = db()
    try:
        api_key = (payload.api_key or "").strip() or (None if model.startswith("ollama/")
                                                      else pipeline.get_model_settings(conn)[2])
        try:
            result = pipeline.test_model_connection(model, api_base, api_key)
        except Exception as exc:
            raise HTTPException(400, pipeline.friendly_model_error(exc, model, api_base))
        if model != pipeline.get_setting(conn, "model"):
            pipeline.log_audit(conn, "AI engine changed", f"{model} ({pipeline.provider_name(model)})")
        pipeline.set_setting(conn, "model", model)
        pipeline.set_setting(conn, "api_base", api_base or "")
        if payload.api_key and payload.api_key.strip():
            pipeline.set_secret(conn, "api_key", payload.api_key.strip())
            pipeline.log_audit(conn, "API key saved", pipeline.provider_name(model))
        pipeline.set_setting(conn, "model_tested", "1")
        return {**result, "model": model}
    finally:
        conn.close()


@app.post("/api/voice/calibrate")
def voice_calibrate():
    conn = db()
    try:
        box = open_mailbox(conn)
        try:
            return pipeline.calibrate_user_voice(conn, box)
        finally:
            if box:
                box.close()
    finally:
        conn.close()



# ---------------------------------------------------------------------------
# Demo & privacy
# ---------------------------------------------------------------------------

class IndustryRequest(BaseModel):
    industry: str = ""


@app.get("/api/industry")
def industry_get():
    conn = db()
    try:
        return pipeline.industry_options(conn)
    finally:
        conn.close()


@app.post("/api/industry")
def industry_set(payload: IndustryRequest):
    conn = db()
    try:
        return {"industry": pipeline.set_industry(conn, payload.industry)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.post("/api/industry/kit")
def industry_kit(payload: IndustryRequest):
    conn = db()
    try:
        result = pipeline.install_starter_kit(conn, payload.industry)
        pipeline.log_audit(conn, "Starter kit added", result["label"])
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.get("/api/security-summary")
def security_summary(download: bool = False):
    conn = db()
    try:
        text = pipeline.security_summary(conn, account_summary(conn))
    finally:
        conn.close()
    headers = {"Content-Disposition": 'attachment; filename="CAP-data-handling-summary.txt"'} if download else {}
    return Response(text, media_type="text/plain; charset=utf-8", headers=headers)


@app.post("/api/email/{gmail_id}/verify-task")
def email_verify_task(gmail_id: str):
    conn = db()
    try:
        item = pipeline.get_email(conn, gmail_id)
        if not item:
            raise HTTPException(404, "Email not found.")
        task_id = pipeline.create_task(
            conn, f"Call {item['sender_name'] or item['sender_email']} at a number on file to verify: {item['subject']}",
            notes="Possible fraud flagged by C.A.P. Use a phone number you already have, not one in the email.",
            priority="HIGH", source_email=gmail_id)
        pipeline.log_audit(conn, "Fraud check", f"Verification task added for: {item['subject']}")
        return {"task_id": task_id}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Security Center, app lock, audit log
# ---------------------------------------------------------------------------

@app.get("/api/security")
def security_get():
    conn = db()
    try:
        return pipeline.security_center(conn, lock_enabled=_lock_enabled(conn))
    finally:
        conn.close()


class SecuritySettings(BaseModel):
    cloud_redaction: bool | None = None
    retention_days: int | None = None
    mask_handled: bool | None = None
    bitlocker_confirmed: bool | None = None
    lock_idle_minutes: int | None = None


@app.post("/api/security/settings")
def security_settings(payload: SecuritySettings):
    conn = db()
    try:
        changes = []
        if payload.cloud_redaction is not None:
            pipeline.set_setting(conn, "cloud_redaction", "1" if payload.cloud_redaction else "0")
            changes.append(f"privacy firewall {'on' if payload.cloud_redaction else 'OFF'}")
        if payload.retention_days is not None:
            if payload.retention_days not in pipeline.RETENTION_CHOICES:
                raise HTTPException(400, "Choose one of the listed retention periods.")
            pipeline.set_setting(conn, "retention_days", str(payload.retention_days))
            changes.append(f"retention {payload.retention_days or 'off'} days")
        if payload.mask_handled is not None:
            pipeline.set_setting(conn, "mask_handled", "1" if payload.mask_handled else "0")
            changes.append(f"mask handled emails {'on' if payload.mask_handled else 'off'}")
        if payload.bitlocker_confirmed is not None:
            pipeline.set_setting(conn, "bitlocker_confirmed", "1" if payload.bitlocker_confirmed else "0")
            changes.append(f"BitLocker confirmed {'yes' if payload.bitlocker_confirmed else 'no'}")
        if payload.lock_idle_minutes is not None:
            pipeline.set_setting(conn, "lock_idle_minutes", str(max(1, min(payload.lock_idle_minutes, 240))))
            changes.append(f"auto-lock after {payload.lock_idle_minutes} min")
        if changes:
            pipeline.log_audit(conn, "Security settings changed", ", ".join(changes))
        result = pipeline.apply_retention(conn) if (payload.retention_days or payload.mask_handled) else None
        return {"saved": True, "retention": result}
    finally:
        conn.close()


@app.post("/api/security/mask-now")
def security_mask_now():
    conn = db()
    try:
        return {"masked": pipeline.mask_handled_emails(conn)}
    finally:
        conn.close()


@app.get("/api/audit.csv")
def audit_csv():
    conn = db()
    try:
        body = pipeline.audit_csv(conn)
        pipeline.log_audit(conn, "Audit log exported")
    finally:
        conn.close()
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="CAP-audit-log.csv"'})


def _pin_hash(pin, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def _pin_matches(pin, stored):
    try:
        _, salt, digest = (stored or "").split("$")
    except ValueError:
        return False
    return hmac.compare_digest(_pin_hash(pin or "", salt).split("$")[2], digest)


@app.get("/api/lock/status")
def lock_status():
    conn = db()
    try:
        return {"enabled": _lock_enabled(conn), "locked": bool(_is_locked()),
                "idle_minutes": int(pipeline.get_setting(conn, "lock_idle_minutes", "10") or 10)}
    finally:
        conn.close()


class PinRequest(BaseModel):
    pin: str = ""
    current_pin: str = ""


@app.post("/api/lock/unlock")
def lock_unlock(payload: PinRequest):
    wait = LOCK["retry_at"] - time.time()
    if wait > 0:
        raise HTTPException(429, f"Too many wrong attempts. Try again in {int(wait) + 1} seconds.")
    conn = db()
    try:
        pin_ok = _pin_matches(payload.pin, pipeline.get_setting(conn, "lock_pin"))
        recovery_ok = not pin_ok and _pin_matches(payload.pin.strip().upper(), pipeline.get_setting(conn, "lock_recovery"))
        if not (pin_ok or recovery_ok):
            LOCK["failures"] += 1
            if LOCK["failures"] >= 5:
                LOCK["retry_at"] = time.time() + 30 * (LOCK["failures"] - 4)
            pipeline.log_audit(conn, "Unlock failed", f"Wrong PIN (attempt {LOCK['failures']})")
            raise HTTPException(401, "That PIN isn't right.")
        LOCK.update({"locked": False, "failures": 0, "retry_at": 0.0})
        if recovery_ok:  # recovery code used once: the lock is removed so a new PIN can be set
            conn.execute("DELETE FROM settings WHERE key IN ('lock_pin', 'lock_recovery')")
            conn.commit()
            pipeline.log_audit(conn, "Unlocked with recovery code", "App lock removed; set a new PIN.")
        else:
            pipeline.log_audit(conn, "Unlocked")
        return {"locked": False, "recovered": recovery_ok}
    finally:
        conn.close()


@app.post("/api/lock")
def lock_now():
    conn = db()
    try:
        if not _lock_enabled(conn):
            raise HTTPException(400, "Set a PIN in Security first.")
        LOCK["locked"] = True
        pipeline.log_audit(conn, "Locked")
        return {"locked": True}
    finally:
        conn.close()


@app.post("/api/lock/pin")
def lock_set_pin(payload: PinRequest):
    pin = (payload.pin or "").strip()
    if not (4 <= len(pin) <= 32):
        raise HTTPException(400, "Use a PIN of 4 to 32 characters.")
    conn = db()
    try:
        existing = pipeline.get_setting(conn, "lock_pin")
        if existing and not _pin_matches(payload.current_pin, existing):
            raise HTTPException(401, "Your current PIN isn't right.")
        recovery = "-".join(secrets.token_hex(2).upper() for _ in range(4))
        pipeline.set_setting(conn, "lock_pin", _pin_hash(pin))
        pipeline.set_setting(conn, "lock_recovery", _pin_hash(recovery))
        pipeline.log_audit(conn, "App lock PIN changed" if existing else "App lock turned on")
        return {"enabled": True, "recovery_code": recovery}
    finally:
        conn.close()


@app.delete("/api/lock/pin")
def lock_remove_pin(payload: PinRequest):
    conn = db()
    try:
        if not _pin_matches(payload.current_pin, pipeline.get_setting(conn, "lock_pin")):
            raise HTTPException(401, "Your current PIN isn't right.")
        conn.execute("DELETE FROM settings WHERE key IN ('lock_pin', 'lock_recovery')")
        conn.commit()
        LOCK["locked"] = False
        pipeline.log_audit(conn, "App lock turned off")
        return {"enabled": False}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Industry trackers: client returns (tax), certificates and renewals (insurance)
# ---------------------------------------------------------------------------

class TrackerItem(BaseModel):
    kind: str | None = None
    name: str | None = None
    email: str | None = None
    status: str | None = None
    due_at: str | None = None
    data: dict | None = None
    source_email: str | None = None


@app.get("/api/tracker")
def tracker_list():
    conn = db()
    try:
        industry_key = pipeline.get_industry(conn)
        kinds = [k for k, v in tracker.KINDS.items() if v["industry"] == industry_key]
        return {"industry": industry_key, "items": [i for i in tracker.list_items(conn) if i["kind"] in kinds],
                "kinds": {k: {"label": tracker.KINDS[k]["label"], "statuses": tracker.KINDS[k]["statuses"]} for k in kinds},
                "doc_names": tracker.DOC_NAMES, "default_docs": tracker.DEFAULT_CLIENT_DOCS,
                "key_dates": tracker.key_dates(conn, industry_key), "custom_dates": tracker.list_dates(conn)}
    finally:
        conn.close()


class KeyDate(BaseModel):
    date: str | None = None
    label: str | None = None
    client_id: int | None = None
    yearly: bool = False


@app.post("/api/key-dates")
def key_date_create(payload: KeyDate):
    conn = db()
    try:
        d = tracker.save_date(conn, payload.model_dump())
        pipeline.log_audit(conn, "Key date added", f"{d['set_date']}: {d['label']}")
        return d
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.put("/api/key-dates/{date_id}")
def key_date_update(date_id: int, payload: KeyDate):
    conn = db()
    try:
        return tracker.save_date(conn, payload.model_dump(), date_id)
    except ValueError as exc:
        raise HTTPException(404 if str(exc) == "Not found." else 400, str(exc))
    finally:
        conn.close()


@app.delete("/api/key-dates/{date_id}")
def key_date_delete(date_id: int):
    conn = db()
    try:
        d = tracker.get_date(conn, date_id)
        tracker.delete_date(conn, date_id)
        if d:
            pipeline.log_audit(conn, "Key date removed", f"{d['set_date']}: {d['label']}")
        return {"deleted": True}
    finally:
        conn.close()


@app.post("/api/tracker")
def tracker_create(payload: TrackerItem):
    conn = db()
    try:
        item = tracker.save_item(conn, payload.kind or "", payload.model_dump(exclude_none=True))
        pipeline.log_audit(conn, "Tracker item added", f"{tracker.KINDS[item['kind']]['label']}: {item['name']}")
        return item
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.put("/api/tracker/{item_id}")
def tracker_update(item_id: int, payload: TrackerItem):
    conn = db()
    try:
        current = tracker.get_item(conn, item_id)
        if not current:
            raise HTTPException(404, "Not found.")
        return tracker.save_item(conn, current["kind"], payload.model_dump(exclude_none=True), item_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.delete("/api/tracker/{item_id}")
def tracker_delete(item_id: int):
    conn = db()
    try:
        item = tracker.get_item(conn, item_id)
        tracker.delete_item(conn, item_id)
        if item:
            pipeline.log_audit(conn, "Tracker item removed", f"{tracker.KINDS[item['kind']]['label']}: {item['name']}")
        return {"deleted": True}
    finally:
        conn.close()


class DocsRequest(BaseModel):
    names: list[str]
    received: bool = True


@app.post("/api/tracker/{item_id}/docs")
def tracker_docs(item_id: int, payload: DocsRequest):
    conn = db()
    try:
        return tracker.set_docs(conn, item_id, payload.names, payload.received)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.get("/api/tracker/{item_id}/reminder")
def tracker_reminder(item_id: int):
    conn = db()
    try:
        item = tracker.get_item(conn, item_id)
        if not item:
            raise HTTPException(404, "Not found.")
        return tracker.reminder_for(item, pipeline._sign_off(conn), pipeline.get_business_profile(conn))
    finally:
        conn.close()


class DemoRequest(BaseModel):
    industry: str | None = None


@app.post("/api/demo")
def demo_seed(payload: DemoRequest | None = None):
    conn = db()
    try:
        result = pipeline.seed_demo_inbox(conn, payload.industry if payload else None)
        pipeline.log_audit(conn, "Sample inbox loaded", result["business"])
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()


@app.delete("/api/demo")
def demo_clear():
    conn = db()
    try:
        removed = pipeline.clear_demo(conn)
        pipeline.log_audit(conn, "Sample inbox removed")
        return {"removed": removed}
    finally:
        conn.close()


@app.get("/api/export")
def export():
    conn = db()
    try:
        body = json.dumps(pipeline.export_local_data(conn), indent=2, default=str)
        pipeline.log_audit(conn, "Data exported", "Full local data export (JSON)")
    finally:
        conn.close()
    name = f"cap-export-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/purge-email")
def purge_email():
    conn = db()
    try:
        deleted = pipeline.purge_email_history(conn)
        pipeline.log_audit(conn, "Email history deleted", f"{deleted} emails")
        return {"deleted": deleted}
    finally:
        conn.close()

"""
Email accounts for C.A.P.

Three ways in, all behind one small Mailbox interface:
- ImapMailbox  + app password      -> Gmail, Yahoo, iCloud, AOL, Zoho, Fastmail, most business hosts
- ImapMailbox  + Microsoft sign-in -> Outlook.com / Hotmail / Microsoft 365 (OAuth device code, XOAUTH2)
- GmailApiMailbox                  -> Gmail through Google's API (advanced; needs a credentials file)

Passwords go to the OS credential vault (Windows Credential Manager) when available.
"""

import base64
import email
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, formatdate, make_msgid, parseaddr, parsedate_to_datetime
from pathlib import Path

import pipeline

set_secret = pipeline.set_secret
get_secret = pipeline.get_secret
secret_backend_name = pipeline.secret_backend_name


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

PROVIDERS = {
    "gmail": {
        "label": "Gmail / Google Workspace", "imap": ("imap.gmail.com", 993), "smtp": ("smtp.gmail.com", 465),
        "method": "password", "saves_sent": True, "archive": "\\All",
        "help_url": "https://myaccount.google.com/apppasswords",
        "steps": ["Turn on 2-Step Verification for your Google account (if it isn't already).",
                  "Open Google Account → Security → App passwords (link below).",
                  "Create one named “C.A.P.” and paste the 16-letter password here."],
    },
    "yahoo": {
        "label": "Yahoo Mail", "imap": ("imap.mail.yahoo.com", 993), "smtp": ("smtp.mail.yahoo.com", 465),
        "method": "password", "saves_sent": False, "help_url": "https://login.yahoo.com/account/security",
        "steps": ["Open Yahoo Account Security (link below).", "Choose “Generate app password”, name it C.A.P.",
                  "Paste the password here."],
    },
    "icloud": {
        "label": "iCloud Mail", "imap": ("imap.mail.me.com", 993), "smtp": ("smtp.mail.me.com", 587),
        "method": "password", "saves_sent": False, "help_url": "https://account.apple.com",
        "steps": ["Sign in at account.apple.com (link below).", "Sign-In and Security → App-Specific Passwords → add one.",
                  "Paste it here. Use your full @icloud.com address."],
    },
    "aol": {
        "label": "AOL Mail", "imap": ("imap.aol.com", 993), "smtp": ("smtp.aol.com", 465), "method": "password",
        "saves_sent": False, "help_url": "https://login.aol.com/account/security",
        "steps": ["Open AOL Account Security (link below).", "Generate an app password and paste it here."],
    },
    "zoho": {
        "label": "Zoho Mail", "imap": ("imap.zoho.com", 993), "smtp": ("smtp.zoho.com", 465), "method": "password",
        "saves_sent": False, "help_url": "https://accounts.zoho.com",
        "steps": ["In Zoho Accounts → Security → App passwords, create one for C.A.P.", "Make sure IMAP access is on in Zoho Mail settings.",
                  "Paste the app password here."],
    },
    "fastmail": {
        "label": "Fastmail", "imap": ("imap.fastmail.com", 993), "smtp": ("smtp.fastmail.com", 465),
        "method": "password", "saves_sent": False, "help_url": "https://app.fastmail.com/settings/security/apps",
        "steps": ["Settings → Privacy & Security → Manage app passwords → New app password (IMAP + SMTP).",
                  "Paste it here."],
    },
    "microsoft": {
        "label": "Outlook.com / Microsoft 365", "imap": ("outlook.office365.com", 993),
        "smtp": ("smtp.office365.com", 587), "method": "microsoft", "saves_sent": True,
        "steps": ["Click “Sign in with Microsoft”.", "A code appears here; enter it on the Microsoft page that opens.",
                  "Approve access. C.A.P. never sees your password."],
    },
    "other": {
        "label": "Other email provider", "imap": ("", 993), "smtp": ("", 465), "method": "password",
        "saves_sent": False,
        "steps": ["Find your provider's IMAP and SMTP server names (often mail.yourdomain.com).",
                  "Use an app password if your provider offers one; otherwise your email password."],
    },
}

DOMAIN_PROVIDERS = {
    "gmail.com": "gmail", "googlemail.com": "gmail", "yahoo.com": "yahoo", "ymail.com": "yahoo",
    "rocketmail.com": "yahoo", "icloud.com": "icloud", "me.com": "icloud", "mac.com": "icloud", "aol.com": "aol",
    "zoho.com": "zoho", "zohomail.com": "zoho", "fastmail.com": "fastmail", "fastmail.fm": "fastmail",
    "outlook.com": "microsoft", "hotmail.com": "microsoft", "live.com": "microsoft", "msn.com": "microsoft",
}
CONSUMER_MICROSOFT = {"outlook.com", "hotmail.com", "live.com", "msn.com"}


def detect_provider(address):
    address = (address or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", address):
        raise ValueError("Enter a full email address, like you@yourbusiness.com.")
    domain = address.split("@", 1)[1]
    key = DOMAIN_PROVIDERS.get(domain)
    if not key:
        for suffix, provider in (("yahoo.", "yahoo"), ("hotmail.", "microsoft"), ("outlook.", "microsoft"),
                                 ("live.", "microsoft")):
            if domain.startswith(suffix):
                key = provider
                break
    key = key or "other"
    p = PROVIDERS[key]
    imap_host, imap_port = p["imap"]
    smtp_host, smtp_port = p["smtp"]
    if key == "microsoft" and domain in CONSUMER_MICROSOFT:
        smtp_host = "smtp-mail.outlook.com"
    if key == "other":
        imap_host, smtp_host = f"mail.{domain}", f"mail.{domain}"
    return {"provider": key, "label": p["label"], "method": p["method"], "steps": p["steps"],
            "help_url": p.get("help_url"), "imap_host": imap_host, "imap_port": imap_port,
            "smtp_host": smtp_host, "smtp_port": smtp_port,
            "microsoft_ready": bool(microsoft_client_id()) if key == "microsoft" else None,
            "custom_domain": key == "other"}


def friendly_mail_error(exc, provider=None):
    text = str(exc)
    low = text.lower()
    if any(m in low for m in ("authenticationfailed", "authentication failed", "invalid credentials",
                              "535", "534", "login failed", "auth failed", "username and password not accepted")):
        if provider == "gmail":
            return ("Google didn't accept that password. Gmail needs an app password (not your normal password); "
                    "2-Step Verification must be on.")
        if provider == "microsoft":
            return "Microsoft rejected the sign-in. Try “Sign in with Microsoft” again."
        return "The email server didn't accept that address and password. Check them, and use an app password if your provider has them."
    if "smtp auth" in low and "disabled" in low or "5.7.139" in low:
        return ("Your Microsoft 365 admin has SMTP sending turned off for this mailbox. Ask them to enable "
                "“Authenticated SMTP” for your account, then try again.")
    if any(m in low for m in ("name or service not known", "getaddrinfo", "nodename", "no address associated")):
        return "Couldn't find that mail server. Check the server name (for example mail.yourdomain.com)."
    if any(m in low for m in ("timed out", "timeout", "connection refused", "unreachable")):
        return "Couldn't reach the mail server. Check your internet connection and the server name/port."
    if "certificate" in low or "ssl" in low:
        return f"Secure connection to the mail server failed: {text[:200]}"
    return f"Mail server error: {text[:300]}"


# ---------------------------------------------------------------------------
# Microsoft sign-in (device code flow -> XOAUTH2 for IMAP and SMTP)
# ---------------------------------------------------------------------------

MS_SCOPES = ["https://outlook.office.com/IMAP.AccessAsUser.All", "https://outlook.office.com/SMTP.Send"]
_ms_flow = {"state": "idle"}
_ms_lock = threading.Lock()


def microsoft_client_id():
    """The vendor's (your) Azure app registration ID: env var, cap_config.json, or setting."""
    if os.environ.get("CAP_MS_CLIENT_ID"):
        return os.environ["CAP_MS_CLIENT_ID"].strip()
    for folder in (pipeline.DATA_DIR, pipeline.BUNDLE_DIR, Path(getattr(sys, "_MEIPASS", pipeline.APP_DIR))):
        cfg = Path(folder) / "cap_config.json"
        if cfg.exists():
            try:
                value = json.loads(cfg.read_text(encoding="utf-8")).get("microsoft_client_id", "")
                if value:
                    return value.strip()
            except (OSError, json.JSONDecodeError):
                pass
    return ""


def _msal_app(conn):
    import msal

    cache = msal.SerializableTokenCache()
    raw = pipeline.get_setting(conn, "ms_token_cache", "")
    if raw:
        cache.deserialize(raw)
    app = msal.PublicClientApplication(microsoft_client_id(), authority="https://login.microsoftonline.com/common",
                                       token_cache=cache)
    return app, cache


def _save_cache(conn, cache):
    if cache.has_state_changed:
        pipeline.set_setting(conn, "ms_token_cache", cache.serialize())


def microsoft_start():
    if not microsoft_client_id():
        raise ValueError("Microsoft sign-in isn't configured in this build (missing app ID). See SETUP.md → Microsoft.")
    conn = pipeline.init_db()
    try:
        app, cache = _msal_app(conn)
        flow = app.initiate_device_flow(scopes=MS_SCOPES)
    finally:
        conn.close()
    if "user_code" not in flow:
        raise ValueError(f"Microsoft sign-in couldn't start: {flow.get('error_description', flow)}")
    with _ms_lock:
        _ms_flow.clear()
        _ms_flow.update({"state": "waiting", "user_code": flow["user_code"],
                         "verification_uri": flow["verification_uri"], "expires_at": time.time() + flow.get("expires_in", 900)})

    def wait():
        c = pipeline.init_db()
        try:
            app2, cache2 = _msal_app(c)
            result = app2.acquire_token_by_device_flow(flow)
            if "access_token" in result:
                _save_cache(c, cache2)
                address = (result.get("id_token_claims") or {}).get("preferred_username", "")
                info = detect_provider(address) if "@" in address else PROVIDERS["microsoft"]
                save_account(c, {"kind": "microsoft", "provider": "microsoft", "address": address.lower(),
                                 "imap_host": "outlook.office365.com", "imap_port": 993,
                                 "smtp_host": info.get("smtp_host", "smtp.office365.com"), "smtp_port": 587,
                                 "saves_sent": True})
                _ms_flow.update({"state": "done", "address": address})
            else:
                _ms_flow.update({"state": "error", "error": result.get("error_description") or result.get("error")})
        except Exception as exc:
            _ms_flow.update({"state": "error", "error": str(exc)})
        finally:
            c.close()

    threading.Thread(target=wait, daemon=True).start()
    return microsoft_status()


def microsoft_status():
    return {k: v for k, v in _ms_flow.items() if k != "expires_at"}


def microsoft_token(conn, address):
    app, cache = _msal_app(conn)
    accounts = [a for a in app.get_accounts() if a.get("username", "").lower() == address.lower()] or app.get_accounts()
    if not accounts:
        raise RuntimeError("Microsoft sign-in expired. Sign in again in Settings → Email account.")
    result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
    _save_cache(conn, cache)
    if not result or "access_token" not in result:
        raise RuntimeError("Microsoft sign-in expired. Sign in again in Settings → Email account.")
    return result["access_token"]


def _xoauth2(address, token):
    return f"user={address}\x01auth=Bearer {token}\x01\x01"


# ---------------------------------------------------------------------------
# Account persistence
# ---------------------------------------------------------------------------

def get_account(conn):
    raw = pipeline.get_setting(conn, "mail_account", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def save_account(conn, account, password=None):
    clean = {k: account.get(k) for k in ("kind", "provider", "address", "imap_host", "imap_port", "smtp_host",
                                         "smtp_port", "saves_sent")}
    clean["address"] = (clean.get("address") or "").strip().lower()
    pipeline.set_setting(conn, "mail_account", json.dumps(clean))
    if password is not None:
        set_secret(conn, "mail_password", password)
    return clean


def remove_account(conn):
    set_secret(conn, "mail_password", "")
    conn.execute("DELETE FROM settings WHERE key IN ('mail_account', 'ms_token_cache')")
    conn.commit()


# ---------------------------------------------------------------------------
# Mailboxes
# ---------------------------------------------------------------------------

class Mailbox:
    kind = "base"
    address = ""
    supports_calendar = False

    def fetch_recent(self, max_results, is_known=lambda _id: False):
        raise NotImplementedError

    def fetch_thread(self, conn, email_item):
        return local_thread(conn, email_item)

    def send_reply(self, email_item, text, from_name=""):
        raise NotImplementedError

    def archive(self, email_item):
        raise NotImplementedError

    def trash(self, email_item):
        raise NotImplementedError

    def sent_samples(self, limit=30):
        return []

    def close(self):
        pass


local_thread = pipeline.local_thread


class GmailApiMailbox(Mailbox):
    kind = "gmail_api"
    supports_calendar = True

    def __init__(self, service, address=""):
        self.service = service
        self.address = address

    def fetch_recent(self, max_results, is_known=lambda _id: False):
        listing = self.service.users().messages().list(userId="me", labelIds=["INBOX"],
                                                       maxResults=max_results).execute()
        out = []
        for meta in listing.get("messages", []):
            if is_known(meta["id"]):
                continue
            try:
                msg = self.service.users().messages().get(userId="me", id=meta["id"], format="full").execute()
                out.append(pipeline._parse_message(msg))
            except Exception:
                continue
        return out

    def fetch_thread(self, conn, email_item):
        return pipeline.fetch_email_thread(self.service, email_item.get("thread_id"), email_item.get("id")) \
            or local_thread(conn, email_item)

    def refresh(self, email_item):
        msg = self.service.users().messages().get(userId="me", id=email_item["id"], format="full").execute()
        return pipeline._parse_message(msg)

    def send_reply(self, email_item, text, from_name=""):
        return pipeline.send_reply(self.service, email_item, text)

    def archive(self, email_item):
        pipeline.archive_in_gmail(self.service, email_item["id"])

    def trash(self, email_item):
        pipeline.trash_in_gmail(self.service, email_item["id"])

    def sent_samples(self, limit=30):
        texts = []
        listing = self.service.users().messages().list(userId="me", q="in:sent", maxResults=limit).execute()
        for meta in listing.get("messages", []):
            try:
                msg = self.service.users().messages().get(userId="me", id=meta["id"], format="full").execute()
                body = pipeline.clean_email_body(pipeline._extract_body(msg.get("payload", {})))
                if len(body) > 30:
                    texts.append(body)
            except Exception:
                continue
        return texts


_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')


def _quote(name):
    name = name.strip()
    if name.startswith('"') and name.endswith('"'):
        return name
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _attachment_names(msg):
    names = []
    try:
        for part in msg.iter_attachments():
            if part.get_filename():
                names.append(str(part.get_filename()))
    except Exception:
        pass
    return names[:20]


def parse_raw_message(raw, uid_key):
    msg = email.message_from_bytes(raw, policy=default_policy)
    body = ""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            content = part.get_content()
            body = pipeline._html_to_text(content) if part.get_content_type() == "text/html" else content
    except Exception:
        payload = msg.get_payload(decode=True) or b""
        body = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    received = None
    try:
        received = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
    except Exception:
        received = pipeline._iso()
    message_id = (msg.get("Message-ID") or "").strip()
    references = " ".join((msg.get("References") or "").split())
    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    root = (references.split()[0] if references else "") or in_reply_to or message_id or uid_key
    return {
        "id": uid_key, "thread_id": f"imap-thread:{root}", "message_id": message_id or None,
        "references": references or None, "sender": str(msg.get("From") or "unknown"), "to": str(msg.get("To") or ""),
        "reply_to": str(msg.get("Reply-To") or "") or None, "attachments": _attachment_names(msg),
        "subject": str(msg.get("Subject") or "(no subject)"), "body": pipeline.clean_email_body(body),
        "received_at": received, "list_unsubscribe": msg.get("List-Unsubscribe"),
        "precedence": msg.get("Precedence"), "auto_submitted": msg.get("Auto-Submitted"), "label_ids": [],
    }


class ImapMailbox(Mailbox):
    kind = "imap"

    def __init__(self, conn, account, password=None, token=None):
        self.account = account
        self.address = account["address"]
        self.provider = account.get("provider", "other")
        self._password = password
        self._token = token
        self._imap = None
        self._folders = None
        self._db = conn

    # -- connection helpers --
    def _auth_imap(self, imap):
        if self.account.get("kind") == "microsoft":
            token = self._token or microsoft_token(self._db, self.address)
            imap.authenticate("XOAUTH2", lambda _: _xoauth2(self.address, token).encode())
        else:
            imap.login(self.address, self._password or get_secret(self._db, "mail_password"))

    def imap(self):
        if self._imap is None:
            host, port = self.account["imap_host"], int(self.account.get("imap_port") or 993)
            imap = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)
            try:
                self._auth_imap(imap)
            except Exception:
                try:
                    imap.logout()
                except Exception:
                    pass
                raise
            self._imap = imap
        return self._imap

    def close(self):
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    def _select_inbox(self, readonly=True):
        typ, _ = self.imap().select("INBOX", readonly=readonly)
        if typ != "OK":
            raise RuntimeError("Couldn't open the INBOX folder.")
        typ, data = self.imap().response("UIDVALIDITY")
        return (data[0].decode() if data and data[0] else "0")

    def folders(self):
        if self._folders is None:
            self._folders = {}
            typ, data = self.imap().list()
            for line in data or []:
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)
                m = _LIST_RE.match(text)
                if not m:
                    continue
                flags, name = m.group("flags"), m.group("name").strip()
                for special in ("\\Sent", "\\Trash", "\\Archive", "\\All", "\\Drafts"):
                    if special.lower() in flags.lower():
                        self._folders.setdefault(special, name)
                self._folders.setdefault("names", []).append(name.strip('"'))
        return self._folders

    def _folder(self, special, fallbacks):
        found = self.folders().get(special)
        if found:
            return found
        names = self.folders().get("names", [])
        for fb in fallbacks:
            if fb in names:
                return fb
        return None

    # -- interface --
    def test(self):
        self._select_inbox()
        self.folders()
        smtp = self._smtp()
        smtp.quit()
        return True

    def fetch_recent(self, max_results, is_known=lambda _id: False):
        validity = self._select_inbox(readonly=True)
        since = (datetime.now() - timedelta(days=14)).strftime("%d-%b-%Y")
        typ, data = self.imap().uid("SEARCH", None, f"SINCE {since}")
        uids = (data[0].split() if data and data[0] else [])[-max_results:]
        out = []
        for uid in reversed(uids):
            uid = uid.decode()
            key = f"imap:{validity}:{uid}"
            if is_known(key):
                continue
            typ, parts = self.imap().uid("FETCH", uid, "(BODY.PEEK[])")  # PEEK: never marks mail as read
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if not raw:
                continue
            item = parse_raw_message(raw, key)
            if pipeline.sender_address(item["sender"]) == self.address:
                continue
            out.append(item)
        return out

    def _uid_for(self, email_item):
        """Current UID for a stored message; falls back to Message-ID search if the folder was re-indexed."""
        validity = self._select_inbox(readonly=False)
        parts = (email_item.get("id") or "").split(":")
        if len(parts) == 3 and parts[1] == validity:
            return parts[2]
        if email_item.get("message_id"):
            typ, data = self.imap().uid("SEARCH", None, "HEADER", "Message-ID", f'"{email_item["message_id"]}"')
            found = data[0].split() if data and data[0] else []
            if found:
                return found[-1].decode()
        raise RuntimeError("This email is no longer in your inbox on the server.")

    def _move(self, uid, target):
        imap = self.imap()
        if b"MOVE" in b" ".join(c if isinstance(c, bytes) else c.encode() for c in imap.capabilities):
            typ, data = imap.uid("MOVE", uid, _quote(target))
            if typ == "OK":
                return
        typ, _ = imap.uid("COPY", uid, _quote(target))
        if typ != "OK":
            raise RuntimeError(f"Couldn't copy the message to {target}.")
        imap.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        imap.expunge()

    def archive(self, email_item):
        uid = self._uid_for(email_item)
        if self.provider == "gmail":
            target = self._folder("\\All", ["[Gmail]/All Mail", "[Google Mail]/All Mail"])
        else:
            target = self._folder("\\Archive", ["Archive", "Archives", "INBOX.Archive", "INBOX/Archive"])
            if not target:
                self.imap().create("Archive")
                self._folders = None
                target = "Archive"
        self._move(uid, target)

    def trash(self, email_item):
        uid = self._uid_for(email_item)
        target = self._folder("\\Trash", ["Trash", "Deleted Items", "Deleted Messages", "INBOX.Trash",
                                          "[Gmail]/Trash", "[Gmail]/Bin"])
        if not target:
            raise RuntimeError("Couldn't find a Trash folder on your mail server.")
        self._move(uid, target)

    def _smtp(self):
        host, port = self.account["smtp_host"], int(self.account.get("smtp_port") or 465)
        context = ssl.create_default_context()
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, context=context, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
            smtp.ehlo()
            smtp.starttls(context=context)
        smtp.ehlo()
        if self.account.get("kind") == "microsoft":
            token = self._token or microsoft_token(self._db, self.address)
            code, resp = smtp.docmd("AUTH", "XOAUTH2 " + base64.b64encode(_xoauth2(self.address, token).encode()).decode())
            if code != 235:
                smtp.close()
                raise smtplib.SMTPAuthenticationError(code, resp)
        else:
            smtp.login(self.address, self._password or get_secret(self._db, "mail_password"))
        return smtp

    def build_reply(self, email_item, text, from_name=""):
        msg = EmailMessage()
        to_addr = parseaddr(email_item.get("sender", ""))[1]
        if not to_addr:
            raise ValueError(f"Could not find a reply address in: {email_item.get('sender')!r}")
        subject = email_item.get("subject") or ""
        msg["From"] = formataddr((from_name, self.address)) if from_name else self.address
        msg["To"] = to_addr
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.address.split("@")[-1])
        if email_item.get("message_id"):
            msg["In-Reply-To"] = email_item["message_id"]
            msg["References"] = f"{email_item.get('references') or ''} {email_item['message_id']}".strip()
        msg.set_content(text)
        return msg

    def send_reply(self, email_item, text, from_name=""):
        msg = self.build_reply(email_item, text, from_name)
        smtp = self._smtp()
        try:
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                pass
        if not self.account.get("saves_sent"):
            try:  # keep a copy in Sent so the user's other mail apps see it
                sent = self._folder("\\Sent", ["Sent", "Sent Items", "Sent Messages", "INBOX.Sent"])
                if sent:
                    self.imap().append(_quote(sent), "(\\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            except Exception:
                pass
        return msg["Message-ID"]

    def sent_samples(self, limit=30):
        sent = self._folder("\\Sent", ["Sent", "Sent Items", "Sent Messages", "[Gmail]/Sent Mail", "INBOX.Sent"])
        if not sent:
            return []
        typ, _ = self.imap().select(_quote(sent), readonly=True)
        if typ != "OK":
            return []
        typ, data = self.imap().uid("SEARCH", None, "ALL")
        texts = []
        for uid in (data[0].split() if data and data[0] else [])[-limit:]:
            typ, parts = self.imap().uid("FETCH", uid.decode(), "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple) and len(p) > 1), None)
            if raw:
                body = parse_raw_message(raw, "sent")["body"]
                if len(body) > 30:
                    texts.append(body)
        return texts


def open_mailbox(conn, gmail_service_factory=None):
    """The active mailbox: an email-password / Microsoft account if set up, else Gmail API, else None."""
    account = get_account(conn)
    if account:
        return ImapMailbox(conn, account)
    if gmail_service_factory:
        service = gmail_service_factory()
        if service:
            return GmailApiMailbox(service)
    return None


def connect_with_password(conn, address, password, imap_host=None, imap_port=None, smtp_host=None, smtp_port=None):
    info = detect_provider(address)
    if info["method"] == "microsoft":
        raise ValueError("Outlook and Microsoft 365 no longer accept passwords from apps. Use “Sign in with Microsoft”.")
    password = (password or "").replace(" ", "") if info["provider"] == "gmail" else (password or "")
    if not password:
        raise ValueError("Enter the app password.")
    account = {"kind": "imap", "provider": info["provider"], "address": address.strip().lower(),
               "imap_host": (imap_host or info["imap_host"]).strip(), "imap_port": int(imap_port or info["imap_port"]),
               "smtp_host": (smtp_host or info["smtp_host"]).strip(), "smtp_port": int(smtp_port or info["smtp_port"]),
               "saves_sent": PROVIDERS[info["provider"]]["saves_sent"]}
    box = ImapMailbox(conn, account, password=password)
    try:
        box.test()
    except Exception as exc:
        raise ValueError(friendly_mail_error(exc, info["provider"])) from exc
    finally:
        box.close()
    return save_account(conn, account, password=password)

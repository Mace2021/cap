"""Fraud & impersonation shield: local, explainable checks for the email attacks small offices actually face.

Business email compromise (payment and bank-detail changes), lookalike sender domains, reply-to redirection,
fake IRS or e-Services messages, credential phishing, risky attachments and "new client" lures. Nothing here
calls the network: every signal is computed from the email and the office's own mail history, and every flag
says why it was raised and what to do. It is a second pair of eyes, not a guarantee.
"""

import re
from email.utils import getaddresses, parseaddr

FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
            "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "zoho.com", "example.com"}
# Domains attackers like to imitate, checked even if the office has never received mail from them.
IMPERSONATED = {"irs.gov", "treasury.gov", "ssa.gov", "gmail.com", "outlook.com", "microsoft.com", "office.com",
                "google.com", "intuit.com", "docusign.com", "dropbox.com", "paypal.com", "chase.com",
                "bankofamerica.com", "wellsfargo.com", "amazon.com", "apple.com", "yahoo.com"}
GOV_DOMAINS = ("irs.gov", "treasury.gov", "ssa.gov")
RISKY_EXT = {".zip", ".rar", ".7z", ".exe", ".js", ".vbs", ".scr", ".bat", ".cmd", ".iso", ".img", ".htm",
             ".html", ".hta", ".lnk", ".docm", ".xlsm", ".pptm", ".one", ".svg"}

_LINK = re.compile(r"https?://([^\s/<>\"')]+)", re.I)
_PAYMENT_CHANGE = [
    re.compile(r"\b(chang\w*|updat\w*|new|switch\w*)\s+(?:to\s+)?(?:our|my|the)?\s*(?:bank(?:ing)?|account|wire|"
               r"payment|remittance|deposit|routing)\s*(?:details|info\w*|instructions|account|number)", re.I),
    re.compile(r"\b(send|wire|transfer|deposit|pay|direct)\w*\s+(?:the\s+|my\s+|our\s+)?(?:refund|payment|funds|money|"
               r"claim (?:payment|check)|premium|balance|invoice)[^.\n]{0,50}\b(?:to|into)\s+(?:my|our|this|a|the)\s+"
               r"(?:new|different|other|updated)\b", re.I),
    re.compile(r"\b(?:new|different|updated)\s+(?:bank|banking)\s+(?:account|details|info\w*)", re.I),
]
_GIFT = re.compile(r"\bgift ?cards?\b|\b(?:itunes|google play|steam) cards?\b", re.I)
_MONEY = re.compile(r"\b(?:wire(?:d| transfer| the)?|ach transfer|zelle|venmo|cash ?app|bitcoin|crypto\w*|"
                    r"western union|money ?gram|bank transfer)\b", re.I)
_URGENT = re.compile(r"\b(?:urgent\w*|asap|immediately|right away|right now|today|within the hour|end of (?:the )?day|"
                     r"time.sensitive|final notice|last chance)\b", re.I)
_SECRECY = re.compile(r"keep (?:this|it) (?:between us|confidential|quiet)|don'?t (?:tell|mention|call)|"
                      r"(?:i'?m|i am) (?:in a meeting|unavailable|travel\w*|on a flight)|can'?t (?:talk|take calls)|"
                      r"(?:email|text) only", re.I)
_NEW_CONTACT = re.compile(r"\b(?:my|our) (?:new|updated|personal) (?:email|e-mail|phone|cell|number|address)\b", re.I)
_CREDENTIAL = re.compile(r"\b(?:verify|confirm|validate|update|re-?enter|reactivate)\s+(?:your\s+)?(?:account|password|"
                         r"log ?in|credentials|identity|e-?services|efin|ptin|mailbox)\b|\b(?:account|access|mailbox)\s+"
                         r"(?:will be|has been|is)\s+(?:suspended|locked|disabled|closed|limited|deactivated)\b|"
                         r"\b(?:log|sign) ?in\s+(?:here|now|below|to (?:view|review|access|download|restore))\b|"
                         r"\bpassword (?:expires|expired|will expire)\b", re.I)
_MACRO = re.compile(r"\benable (?:content|editing|macros)\b", re.I)
_LURE = re.compile(r"\b(?:new client|prospective client|need (?:a |an )?(?:tax|accountant|preparer|cpa|quote|policy)|"
                   r"my (?:tax )?documents|w-?2|1099|tax return|quote|policy|claim|invoice|statement)\b", re.I)
_IRS_NAME = re.compile(r"\b(?:irs|internal revenue|treasury|e-?services|social security administration)\b", re.I)

_HOMOGLYPH = str.maketrans({"0": "o", "1": "l", "i": "l", "3": "e", "5": "s", "4": "a", "q": "g", "@": "a"})

ADVICE = {
    "payment": "Don't change payment details or send money because of this email. Call the person at a phone "
               "number you already have on file, not one in this email, and confirm before acting.",
    "credential": "Don't click links or enter passwords from this email. Go to the site by typing its address "
                  "yourself.",
    "irs": "The IRS doesn't start contact by email about your account or ask for passwords by email. Don't click "
           "anything; forward it as an attachment to phishing@irs.gov and delete it.",
    "attachment": "Don't open the attachment or link until you've confirmed with the sender by phone.",
    "lookalike": "Check the sender's address letter by letter. Reply only to the address you already know.",
}


def _domain(address):
    return address.rsplit("@", 1)[-1].lower().strip(">") if "@" in (address or "") else ""


def _skeleton(domain):
    d = domain.lower().replace("rn", "m").replace("vv", "w").replace("cl", "d").replace("-", "")
    return d.translate(_HOMOGLYPH)


def _distance(a, b):
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def lookalike_of(domain, trusted):
    """The trusted domain this one imitates, or None. Exact matches are not lookalikes."""
    domain = (domain or "").lower()
    if not domain or domain in trusted:
        return None
    label = domain.split(".")[0]
    for t in sorted(trusted):
        t_label = t.split(".")[0]
        # brand stuffed into another domain: irs-gov-secure.com, ssa.gov.verify-id.net
        if t in GOV_DOMAINS and t_label in re.split(r"[.\-]", domain) and not domain.endswith("." + t):
            return t
        if len(t_label) < 5:
            continue
        if _skeleton(domain) == _skeleton(t):
            return t
        if domain.split(".")[1:] == t.split(".")[1:] and _distance(label, t_label) == 1:
            return t
    return None


class Contacts:
    """What the office's mail history says about who it already knows."""

    def __init__(self, counts=None, names=None):
        self.counts = dict(counts or {})          # address -> number of emails seen from it
        self.names = dict(names or {})            # lower display name -> set of addresses

    def seen(self, address, self_count=0):
        return self.counts.get(address, 0) - self_count > 0

    def domains(self, exclude_address=None, self_count=0):
        out = set()
        for addr, n in self.counts.items():
            if n - (self_count if addr == exclude_address else 0) > 0:
                d = _domain(addr)
                if d and d not in FREEMAIL:
                    out.add(d)
        return out


def assess(email, contacts=None, self_count=0):
    """Return {"level": "high"|"medium"|None, "score", "signals": [{code,label}], "advice": [..]} for one email.

    self_count = how many of this sender's emails in `contacts` are this very email (1 if it is already logged)."""
    contacts = contacts or Contacts()
    sender = email.get("sender") or ""
    name, address = parseaddr(sender)
    address = address.lower()
    domain = _domain(address)
    body = email.get("body") or ""
    text = f"{email.get('subject') or ''}\n{body}"
    attachments = [a for a in (email.get("attachments") or []) if a]
    links = [m.group(1).lower().rstrip(".,;") for m in _LINK.finditer(body)]
    signals, advice, score = [], [], 0

    def add(code, label, weight, tip=None):
        nonlocal score
        signals.append({"code": code, "label": label})
        score += weight
        if tip and ADVICE[tip] not in advice:
            advice.append(ADVICE[tip])

    known_sender = contacts.seen(address, self_count)
    trusted = contacts.domains(address, self_count) | IMPERSONATED

    # Who is it really from?
    imitated = lookalike_of(domain, trusted)
    if imitated:
        add("lookalike", f"The sender's domain ({domain}) looks like {imitated} but isn't.", 3, "lookalike")
    if name and not known_sender:
        same_name = {a for a in contacts.names.get(name.strip().lower(), set()) if a != address}
        if same_name:
            add("name-spoof", f"Uses the name of a known contact ({name.strip()}) from a new address.", 2, "lookalike")
    embedded = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", name or "")
    if embedded and embedded[0].lower() != address:
        add("display-address", "The display name shows a different email address than the real sender.", 2, "lookalike")
    reply_to = [a.lower() for _, a in getaddresses([email.get("reply_to") or ""]) if a]
    if reply_to and all(_domain(r) != domain for r in reply_to):
        add("reply-to", f"Replies would go to a different address ({reply_to[0]}).", 2, "lookalike")
    if _IRS_NAME.search(f"{name} {address}") and not domain.endswith(GOV_DOMAINS):
        add("irs", "Claims to be the IRS or a government agency, but doesn't come from a .gov address.", 3, "irs")

    # What does it want?
    if any(p.search(text) for p in _PAYMENT_CHANGE):
        add("payment-change", "Asks to change bank or payment details, or to send money somewhere new.", 3, "payment")
    if _GIFT.search(text):
        add("gift-cards", "Mentions gift cards, a classic scam payment method.", 3, "payment")
    elif _MONEY.search(text) and _URGENT.search(text):
        add("urgent-money", "Pushes for a payment or transfer with urgency.", 2, "payment")
    if _SECRECY.search(text):
        add("secrecy", "Asks for secrecy or says the sender can't be reached by phone.", 1)
    if _NEW_CONTACT.search(text) and known_sender is False:
        add("new-contact", "Says it's from a new email address or phone number.", 1, "lookalike")
    if _CREDENTIAL.search(text):
        add("credential", "Asks you to sign in, verify an account or update a password.", 2 + (1 if links else 0),
            "credential")
    for link in links:
        target = lookalike_of(link, trusted)
        if target:
            add("link-lookalike", f"Contains a link to {link}, which imitates {target}.", 3, "credential")
            break
    risky = [a for a in attachments if any(a.lower().endswith(ext) for ext in RISKY_EXT)]
    if risky:
        add("attachment", f"Has a risky attachment type ({risky[0]}).", 2, "attachment")
    if _MACRO.search(text):
        add("macros", "Asks you to enable content or macros.", 2, "attachment")
    if not known_sender and (attachments or links) and _LURE.search(text) and not risky:
        add("first-contact", "First email from this sender, and it carries a link or attachment.", 1, "attachment")

    strong = any(s["code"] not in ("secrecy", "new-contact", "first-contact") for s in signals)
    level = "high" if score >= 3 and strong else ("medium" if score >= 2 and strong else None)
    return {"level": level, "score": score, "signals": signals if level else [], "advice": advice if level else []}


def prompt_warning(result):
    """Instruction for the drafting model when an email looks fraudulent."""
    if not result or not result.get("level"):
        return ""
    reasons = "; ".join(s["label"] for s in result["signals"])
    return ("FRAUD WARNING: this email shows signs of fraud or impersonation (" + reasons + "). Do NOT agree to "
            "change payment or bank details, send money, click links, open attachments or share any information. "
            "If a reply is needed at all, keep it short, say you will confirm by phone using the contact details "
            "already on file, and set confidence below 0.3.")

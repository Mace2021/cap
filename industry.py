"""Industry starter kits and the sensitive-number guard.

Starter kits give a tax/bookkeeping firm or an insurance agency a ready set of saved replies for the questions
they answer every day, safe-language rules for their profile, a checklist for Knowledge, and guidance that tells
the AI how this kind of office sorts and answers mail. Installing a kit never overwrites anything the user wrote.

Templates avoid firm-specific numbers on purpose; "___" marks the few blanks to fill in before sending.
"""

import re

# ---------------------------------------------------------------------------
# Starter kits
# ---------------------------------------------------------------------------

TAX_GUIDANCE = """This is a tax preparation and bookkeeping firm.
- Urgent: IRS or state notices (CP2000, CP14, audit or balance-due letters, especially with a response date), filing or payment deadlines within two weeks, rejected e-files, identity theft, and anything blocking a return from being filed.
- Never give tax advice, opinions or calculations by email, and never state how much a client owes or will get back, or when a refund will arrive.
- Never ask for, repeat or confirm Social Security numbers, bank account numbers or passwords in a reply. Point the client to the secure upload method in the Business Profile.
- Don't confirm documents were received unless the email or thread shows it; say you'll check and confirm.
- An extension gives more time to file, not more time to pay.
- Use confidence below 0.5 for IRS or state notices, audits, amended returns, penalties, identity theft and fee disputes."""

TAX_SORT_GUIDANCE = ("Tax/bookkeeping firm: IRS or state notices, deadlines within two weeks, rejected e-files and identity "
                     "theft are URGENT. Client questions about returns, documents, fees or bookkeeping need a reply.")

INSURANCE_GUIDANCE = """This is an independent insurance agency.
- Urgent: claims, accidents and losses, certificate of insurance requests with a deadline, home closings or lender proof-of-insurance deadlines, cancellation or non-payment notices, and any lapse in coverage.
- Never say coverage is bound, in force, added or changed until the carrier has confirmed it in writing. Say you've requested it and will confirm.
- Never promise a claim will be paid, or that rates will or won't change. Never quote a premium that isn't from a carrier quote in the thread or Knowledge.
- Never ask for driver's license numbers, Social Security numbers or bank details by email. Point the client to the secure method in the Business Profile.
- For new quotes, list exactly what information you need.
- Use confidence below 0.5 for claims, coverage questions, cancellations, complaints and anything involving a lapse."""

INSURANCE_SORT_GUIDANCE = ("Insurance agency: claims and accidents, certificate requests with deadlines, closing-date "
                           "proof of insurance and cancellation or non-payment notices are URGENT. Quote, policy change "
                           "and renewal questions need a reply.")

TAX_TEMPLATES = [
    ("Welcome — new client next steps",
     "Hi {first_name},\n\nThank you for choosing {business_name}. Here's how we get started:\n\n"
     "1. We'll send you our engagement letter to sign.\n"
     "2. Upload your documents through our secure client portal. Please don't send tax documents or Social "
     "Security numbers by email.\n"
     "3. We'll review everything and let you know if anything is missing.\n\n"
     "I've attached our document checklist so you can see what to gather.\n\n{my_name}"),
    ("Missing documents reminder",
     "Hi {first_name},\n\nThanks for sending your documents. To finish your return we still need:\n\n- ___\n- ___\n\n"
     "Please upload them through our secure client portal rather than by email. We prepare returns in the order "
     "complete documents arrive, so the sooner we have these, the sooner we can finish.\n\n{my_name}"),
    ("Documents received — we'll review",
     "Hi {first_name},\n\nThank you, we've received your documents. We'll review them and let you know if "
     "anything else is needed.\n\n{my_name}"),
    ("Please use the secure portal",
     "Hi {first_name},\n\nTo protect your information, please don't send Social Security numbers, bank account "
     "numbers or tax documents by email. Please upload them through our secure client portal instead.\n\n"
     "If you already sent them by email, you may want to delete that message from your Sent folder.\n\n{my_name}"),
    ("Return ready for review and signature",
     "Hi {first_name},\n\nGood news: your return is ready for your review. Please look it over in the secure "
     "portal and sign the e-file authorization. Once it's signed and the invoice is paid, we'll file it and send "
     "you confirmation.\n\nLet me know if you have any questions.\n\n{my_name}"),
    ("Return filed",
     "Hi {first_name},\n\nYour return has been filed. We'll let you know once it's accepted. A copy is "
     "available in your secure portal for your records.\n\n{my_name}"),
    ("Status update — return in progress",
     "Hi {first_name},\n\nThanks for checking in. Your return is in progress, and we'll contact you as soon as "
     "it's ready for your review or if we need anything else from you.\n\n{my_name}"),
    ("Extension filed",
     "Hi {first_name},\n\nWe've filed your extension. Please note that an extension gives you more time to "
     "file, not more time to pay. Any tax owed is still due by the original deadline, so let's talk about an "
     "estimated payment if you expect to owe.\n\n{my_name}"),
    ("IRS or state letter received",
     "Hi {first_name},\n\nThanks for letting us know. Please don't pay anything or call the number on the "
     "letter until we've reviewed it. Upload a copy of the full letter through our secure portal and let us "
     "know the response date printed on it. We'll review it and get back to you with next steps.\n\n{my_name}"),
    ("Where's my refund?",
     "Hi {first_name},\n\nOnce a return is accepted, the IRS \"Where's My Refund?\" tool is the quickest way "
     "to check the status of a federal refund: https://www.irs.gov/refunds\n\nWe aren't able to speed up or "
     "predict the IRS's timing, but let us know if the tool shows a problem and we'll take a look.\n\n{my_name}"),
    ("Estimated tax payment reminder",
     "Hi {first_name},\n\nA friendly reminder that your next estimated tax payment is due on ___. Let us know "
     "if your income has changed and you'd like us to revisit the amount.\n\n{my_name}"),
    ("Engagement letter — please sign",
     "Hi {first_name},\n\nI've sent our engagement letter for this year's work. Please review and sign it when "
     "you can; we'll start as soon as it's back and your documents are uploaded.\n\n{my_name}"),
    ("Bookkeeping — statements needed for month-end",
     "Hi {first_name},\n\nWe're closing your books for ___ and need the following to finish:\n\n- ___\n\n"
     "Please upload them through the secure portal. We'll send your reports once the month is closed.\n\n{my_name}"),
    ("Invoice reminder",
     "Hi {first_name},\n\nA friendly reminder that your invoice from {business_name} is still open. Let me know "
     "if you have any questions about it.\n\n{my_name}"),
]

INSURANCE_TEMPLATES = [
    ("Auto quote — information needed",
     "Hi {first_name},\n\nThanks for the chance to quote your auto insurance. We'll compare several carriers. "
     "To get started, please send:\n\n- Year, make and model of each vehicle\n- Each driver's name and date of "
     "birth\n- Your current carrier and renewal date\n- Any accidents or tickets in the last 5 years\n\n"
     "Please don't email driver's license numbers; send those through our secure upload link or by phone.\n\n{my_name}"),
    ("Home quote — information needed",
     "Hi {first_name},\n\nHappy to quote your home insurance. Please send:\n\n- Property address, year built "
     "and square footage\n- Roof age and type\n- Any claims in the last 5 years\n- For a purchase: closing date, "
     "lender name and mortgagee clause\n\nWe'll compare several carriers and get back to you.\n\n{my_name}"),
    ("Quote ready — review options",
     "Hi {first_name},\n\nYour quotes are ready. I've included the options side by side so you can compare "
     "coverage and price. Coverage starts only once the carrier confirms it in writing, so let me know which "
     "option you'd like and I'll take care of the rest.\n\n{my_name}"),
    ("Certificate of insurance — details needed",
     "Hi {first_name},\n\nHappy to issue that certificate. Please send:\n\n1. The certificate holder's full legal "
     "name and mailing address\n2. Any special wording, project details or additional insured requirements\n\n"
     "If they need to be listed as additional insured, we'll confirm with the carrier that your policy includes "
     "it.\n\n{my_name}"),
    ("Certificate of insurance — attached",
     "Hi {first_name},\n\nThe certificate of insurance you requested is attached. Let me know if the certificate "
     "holder needs anything else.\n\n{my_name}"),
    ("Claim — first steps",
     "Hi {first_name},\n\nI'm sorry this happened. Here's what to do next:\n\n1. Make sure everyone is safe.\n"
     "2. Take photos and note the date, time and location.\n3. Report the claim to your carrier's 24/7 claims "
     "line (on your ID card or policy), or call us and we'll open it with you.\n\nWhether it's covered depends on "
     "your policy and the carrier's review, and we'll help you through it.\n\n{my_name}"),
    ("Claim — checking in",
     "Hi {first_name},\n\nI wanted to check in on your claim. Has the adjuster been in touch? Let me know if "
     "anything is holding things up and I'll follow up with the carrier.\n\n{my_name}"),
    ("Policy change requested — pending carrier",
     "Hi {first_name},\n\nWe've received your change request and sent it to the carrier. The change takes effect "
     "once the carrier confirms it in writing, and we'll send you that confirmation.\n\n{my_name}"),
    ("Add a driver",
     "Hi {first_name},\n\nWe can add them to your policy. Please send their full name and date of birth, and "
     "which vehicle they'll drive most. For their driver's license number, please use our secure upload link or "
     "call us rather than email. The change takes effect once the carrier confirms it.\n\n{my_name}"),
    ("Renewal increase — we'll shop it",
     "Hi {first_name},\n\nI understand, and thanks for reaching out. As an independent agency we can compare your "
     "coverage with other carriers. I'll review what changed at renewal and come back to you with options. If "
     "anything about your home or vehicles has changed, let me know since it can affect the price.\n\n{my_name}"),
    ("Cancellation — signed request needed",
     "Hi {first_name},\n\nTo cancel, we need a signed cancellation request with the date you'd like coverage to "
     "end. I'll send the form. Any refund of unused premium comes from the carrier.\n\nBefore you cancel, please "
     "make sure any replacement coverage is in place so there's no gap.\n\n{my_name}"),
    ("Payment due — avoid a lapse",
     "Hi {first_name},\n\nA quick heads-up: the carrier shows a payment due on your policy. To avoid a lapse in "
     "coverage, please make the payment through the carrier's website or phone line by the due date. Let me know "
     "if you have any trouble.\n\n{my_name}"),
    ("Proof of insurance / ID cards",
     "Hi {first_name},\n\nYour proof of insurance is attached. You can also find your ID cards in the carrier's "
     "app or website.\n\n{my_name}"),
    ("Annual policy review",
     "Hi {first_name},\n\nIt's been about a year since we last reviewed your coverage. A quick review helps make "
     "sure your limits still fit and that you're getting every discount you qualify for. Would you like to set up "
     "a 15-minute call?\n\n{my_name}"),
]

TAX_CHECKLIST = ("Tax return document checklist.txt",
    "TAX RETURN DOCUMENT CHECKLIST\n\nClients upload these through the secure client portal, never by email.\n\n"
    "Everyone:\n- Last year's tax return (new clients)\n- Photo ID for each taxpayer\n- W-2s from every employer\n"
    "- 1099-INT and 1099-DIV (interest and dividends)\n- 1099-B (sales of stocks, funds or crypto)\n"
    "- 1099-R (retirement distributions) and SSA-1099 (Social Security benefits)\n"
    "- 1098 (mortgage interest) and property tax statements\n- 1098-T (tuition) and 1098-E (student loan interest)\n"
    "- 1095-A if anyone had Marketplace health insurance\n- Estimated tax payments made, with dates and amounts\n"
    "- Childcare provider name, address, tax ID and amount paid\n- Charitable donation receipts\n\n"
    "Self-employed (Schedule C):\n- 1099-NEC and 1099-K forms\n- Income and expense records or bookkeeping P&L\n"
    "- Business mileage log\n- Home office square footage, if used\n\n"
    "Rental property (Schedule E):\n- Rent received\n- Property tax, insurance, repairs, HOA and management fees\n"
    "- Purchase date and price for properties bought this year")

INSURANCE_CHECKLIST = ("Insurance quote information checklist.txt",
    "INSURANCE QUOTE INFORMATION CHECKLIST\n\nDriver's license and Social Security numbers come by phone or secure "
    "upload, never by email.\n\n"
    "Auto: each driver's name and date of birth; license details (secure method); year, make and model of each "
    "vehicle; current carrier and renewal date; accidents or tickets in the last 5 years.\n\n"
    "Home: address, year built, square footage, roof age and type, heating and electrical updates, claims in the "
    "last 5 years, lender name and mortgagee clause, closing date for purchases.\n\n"
    "Renters: address, value of belongings, roommates.\n\n"
    "Small business: business type, years in business, annual revenue and payroll, number of employees, current "
    "policies and renewal dates, contract insurance requirements.\n\n"
    "Certificates of insurance: certificate holder's full legal name and mailing address, project details, any "
    "additional insured or special wording requirements.")

TAX_NEVER_SAY = ("Never give tax advice or tell a client how much they owe or will get back by email. Never promise "
                 "a refund amount or date. Never ask for or repeat Social Security numbers, bank account numbers or "
                 "passwords in email; point clients to the secure portal.")

INSURANCE_NEVER_SAY = ("Never say coverage is bound, in force or changed until the carrier confirms it in writing. "
                       "Never promise a claim will be paid or that rates won't go up. Never quote a premium without a "
                       "carrier quote. Never ask for driver's license or Social Security numbers by email.")

KITS = {
    "tax": {"label": "Tax & bookkeeping", "guidance": TAX_GUIDANCE, "sort_guidance": TAX_SORT_GUIDANCE,
            "templates": TAX_TEMPLATES, "checklist": TAX_CHECKLIST, "never_say": TAX_NEVER_SAY},
    "insurance": {"label": "Insurance agency", "guidance": INSURANCE_GUIDANCE,
                  "sort_guidance": INSURANCE_SORT_GUIDANCE, "templates": INSURANCE_TEMPLATES,
                  "checklist": INSURANCE_CHECKLIST, "never_say": INSURANCE_NEVER_SAY},
}

# ---------------------------------------------------------------------------
# Sensitive-number guard
# ---------------------------------------------------------------------------

_SSN = re.compile(r"(?<![\d-])(?!000|666)(\d{3})[- ](?!00)(\d{2})[- ](?!0000)(\d{4})(?![\d-])")
_SSN_LABELED = re.compile(r"(?i)\b(?:ssn|social security(?: number| no\.?| #)?|soc\.? sec\.?|itin|tin)\b[^\d\n]{0,20}"
                          r"(?<!\d)(\d{9})(?!\d)")
_ROUTING = re.compile(r"(?i)\b(?:routing|aba|rtn)\b(?: number| no\.?| #)?[^\d\n]{0,20}(?<!\d)(\d{9})(?!\d)")
_ACCOUNT = re.compile(r"(?i)\b(?:account|acct|a/c)\b(?: number| no\.?| #)?[^\d\n]{0,20}(?<!\d)(\d[\d -]{4,22}\d)(?!\d)")
_CARD = re.compile(r"(?<!\d)([2-6](?:[ -]?\d){12,18})(?!\d)")
_LICENSE = re.compile(r"(?i)\b(?:(?:driver'?s?|drivers)\s+licen[cs]e|DL)\b[^\n]{0,25}?"
                      r"\b((?=[A-Z0-9-]*\d[A-Z0-9-]*\d[A-Z0-9-]*\d[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{4,16})\b")
_PASSWORD = re.compile(r"(?i)\b(?:password|passcode|pin)\b\s*(?:is|:|=)\s*(\S{4,})")

KIND_LABELS = {"ssn": "Social Security or tax ID number", "routing": "bank routing number",
               "account": "bank account number", "card": "payment card number",
               "license": "driver's license number", "password": "password"}


def _luhn_ok(digits):
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask(value):
    raw = re.sub(r"[\s-]", "", value)
    return "•" * max(len(raw) - 4, 3) + raw[-4:] if len(raw) > 4 else "••••"


def find_sensitive(text):
    """Return what looks like sensitive numbers in text, masked. Errs toward flagging, never shows full values."""
    return _scan(text)[0]


def _scan(text):
    """(hits, spans): one hit per distinct number for display; every occurrence's span for masking."""
    text = text or ""
    hits, seen, spans = [], set(), []

    def add(kind, value, start):
        key = (kind, re.sub(r"\D", "", value) or value)
        spans.append((start, start + len(value), kind))
        if key in seen:
            return
        seen.add(key)
        hits.append({"kind": kind, "label": KIND_LABELS[kind], "masked": _mask(value), "start": start})

    for m in _SSN.finditer(text):
        add("ssn", m.group(0), m.start())
    for m in _SSN_LABELED.finditer(text):
        add("ssn", m.group(1), m.start(1))
    routing_digits = set()
    for m in _ROUTING.finditer(text):
        routing_digits.add(m.group(1))
        add("routing", m.group(1), m.start(1))
    for m in _ACCOUNT.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if 6 <= len(digits) <= 17 and digits not in routing_digits:
            add("account", m.group(1), m.start(1))
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            add("card", m.group(1), m.start(1))
    for m in _LICENSE.finditer(text):
        if sum(c.isdigit() for c in m.group(1)) >= 4:
            add("license", m.group(1), m.start(1))
    for m in _PASSWORD.finditer(text):
        add("password", m.group(1), m.start(1))
    return sorted(hits, key=lambda h: h["start"]), sorted(spans)


REDACTION_TAGS = {"ssn": "[SSN REMOVED]", "routing": "[ROUTING NUMBER REMOVED]", "account": "[ACCOUNT NUMBER REMOVED]",
                  "card": "[CARD NUMBER REMOVED]", "license": "[LICENSE NUMBER REMOVED]", "password": "[PASSWORD REMOVED]"}


def redact_text(text):
    """Replace every sensitive number in text with a tag. Returns (new_text, number_of_replacements)."""
    if not text:
        return text, 0
    spans = _scan(text)[1]
    out, last, count = [], 0, 0
    for start, end, kind in spans:
        if start < last:  # overlapping match (e.g. labelled and unlabelled forms of one number)
            continue
        out.append(text[last:start])
        out.append(REDACTION_TAGS[kind])
        last, count = end, count + 1
    out.append(text[last:])
    return "".join(out), count


def mask_text(text):
    """Like redact_text, but keeps the last 4 characters so staff can still match a stored email to a client."""
    if not text:
        return text, 0
    spans = _scan(text)[1]
    out, last, count = [], 0, 0
    for start, end, _ in spans:
        if start < last:
            continue
        out.append(text[last:start])
        out.append(_mask(text[start:end]))
        last, count = end, count + 1
    out.append(text[last:])
    return "".join(out), count


def describe_sensitive(hits):
    """'a Social Security or tax ID number and a bank account number' — for banners and warnings."""
    labels = []
    for h in hits:
        if h["label"] not in labels:
            labels.append(h["label"])
    phrases = [f"a {l}" for l in labels]
    if len(phrases) <= 1:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]

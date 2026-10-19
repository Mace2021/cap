"""Industry sample inboxes for sales demos: a tax & bookkeeping firm and an independent insurance agency.

Every business, person, address and link here is fictional (.example domains). Drafts are pre-written so a demo
is instant; "Regenerate" in the app still writes a fresh draft with the real AI from the same profile.
Facts quoted in the drafts (prices, turnaround times, policies) all come from the pack's profile or
Knowledge document, so fact highlighting shows them as backed.
"""

# ---------------------------------------------------------------------------
# Tax & bookkeeping firm
# ---------------------------------------------------------------------------

TAX_SIGNATURE = "Dana Whitfield, EA\nHarbor Lane Tax & Bookkeeping"

TAX_PROFILE = {
    "business_name": "Harbor Lane Tax & Bookkeeping",
    "about": "A small tax and bookkeeping firm for individuals, freelancers and small businesses. "
             "Owner: Dana Whitfield, Enrolled Agent. Team of 4.",
    "services_prices": "Individual return (Form 1040) from $275, one state return included. "
                       "Individual return with self-employment income (Schedule C) from $450. "
                       "Each rental property (Schedule E) adds $125. "
                       "Business return (S corp 1120-S or partnership 1065) from $1,200. "
                       "Amended return from $200. IRS or state notice response from $150. "
                       "Monthly bookkeeping from $350/month (up to 150 transactions). Payroll from $85/month.",
    "hours_location": "Mon–Fri 9am–5pm. During tax season (Jan 15 – Apr 15) also Saturdays 9am–1pm. "
                      "210 Harbor Lane, Suite 4. Meetings in person, by phone or video.",
    "policies": "Clients send documents only through our secure client portal, never by email. "
                "A signed engagement letter is needed before we start. Business returns need a 50% deposit. "
                "Returns are prepared in the order complete documents arrive, usually within 10 business days "
                "in tax season. Extension requests are due by April 1. Fees are paid before we e-file. "
                "Monthly books are closed by the 15th of the following month.",
    "links": "Secure client portal: https://portal.harborlanetax.example  "
             "Book a call: https://harborlanetax.example/book",
    "faq": "Q: Does an extension give me more time to pay? A: No. An extension gives more time to file, not to pay; "
           "any tax owed is still due by the April deadline. "
           "Q: When are fourth-quarter estimated taxes due? A: Mid-January (January 15, 2027 for the 2026 tax year). "
           "Q: What should I do with an IRS letter? A: Don't ignore it and don't pay before we review it. "
           "Upload a copy to the portal and note the response date on the letter.",
    "never_say": "Never give tax advice or tell a client how much they owe or will get back by email. "
                 "Never promise a refund amount or date. Never ask for or repeat Social Security numbers, "
                 "bank account numbers or passwords in email; point clients to the secure portal.",
}

TAX_EMAILS = [
    {
        "id": "demo-tax-ssn", "urgency": "URGENT", "confidence": 0.86,
        "reason": "The client emailed her Social Security number and bank details. The reply moves her to the portal "
                  "and doesn't repeat the numbers.",
        "sender": "Rosa Delgado <rosa.delgado@example.com>",
        "subject": "My info for the return",
        "body": "Hi Dana,\n\nHere's what you asked for so you can finish our return:\n\nMy SSN: 123-45-6789\n"
                "Bank routing 000000000, account 000123456789 for the refund.\n\nLet me know if you need anything "
                "else!\n\nRosa",
        "summary": "Rosa emailed her Social Security number and bank account details for direct deposit.",
        "suggested_replies": ["Thanks Rosa — please send those through the secure portal instead.",
                              "Got it. Can you call the office so we can confirm the details by phone?",
                              "Thanks — I'll delete this email; please re-send via the portal."],
        "reply": "Hi Rosa,\n\nThank you for sending this over so quickly. To keep your information safe, we only "
                 "accept Social Security and bank account numbers through our secure client portal, never by "
                 "email. Could you re-enter them there? https://portal.harborlanetax.example\n\n"
                 "I'll remove this email from our inbox once you've uploaded them. For your own security, you may "
                 "want to delete it from your Sent folder too.\n\n"
                 "Thank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
        "tasks": [{"title": "Delete Rosa's email with her SSN once she re-sends via the portal", "due": ""}],
    },
    {
        "id": "demo-tax-irs", "urgency": "URGENT", "confidence": 0.72,
        "reason": "IRS notice: the reply calms the client and asks for the letter, without guessing what she owes.",
        "sender": "Michelle Grant <m.grant@example.com>",
        "subject": "IRS letter says I owe $2,300?!",
        "body": "Dana,\n\nI just got a letter from the IRS (it says CP2000 at the top) saying I owe $2,300 for 2024 "
                "because of income I didn't report. I'm freaking out. Should I just pay it? What do I do?\n\n"
                "Michelle",
        "summary": "Michelle received an IRS CP2000 notice proposing $2,300 owed for 2024 and asks whether to pay it.",
        "suggested_replies": ["Don't pay yet — please upload the letter and I'll review it this week.",
                              "Can we set up a call to go through the letter together?",
                              "Please send the letter through the portal; notice reviews start at $150."],
        "reply": "Hi Michelle,\n\nI understand this is stressful, but please don't pay anything yet. A CP2000 is a "
                 "proposal, not a final bill, and these are often resolved by showing the IRS information it "
                 "didn't have.\n\nCould you upload a copy of the full letter to our secure portal and let me know "
                 "the response date printed on it? https://portal.harborlanetax.example\n\n"
                 "Our IRS notice response starts at $150. Once I've reviewed it, we can talk through the next "
                 "steps by phone or video: https://harborlanetax.example/book\n\n"
                 "Thank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
        "tasks": [{"title": "Review Michelle's CP2000 notice once uploaded and note the response date", "due": ""}],
    },
    {
        "id": "demo-tax-quote", "urgency": "IMPORTANT", "confidence": 0.84,
        "reason": "New client asking for a price; quoted from your fee list.",
        "sender": "Jason Park <jason@parkstudio.example>",
        "subject": "New client - how much for my taxes?",
        "body": "Hi,\n\nA friend recommended you. I'm a freelance graphic designer (mostly 1099 income) and I also "
                "rent out a condo I own. My old preparer retired. What would you charge to do my return, and how "
                "do we get started?\n\nThanks,\nJason Park",
        "summary": "Jason, a freelance designer with 1099 income and one rental condo, wants a price and next steps.",
        "suggested_replies": ["Welcome! Your return would start at $575 — I'll send the engagement letter.",
                              "Happy to help. Can we book a quick intro call first?",
                              "Thanks for reaching out — here's our checklist to get started."],
        "reply": "Hi Jason,\n\nThanks for reaching out, and please thank your friend for the referral.\n\n"
                 "For a return with self-employment income (Schedule C) and one rental property, our fee starts "
                 "at $575: $450 for the return with Schedule C, plus $125 for the rental. One state return is "
                 "included.\n\nTo get started:\n1. I'll send our engagement letter for you to sign.\n"
                 "2. Upload your documents to our secure client portal: https://portal.harborlanetax.example\n"
                 "3. If you'd like to talk first, book a call here: https://harborlanetax.example/book\n\n"
                 "Please don't email any documents with Social Security or account numbers; the portal keeps them "
                 "secure.\n\nThank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
        "tasks": [{"title": "Send engagement letter to Jason Park", "due": ""}],
    },
    {
        "id": "demo-tax-status", "urgency": "IMPORTANT", "confidence": 0.8,
        "reason": "Status question; the reply uses your turnaround policy without promising a date.",
        "sender": "Kevin O'Brien <kevin.obrien@example.com>",
        "subject": "Did you get my documents?",
        "body": "Hi Dana,\n\nI uploaded my W-2, two 1099-INTs and my mortgage statement last Tuesday. Did you get "
                "everything? Any idea when my return will be done? Hoping to get the refund soon.\n\nKevin",
        "summary": "Kevin uploaded his W-2, 1099-INTs and mortgage statement and asks when his return will be done.",
        "suggested_replies": ["Thanks Kevin — I'll check your upload and confirm today.",
                              "Got it. Returns usually take about 10 business days once complete.",
                              "We're missing one item — could you upload your 1098?"],
        "reply": "Hi Kevin,\n\nThanks for uploading your documents. I'll check today that everything we need is "
                 "there and let you know if anything is missing.\n\nWe prepare returns in the order complete "
                 "documents arrive, and during tax season that's usually within 10 business days. I can't "
                 "promise a refund amount or date, but I'll let you know as soon as your return is ready for "
                 "your review and signature.\n\n"
                 "Thank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
        "tasks": [{"title": "Check Kevin O'Brien's portal upload for completeness", "due": ""}],
    },
    {
        "id": "demo-tax-extension", "urgency": "IMPORTANT", "confidence": 0.83,
        "reason": "Extension question; the reply explains file vs. pay from your FAQ.",
        "sender": "Linda Chao <linda.chao@example.com>",
        "subject": "Can I get an extension?",
        "body": "Hi,\n\nThings are hectic with my business this year and I don't think I'll have my paperwork "
                "together in time. Can you file an extension for me? Does that mean I can pay later too?\n\n"
                "Linda",
        "summary": "Linda wants an extension and asks whether it also delays when she has to pay.",
        "suggested_replies": ["Yes, we can file it — but it extends filing, not paying.",
                              "Happy to. Can we book a quick call to estimate what you'll owe?",
                              "Yes — please confirm by April 1 so we can file it in time."],
        "reply": "Hi Linda,\n\nYes, we can file an extension for you. Please confirm by April 1 so we have time "
                 "to file it.\n\nOne important point: an extension gives you more time to file, not more time to "
                 "pay. Any tax you owe is still due by the April deadline, so we'll want to make a reasonable "
                 "estimate before then. If you'd like to go through it together, you can book a call here: "
                 "https://harborlanetax.example/book\n\n"
                 "Thank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
        "tasks": [{"title": "Prepare extension and payment estimate for Linda Chao", "due": ""}],
    },
    {
        "id": "demo-tax-books", "urgency": "INFORMATIONAL", "confidence": 0.88,
        "reason": "Bookkeeping client; the reply quotes your month-end close policy.",
        "sender": "Omar Haddad <omar@haddadcoffee.example>",
        "subject": "Last month's books + Amazon charges",
        "body": "Hey Dana,\n\nWhen will last month be closed out? I need the P&L for the bank. Also there are a "
                "bunch of Amazon charges — most are shop supplies but a couple were personal, sorry. I can tell "
                "you which ones.\n\nOmar\nHaddad Coffee Co.",
        "summary": "Omar asks when last month's books close and wants to flag which Amazon charges were personal.",
        "suggested_replies": ["Last month closes by the 15th — I'll send the P&L then.",
                              "Thanks — please mark the personal Amazon charges in the portal.",
                              "Can you send the bank's deadline so I can prioritize?"],
        "reply": "Hi Omar,\n\nWe close each month's books by the 15th of the following month, so last month's books "
                 "will be closed by the 15th of this month and I'll send your P&L as soon as it's ready. If the bank needs it "
                 "sooner, let me know their deadline.\n\nThanks for the heads-up on Amazon. Could you mark which "
                 "charges were personal? A quick list of dates and amounts is perfect, and I'll record them as "
                 "owner draws rather than business expenses.\n\n"
                 "Thank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
        "tasks": [{"title": "Close last month's books for Haddad Coffee and send P&L", "due": ""}],
    },
]

TAX_EMAILS.append({
    "id": "demo-tax-fraud", "urgency": "URGENT", "confidence": 0.15,
    "reason": "Likely fraud: a lookalike of Priya Raman's address asks to send her refund to a new bank account. "
              "Call Priya at the number on file before doing anything.",
    "sender": "Priya Raman <priya@ramandesiqn.example>",
    "subject": "Updated bank details for our refund",
    "body": "Hi Dana,\n\nWe've switched banks this month. Please update our bank details and send the business "
            "refund to our new account instead of the old one. The new routing and account numbers are in the "
            "attached form.\n\nI'm traveling and in meetings all week, so email only please.\n\nThanks,\nPriya",
    "summary": "A message that looks like it's from Priya asks to send her refund to a new bank account.",
    "suggested_replies": ["We'll call you at the number on file before changing anything.",
                          "For your security, we don't change payment details by email.",
                          "Thanks — I'll confirm with you by phone first."],
    "reply": "Hi Priya,\n\nThanks for letting us know. To protect you, we never change payment or deposit details "
             "based on an email. I'll call you at the phone number we already have on file to confirm before "
             "anything changes.\n\nThank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
    "tasks": [{"title": "Call Priya Raman at the number on file to verify the bank-change request", "due": ""}],
})

TAX_FYI = [
    ("demo-tax-phish", "IRS e-Services <alerts@irs-gov-secure.example>", "Action required: e-Services account suspension",
     "NO_REPLY_NEEDED",
     "Your IRS e-Services account will be suspended in 24 hours. Verify your account and PTIN here: "
     "https://irs-gov-secure.example/login",
     "Phishing: fake IRS e-Services message. Don't click; forward it to phishing@irs.gov and delete it."),
    ("demo-tax-software", "Tax Software Updates <updates@taxsoftware.example>",
     "Update 26.4 is ready to install", "SKIPPED"),
    ("demo-tax-news", "Practitioner News <news@taxpronews.example>",
     "This week: 5 changes to watch before filing season", "SKIPPED"),
    ("demo-tax-thanks", "Greg Novak <greg.novak@example.com>",
     "Got the refund — thank you!", "NO_REPLY_NEEDED"),
]

TAX_SENT = {
    "id": "demo-tax-sent", "sender": "Priya Raman <priya@ramandesign.example>",
    "subject": "Engagement letter for your 2026 business return",
    "body": "Can you send over what you need from us for the S corp return?",
    "summary": "Priya asked what's needed for her S corp return; the engagement letter was sent.",
    "reply": "Hi Priya,\n\nAttached is our engagement letter for your 2026 S corp return. Our fee starts at $1,200, "
             "with a 50% deposit to begin. Once it's signed, please upload your documents to the secure portal: "
             "https://portal.harborlanetax.example\n\nThank you,\nDana Whitfield, EA\nHarbor Lane Tax & Bookkeeping",
}

TAX_TEMPLATES = [
    ("Missing documents reminder",
     "Hi {first_name},\n\nThanks for sending your documents. To finish your return we still need:\n\n- \n\n"
     "Please upload them to our secure client portal (not by email): https://portal.harborlanetax.example\n\n"
     "We prepare returns in the order complete documents arrive."),
    ("Return ready for review and signature",
     "Hi {first_name},\n\nYour return is ready for your review. Please log in to the secure portal to review it "
     "and sign the e-file authorization: https://portal.harborlanetax.example\n\nOnce it's signed and your fee "
     "is paid, we'll e-file and send you confirmation."),
    ("Please use the secure portal",
     "Hi {first_name},\n\nTo protect your information, please don't send Social Security numbers, bank account "
     "numbers or tax documents by email. You can upload everything securely here: "
     "https://portal.harborlanetax.example"),
    ("Extension filed",
     "Hi {first_name},\n\nWe've filed your extension. Remember, it extends the time to file, not the time to pay. "
     "We'll be in touch about any estimated payment."),
]

TAX_DOCUMENTS = [
    ("Harbor Lane — client document checklist.txt",
     "HARBOR LANE TAX & BOOKKEEPING — WHAT TO UPLOAD FOR YOUR RETURN\n\n"
     "Upload everything to the secure client portal. Never send these by email.\n\n"
     "Everyone:\n- Last year's tax return (new clients)\n- Photo ID for you and your spouse\n"
     "- W-2s from every employer\n- 1099-INT and 1099-DIV (bank and investment income)\n"
     "- 1099-B (sales of stocks or crypto)\n- 1098 mortgage interest statement\n"
     "- 1095-A if you had Marketplace health insurance\n- Estimated tax payments you made, with dates\n"
     "- Childcare provider name, address, tax ID and amount paid\n\n"
     "Self-employed / freelancers (Schedule C):\n- 1099-NEC and 1099-K forms\n"
     "- Income and expense summary (or your bookkeeping P&L)\n- Business mileage log\n"
     "- Home office square footage, if you use one\n\n"
     "Rental property (Schedule E):\n- Rent received\n- Property tax, insurance, repairs, HOA, management fees\n"
     "- Purchase date and price, for new properties\n\n"
     "Turnaround: returns are prepared in the order complete documents arrive, usually within 10 business days "
     "in tax season."),
]

# ---------------------------------------------------------------------------
# Independent insurance agency
# ---------------------------------------------------------------------------

INS_SIGNATURE = "Marcus Bell\nSummit Ridge Insurance Agency"

INS_PROFILE = {
    "business_name": "Summit Ridge Insurance Agency",
    "about": "An independent insurance agency representing several carriers. Auto, home, renters, umbrella, "
             "small business (BOP, general liability, workers' comp) and life insurance. "
             "Principal agent: Marcus Bell. Team of 5.",
    "services_prices": "Quotes and policy reviews are free. Premiums depend on the carrier quote; we compare "
                       "several carriers for each client. Certificates of insurance (COIs) are free for clients.",
    "hours_location": "Mon–Fri 8:30am–5:30pm, Saturdays by appointment. 48 Summit Ridge Road. "
                      "Office phone (555) 010-4477.",
    "policies": "Certificates of insurance are issued within 1 business day once we have the certificate holder's "
                "name and address. Coverage and policy changes are only in effect once the carrier confirms them "
                "in writing. Claims are reported to the carrier's 24/7 claims line (on the ID card); we can open "
                "the claim with you and follow up. Cancellations need a signed cancellation request; any refund "
                "of unused premium comes from the carrier. Driver's license numbers and other personal ID are "
                "collected by phone or through our secure upload link, not by email.",
    "links": "Secure upload: https://secure.summitridgeins.example  Book a policy review: https://summitridgeins.example/review",
    "faq": "Q: Will a claim raise my rates? A: It depends on the carrier and who was at fault; we can't say before "
           "the claim is reviewed. Q: How fast can you quote? A: Most personal quotes within 1–2 business days "
           "once we have the information we need.",
    "never_say": "Never say coverage is bound, in force or changed until the carrier confirms it in writing. "
                 "Never promise a claim will be paid or that rates won't go up. Never quote a premium without "
                 "a carrier quote. Never ask for driver's license or Social Security numbers by email.",
}

INS_EMAILS = [
    {
        "id": "demo-ins-coi", "urgency": "URGENT", "confidence": 0.85,
        "reason": "Certificate request with a deadline; the reply asks for the one missing detail.",
        "sender": "Luis Rivera <luis@riveraplumbing.example>",
        "subject": "Need a COI for Hillcrest Builders by Friday",
        "body": "Marcus,\n\nI start a job for Hillcrest Builders on Monday and they need a certificate of "
                "insurance before then, by Friday at the latest. They also want to be listed as additional "
                "insured. Can you send it over?\n\nThanks,\nLuis\nRivera Plumbing",
        "summary": "Luis needs a certificate of insurance for Hillcrest Builders by Friday, with them as additional insured.",
        "suggested_replies": ["On it — send me Hillcrest's mailing address and you'll have it within a day.",
                              "Happy to. I'll check the additional insured endorsement with the carrier.",
                              "Can you forward Hillcrest's insurance requirements?"],
        "reply": "Hi Luis,\n\nHappy to get this done before Friday. Certificates go out within 1 business day once "
                 "we have the certificate holder's details, so could you send me:\n\n"
                 "1. Hillcrest Builders' full legal name and mailing address\n"
                 "2. Their insurance requirements or contract page, if they gave you one\n\n"
                 "Because they want to be additional insured, I'll confirm with your carrier that your policy "
                 "includes that endorsement. I'll let you know if anything needs to be added.\n\n"
                 "Thanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
        "tasks": [{"title": "Issue COI for Rivera Plumbing naming Hillcrest Builders (due Friday)", "due": ""},
                  {"title": "Confirm additional insured endorsement on Rivera Plumbing's GL policy", "due": ""}],
    },
    {
        "id": "demo-ins-claim", "urgency": "URGENT", "confidence": 0.62,
        "reason": "Claim: the reply gives next steps without promising coverage or rates.",
        "sender": "Nicole Foster <nicole.foster@example.com>",
        "subject": "Someone hit my car in the parking lot",
        "body": "Hi Marcus,\n\nSomeone backed into my car at the grocery store this morning and drove off. "
                "There's a big dent in the rear door. What do I do? Is this covered? Will my rates go up?\n\n"
                "Nicole",
        "summary": "Nicole's parked car was hit by a driver who left; she asks what to do, if it's covered and about rates.",
        "suggested_replies": ["I'm sorry, Nicole — call me and we'll open the claim together.",
                              "Here are the next steps; the claims line is on your ID card.",
                              "Did anyone see it or get a plate number?"],
        "reply": "Hi Nicole,\n\nI'm sorry this happened, and I'm glad you're okay.\n\nHere's what to do next:\n"
                 "1. Take photos of the damage and the parking spot, and note the time and location.\n"
                 "2. If the store has cameras or anyone saw it, ask the store to keep the footage.\n"
                 "3. Consider filing a police report, since the other driver left.\n"
                 "4. Report the claim to your carrier's 24/7 claims line (it's on your ID card), or call us at "
                 "(555) 010-4477 and we'll open it with you and follow up.\n\n"
                 "Whether it's covered and whether it affects your rate depends on your policy and the carrier's "
                 "review, so I don't want to guess. I'll look at your coverage today and let you know what I see.\n\n"
                 "Thanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
        "tasks": [{"title": "Review Nicole Foster's auto coverage and help open the hit-and-run claim", "due": ""}],
    },
    {
        "id": "demo-ins-quote", "urgency": "IMPORTANT", "confidence": 0.82,
        "reason": "New home quote with a closing date; the reply lists what you need to quote it.",
        "sender": "Aaron & Beth Kim <aaron.kim@example.com>",
        "subject": "Homeowners quote - closing in 2 weeks",
        "body": "Hello,\n\nWe're closing on a house on the 28th and our lender needs proof of homeowners insurance "
                "before then. Can you get us a quote? We'd also like to look at bundling our two cars.\n\n"
                "Thanks,\nAaron Kim",
        "summary": "Aaron and Beth close on a house on the 28th, need a homeowners quote for the lender and want to bundle auto.",
        "suggested_replies": ["Congratulations! Here's what I need to quote your home and autos.",
                              "Happy to — can we do a 15-minute call this week?",
                              "Can you send the property address and your lender's details?"],
        "reply": "Hi Aaron and Beth,\n\nCongratulations on the new home! We'll compare several carriers for you and "
                 "can quote the home and your two cars together.\n\nTo quote the home, please send:\n"
                 "- Property address, year built and square footage\n- Roof age and type\n"
                 "- Any claims in the last 5 years\n- Your lender's name and the mortgagee clause\n\n"
                 "For the cars, the year, make and model of each is enough to start. Please don't email driver's "
                 "license numbers; use our secure upload link instead: https://secure.summitridgeins.example\n\n"
                 "Most quotes are ready within 1–2 business days once we have everything, which leaves plenty of "
                 "time before the 28th.\n\nThanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
        "tasks": [{"title": "Quote home + auto bundle for the Kims (closing on the 28th)", "due": ""}],
    },
    {
        "id": "demo-ins-teen", "urgency": "IMPORTANT", "confidence": 0.8,
        "reason": "Policy change; the reply doesn't confirm it until the carrier does.",
        "sender": "Tanya Brooks <tanya.brooks@example.com>",
        "subject": "Add my son to our car insurance",
        "body": "Hi Marcus,\n\nOur son Eli just got his driver's license! We need to add him to our auto policy. "
                "Do you need his license number? Also, is there a discount for good grades?\n\nTanya",
        "summary": "Tanya wants to add her newly licensed son Eli to the auto policy and asks about a good-student discount.",
        "suggested_replies": ["Congrats to Eli! Please send his details through our secure link.",
                              "Let's do a quick call to add him and check discounts.",
                              "Which car will Eli drive most?"],
        "reply": "Hi Tanya,\n\nCongratulations to Eli! We'll get him added.\n\nPlease don't email his license "
                 "number. You can send his full name, date of birth and license details through our secure "
                 "upload link, or call us at (555) 010-4477: https://secure.summitridgeins.example\n\n"
                 "It also helps to know which car he'll drive most. I'll ask the carrier about a good-student "
                 "discount when I make the change; if he qualifies, a recent report card is usually all they "
                 "need. The change takes effect once the carrier confirms it in writing, and I'll send you that "
                 "confirmation.\n\nThanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
        "tasks": [{"title": "Add Eli Brooks to the Brooks auto policy; ask carrier about good-student discount", "due": ""}],
    },
    {
        "id": "demo-ins-renewal", "urgency": "IMPORTANT", "confidence": 0.79,
        "reason": "Renewal increase; the reply offers to compare carriers, which is your advantage as an independent agency.",
        "sender": "Dave Henderson <dhenderson@example.com>",
        "subject": "Why did my home insurance go up 18%??",
        "body": "Marcus,\n\nOur homeowners renewal just came in 18% higher than last year and we didn't file any "
                "claims. That's ridiculous. Can you find us something better?\n\nDave",
        "summary": "Dave's home renewal rose 18% with no claims; he wants you to shop for a better option.",
        "suggested_replies": ["Absolutely — I'll compare other carriers and get back to you this week.",
                              "Let's book a policy review to see what changed.",
                              "Could you send the renewal so I can see what increased?"],
        "reply": "Hi Dave,\n\nI understand the frustration, especially with no claims. As an independent agency we "
                 "can compare your coverage with several carriers, so let's see what's out there.\n\n"
                 "I'll review your renewal to see what changed and request quotes from other carriers with "
                 "the same coverage. If any of your home details have changed, like a new roof or updated "
                 "electrical, let me know since that can help.\n\nIf you'd rather talk it through, you can book a "
                 "free policy review here: https://summitridgeins.example/review\n\n"
                 "Thanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
        "tasks": [{"title": "Re-shop the Hendersons' homeowners renewal with other carriers", "due": ""}],
    },
    {
        "id": "demo-ins-cancel", "urgency": "INFORMATIONAL", "confidence": 0.81,
        "reason": "Cancellation; the reply explains the signed request and offers an alternative.",
        "sender": "Sophie Turner <sophie.t@example.com>",
        "subject": "Cancel my renters insurance",
        "body": "Hi,\n\nI'm moving in with my partner next month so I don't need my renters policy anymore. "
                "How do I cancel? Will I get money back?\n\nSophie",
        "summary": "Sophie is moving in with her partner next month and wants to cancel her renters policy and asks about a refund.",
        "suggested_replies": ["Congrats on the move! I'll send the cancellation form today.",
                              "Would you like to be added to your partner's policy instead?",
                              "What date should the cancellation take effect?"],
        "reply": "Hi Sophie,\n\nCongratulations on the move! To cancel, we need a signed cancellation request with "
                 "the date you'd like it to end. I'll send the form for you to sign.\n\nAny refund of unused "
                 "premium comes from the carrier, and they'll calculate it from the cancellation date.\n\n"
                 "One thing to check: if your partner rents too, your belongings won't be covered by their policy "
                 "unless you're added to it. I'm happy to look at that for you.\n\n"
                 "Thanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
        "tasks": [{"title": "Send Sophie Turner a renters cancellation request", "due": ""}],
    },
]

INS_EMAILS.append({
    "id": "demo-ins-fraud", "urgency": "URGENT", "confidence": 0.15,
    "reason": "Likely fraud: a lookalike of Luis Rivera's address asks to send a claim check to a new account. "
              "Call Luis at the number on file before acting.",
    "sender": "Luis Rivera <luis@riveraplumblng.example>",
    "subject": "Claim payment - please use our new account",
    "body": "Marcus,\n\nFor the water damage claim, please have the carrier send the payment by wire to our new "
            "account instead of mailing a check. Our bank changed, so the old account is closed. Please do this "
            "today, we need the funds ASAP.\n\nLuis",
    "summary": "A message that looks like it's from Luis asks to wire a claim payment to a new account.",
    "suggested_replies": ["We'll call you at the number on file to confirm.",
                          "Payment instructions can't be changed by email.",
                          "Thanks — I'll verify with you by phone first."],
    "reply": "Hi Luis,\n\nThanks for the note. For your protection, we never change payment instructions based "
             "on an email. I'll call you at the number we have on file to confirm before anything is changed with "
             "the carrier.\n\nThanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
    "tasks": [{"title": "Call Luis Rivera at the number on file to verify the payment-change request", "due": ""}],
})

INS_FYI = [
    ("demo-ins-phish", "Carrier Agent Portal <security@carrier-portal-login.example>",
     "Your agent portal password expires today", "NO_REPLY_NEEDED",
     "Your agent portal password expires today. Sign in here to keep access: "
     "https://carrier-portal-login.example/agent",
     "Phishing: fake carrier portal message. Don't click; go to the carrier's site yourself."),
    ("demo-ins-bulletin", "Carrier Agent Bulletin <bulletin@carrier.example>",
     "Homeowners rate filing update for your state", "SKIPPED"),
    ("demo-ins-news", "Agency Insider <news@agencyinsider.example>",
     "3 ways independent agents win renewals", "SKIPPED"),
    ("demo-ins-thanks", "Hillcrest Builders <office@hillcrest.example>",
     "Thanks for the quick certificate!", "NO_REPLY_NEEDED"),
]

INS_SENT = {
    "id": "demo-ins-sent", "sender": "Grace Lin <grace.lin@example.com>",
    "subject": "Your umbrella policy quote",
    "body": "Could you look into an umbrella policy for us?",
    "summary": "Grace asked about an umbrella policy; the quote was sent.",
    "reply": "Hi Grace,\n\nAs promised, attached are umbrella quotes from two carriers for $1 million of coverage. "
             "Coverage starts only once the carrier confirms it in writing, so just let me know which one you'd "
             "like and I'll take care of it.\n\nThanks,\nMarcus Bell\nSummit Ridge Insurance Agency",
}

INS_TEMPLATES = [
    ("COI request — details needed",
     "Hi {first_name},\n\nHappy to issue that certificate. Certificates go out within 1 business day once we have:\n"
     "1. The certificate holder's full legal name and mailing address\n"
     "2. Any special wording or additional insured requirements"),
    ("Quote — information checklist",
     "Hi {first_name},\n\nThanks for the chance to quote. We compare several carriers for you. Please send the "
     "details below; anything with personal ID numbers should go through our secure upload link, not email: "
     "https://secure.summitridgeins.example\n\n- \n\nMost quotes are ready within 1–2 business days."),
    ("Claim — first steps",
     "Hi {first_name},\n\nI'm sorry to hear about this. Please take photos, note the date, time and location, and "
     "report the claim to your carrier's 24/7 claims line on your ID card, or call us at (555) 010-4477 and we'll "
     "open it with you."),
    ("Policy change received",
     "Hi {first_name},\n\nWe've received your change request and sent it to the carrier. The change takes effect "
     "once the carrier confirms it in writing, and we'll send you that confirmation."),
]

INS_DOCUMENTS = [
    ("Summit Ridge — what we need to quote.txt",
     "SUMMIT RIDGE INSURANCE AGENCY — WHAT WE NEED TO QUOTE\n\n"
     "Personal ID numbers (driver's license, SSN) go through our secure upload link or by phone, never email.\n\n"
     "Auto: each driver's name and date of birth; license details via secure link; year, make and model of each "
     "car; current carrier and renewal date; any accidents or tickets in the last 5 years.\n\n"
     "Home: address, year built, square footage, roof age and type, heating and electrical updates, claims in "
     "the last 5 years, lender name and mortgagee clause, closing date for purchases.\n\n"
     "Renters: address, value of belongings, any roommates.\n\n"
     "Small business: business type, years in business, annual revenue and payroll, number of employees, "
     "current policies and renewal dates, any contract insurance requirements.\n\n"
     "Certificates of insurance: certificate holder's legal name and mailing address; issued within 1 business "
     "day.\n\nMost personal quotes are ready within 1–2 business days."),
]


# Tracker items for the samples. due_in = days from today (None = no date).
TAX_TRACKER = [
    {"kind": "client", "name": "Kevin O'Brien", "email": "kevin.obrien@example.com", "status": "waiting_docs",
     "due_in": None, "data": {"tax_year": "2026", "return_type": "Form 1040", "docs": [
         {"name": "W-2", "received": False}, {"name": "1099-INT", "received": False},
         {"name": "1098 mortgage interest", "received": False}, {"name": "Photo ID", "received": True}]}},
    {"kind": "client", "name": "Jason Park", "email": "jason@parkstudio.example", "status": "waiting_docs",
     "due_in": None, "data": {"tax_year": "2026", "return_type": "1040 + Schedule C + rental", "docs": [
         {"name": "Signed engagement letter", "received": False}, {"name": "1099-NEC", "received": False},
         {"name": "Business income & expenses", "received": False}, {"name": "Rental income & expenses", "received": False},
         {"name": "Prior-year return", "received": False}, {"name": "Photo ID", "received": False}]}},
    {"kind": "client", "name": "Linda Chao", "email": "linda.chao@example.com", "status": "extended",
     "due_in": None, "data": {"tax_year": "2026", "return_type": "1040 + Schedule C", "docs": [
         {"name": "1099-K", "received": True}, {"name": "Business income & expenses", "received": False},
         {"name": "Estimated payment records", "received": False}]}},
    {"kind": "client", "name": "Priya Raman", "email": "priya@ramandesign.example", "status": "in_prep",
     "due_in": None, "data": {"tax_year": "2026", "return_type": "S corp (1120-S)", "docs": [
         {"name": "Signed engagement letter", "received": True}, {"name": "Business income & expenses", "received": True}]}},
    {"kind": "client", "name": "Greg Novak", "email": "greg.novak@example.com", "status": "filed",
     "due_in": None, "data": {"tax_year": "2025", "return_type": "Form 1040", "docs": [
         {"name": "W-2", "received": True}, {"name": "1098 mortgage interest", "received": True}]}},
]

INS_TRACKER = [
    {"kind": "coi", "name": "Luis Rivera (Rivera Plumbing)", "email": "luis@riveraplumbing.example", "status": "requested",
     "due_in": "friday", "data": {"holder": "Hillcrest Builders", "subject": "Need a COI for Hillcrest Builders by Friday",
                                  "additional_insured": True}},
    {"kind": "renewal", "name": "Dave Henderson", "email": "dhenderson@example.com", "status": "reshopping",
     "due_in": 21, "data": {"policy": "Homeowners policy", "carrier": "current carrier", "premium_change": "+18%"}},
    {"kind": "renewal", "name": "Tanya Brooks", "email": "tanya.brooks@example.com", "status": "upcoming",
     "due_in": 12, "data": {"policy": "Auto policy (2 vehicles)", "carrier": "", "premium_change": ""}},
    {"kind": "renewal", "name": "Grace Lin", "email": "grace.lin@example.com", "status": "upcoming",
     "due_in": 45, "data": {"policy": "Homeowners policy", "carrier": "", "premium_change": ""}},
    {"kind": "renewal", "name": "Sophie Turner", "email": "sophie.t@example.com", "status": "lost",
     "due_in": 30, "data": {"policy": "Renters policy", "carrier": "", "premium_change": ""}},
]

INDUSTRY_PACKS = {
    "tax": {
        "label": "Tax & bookkeeping firm", "profile": TAX_PROFILE, "emails": TAX_EMAILS, "fyi": TAX_FYI,
        "sent": TAX_SENT, "templates": TAX_TEMPLATES, "documents": TAX_DOCUMENTS,
        "signature": TAX_SIGNATURE, "user_name": "Dana", "tracker": TAX_TRACKER,
    },
    "insurance": {
        "label": "Insurance agency", "profile": INS_PROFILE, "emails": INS_EMAILS, "fyi": INS_FYI,
        "sent": INS_SENT, "templates": INS_TEMPLATES, "documents": INS_DOCUMENTS,
        "signature": INS_SIGNATURE, "user_name": "Marcus", "tracker": INS_TRACKER,
    },
}

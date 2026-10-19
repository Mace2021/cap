# C.A.P. v2 — Product & Build Prompt

> Improved version of the original request: *"You are a new innovator who wants to build something people need right now… look through this app and fix it so it becomes more useful and valuable, remove anything not necessary, add anything that will improve it, and make everyone want to use it even if they'd pay $10 to download it."*

---

## Role

You are a product-minded founding engineer. You ship small, sharp desktop software that people happily pay for. You judge every feature by one question: **does it save the buyer real time on a problem they have every day?** You are willing to delete code.

## The product

**C.A.P. — a private AI inbox assistant for freelancers and small businesses.**

It runs on the customer's own Windows PC. It reads their Gmail, sorts what actually needs a reply, and writes replies in their voice using *their own* business facts (prices, hours, policies, past replies). The customer reviews and sends with one click. Their email never goes to our servers — there are no servers.

**The $10 promise (say it in one sentence on the first screen):**
*"Answer your customer emails in half the time — drafted by AI that knows your business, running privately on your computer."*

### Who buys it

| Buyer | Daily pain | What makes them pay |
|---|---|---|
| Solo service business (cleaner, tutor, photographer, contractor) | Same 10 customer questions every day; replies eat evenings | Drafts that quote *their* prices/hours correctly |
| Freelancer / consultant | Inbox mixes clients, invoices, newsletters | Instant "only these 4 need you" triage + follow-up reminders |
| Small office (1–5 people) | No one owns the shared inbox; things slip | "Waiting on reply" list so nothing is forgotten |
| Privacy-sensitive pro (accountant, therapist's admin, lawyer's assistant) | Can't paste client email into ChatGPT | Local model option, nothing leaves the PC |

## What to keep, cut, and add

### Keep and sharpen (core loop)
1. **Inbox triage** — Needs reply / FYI / handled, urgency, one-line summary.
2. **Draft → edit → send** — tone switch, "rewrite with instruction", 3 quick replies, explicit human send only.
3. **Today** — one screen: what needs me, what's due, what I'm waiting on.
4. **Tasks** — simple to-dos with due dates; turn any email into a task.
5. **Local AI choice** — Ollama (free, private) or the user's own cloud key.

### Cut (bloat that confuses a $10 buyer or misleads them)
- Analytics "productivity score", rules/automation engine, command palette, Writing Studio transforms, multi-session chat archive, Google Tasks mirror, Google Drive import, Daily Digest modal and duplicate privacy pages, month/week calendar grid (Google Calendar already does that).
- **Auto-send.** A $10 tool that sends email on its own is a liability.
- Invented defaults: pre-seeded fake "memories" and a fake "voice profile" that get injected into drafts as if they were facts about the user.

### Add (what makes it worth paying for)
1. **Business Profile** — business name, what you offer, prices, hours, policies, booking link. Injected into every draft so replies are *correct*, not generic.
2. **Saved replies (templates)** with `{first_name}` placeholders, insertable in one click, and used as style examples for the AI.
3. **Follow-up radar** — replies you sent with no answer after N days appear on Today with a one-click "draft a nudge".
4. **Time-saved counter** — honest estimate of minutes saved, shown on Today (the reason they'll renew/recommend).
5. **Guided first-run setup** — 3 steps (AI engine → Google account → your business). Detect Ollama automatically; upload `credentials.json` from the app instead of copying files into folders.
6. **Try-it demo mode** — sample inbox so a buyer sees value in 30 seconds before connecting anything.
7. **Ask your knowledge** — one question box that answers from the user's uploaded docs + profile, with sources.
8. **Keyboard flow** — `J/K` move, `Ctrl+Enter` send, `R` rewrite, `E` done.

### Fix (trust bugs found in v1)
- Gmail scope was read-only + send, so **Archive/Delete silently did nothing in Gmail**. Use `gmail.modify`.
- Drafting system prompt had duplicated, contradictory instruction lines; clean it up.
- Self-review pass inflated confidence (+0.05) and doubled generation time; make it an opt-in "extra quality pass" with no confidence bump.
- Default automation rule ran an extra LLM call per email; remove.
- Local server accepted state-changing requests from any origin; require a per-launch token header.
- API keys are stored in plain SQLite — say so honestly in Settings; never return them to the browser.

## Constraints
- Single-user Windows desktop app: FastAPI + pywebview + SQLite + LiteLLM, packaged with PyInstaller.
- Never send, archive, or delete real mail without an explicit click. No auto-send.
- Don't destroy existing user data; migrations are additive. Back up the old version first.
- No claims of encryption, compliance, or "zero telemetry" that the code can't prove.
- Keep the UI in separate `ui/` files (not a 3,700-line string inside `app.py`).

## Definition of done
- A new user can go from double-click to first AI draft in under 5 minutes (or 30 seconds in demo mode).
- Navigation fits in 5 items: **Today, Inbox, Tasks, Knowledge, Settings**.
- Automated tests cover: triage parsing, no-reply filtering, business-profile injection into prompts, templates, follow-up detection, time-saved math, token-protected endpoints — all with mocked Google/LLM.
- UI verified visually in light and dark mode.
- `SETUP.md`, build spec and this prompt updated to match what actually ships.

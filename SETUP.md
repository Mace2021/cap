# C.A.P. — Private AI Inbox Assistant (v2.1)

C.A.P. reads your Gmail, sorts out what actually needs you, and drafts replies in your voice using **your** business facts (prices, hours, policies, saved replies). You review every draft and send it with one click. It runs on your own Windows PC. There is no C.A.P. server.

**Five screens:** Today · Inbox · Tasks · Knowledge · Settings. The first launch opens a three-step setup.

---

## 1. Run it

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe launcher.py
```

Packaged build: extract `CAP.zip`, open the `CAP` folder and run `CAP.exe`. Keep the folder intact. Your data lives in `%LOCALAPPDATA%\CAP` (or next to the code when you run from source).

## 2. Pick the AI that writes drafts

**On this computer (free, private):** install [Ollama](https://ollama.com/download), then pull one model:

| Model | Command | Good for |
|---|---|---|
| Qwen 3.5 9B | `ollama pull qwen3.5:9b` | Newest all-rounder, strong at structured replies (12 GB RAM+) |
| Gemma 4 12B | `ollama pull gemma4:12b` | Most natural writing (16 GB RAM+) |
| IBM Granite 4.2 8B | `ollama pull granite4.2:8b` | Built for business assistants (12 GB RAM+) |
| Gemma 3 12B | `ollama pull gemma3:12b` | Natural writing, no thinking delay (16 GB RAM+) |
| OpenAI gpt-oss 20B | `ollama pull gpt-oss:20b` | Highest quality, slowest (24 GB RAM+) |
| Qwen 3.5 4B | `ollama pull qwen3.5:4b` | 8 GB RAM PCs |
| Gemma 3 4B | `ollama pull gemma3:4b` | 8 GB RAM PCs |
| Llama 3.1 8B | `ollama pull llama3.1:8b` | Older, dependable (8 GB RAM+) |
| Qwen 2.5 7B | `ollama pull qwen2.5:7b` | Older, reliable structured replies |
| Gemma 2 9B | `ollama pull gemma2:9b` | Older, natural writing (12 GB RAM+) |
| Mistral 7B | `ollama pull mistral:7b` | Older, faster |
| Llama 3.2 3B | `ollama pull llama3.2:3b` | Older or low-memory PCs |

C.A.P. detects Ollama automatically. Pick the model in setup or in **Settings → AI engine**, then click **Test and use this model**.

**Cloud (faster on slow PCs):** choose OpenAI, Anthropic, Gemini, Groq or OpenRouter in **Settings → AI engine** and paste your API key. The key is stored in the local SQLite database without encryption and is never sent to the UI. With a cloud model, the text needed for each draft goes to that provider.

## 3. Connect your email

Type your address in setup (or **Settings → Email account**). C.A.P. picks the right method:

| Your email | How it connects | What the user does |
|---|---|---|
| Gmail / Google Workspace, Yahoo, iCloud, AOL, Zoho, Fastmail | IMAP + SMTP with an **app password** | Creates an app password (the screen links to the right page) and pastes it. About 2 minutes. |
| Outlook.com, Hotmail, Microsoft 365 | **Sign in with Microsoft** (device code, OAuth) | Enters a short code on Microsoft's page and approves. C.A.P. never sees the password. |
| Business domain (e.g. you@yourbusiness.com) | IMAP + SMTP | Password or app password; server names are pre-filled as `mail.yourdomain.com` and can be edited. |

- Mail is read with `BODY.PEEK`, so nothing is marked as read. **Done** archives and **Delete** moves to Trash on the server, only when clicked.
- Passwords and API keys are kept in **Windows Credential Manager** (via `keyring`). Only if that isn't available do they fall back to the local database, and Settings says which one is in use.
- Microsoft has turned off password sign-in for IMAP/SMTP on Microsoft 365, and is retiring it on Outlook.com. That's why those accounts use Microsoft sign-in.

### One-time setup for Microsoft sign-in (you, the vendor; not each customer)

1. Go to [Azure portal](https://portal.azure.com) → **Microsoft Entra ID → App registrations → New registration**. Name it "C.A.P. Inbox Assistant". For supported accounts, choose **any organizational directory and personal Microsoft accounts**.
2. **Authentication** → set **Allow public client flows** to **Yes**. No redirect URI is needed.
3. **API permissions → Add → APIs my organization uses → Office 365 Exchange Online → Delegated**: `IMAP.AccessAsUser.All` and `SMTP.Send`. Also add Microsoft Graph `offline_access`.
4. Copy the **Application (client) ID** into `cap_config.json` next to `launcher.py` (see `cap_config.example.json`). `build.py` bundles it automatically. You can also set the `CAP_MS_CLIENT_ID` environment variable instead.
5. For Microsoft 365 customers, their admin may need to approve the app once, and **Authenticated SMTP** must be enabled for their mailbox. Publisher verification (free with a Microsoft partner ID) removes the "unverified" label.

Without `cap_config.json`, Outlook/Microsoft 365 users see a clear message, and everyone else can still connect.

### Optional: Google sign-in (adds your calendar)

**Settings → Google Calendar** connects through Google's API using your own OAuth client file (Google Cloud project → enable the Calendar API, and the Gmail API if you'll read Gmail this way → OAuth consent screen with yourself as a test user → Desktop app client → download JSON → add it in C.A.P.). This lets drafts avoid times you're already booked and shows today's events. It isn't required for email.

## 4. Make drafts accurate

- **Knowledge → Business profile:** prices, hours, policies, links, FAQs, and things you never promise. Every draft can use these facts, and only these.
- **Knowledge → Saved replies:** your common answers, with `{first_name}`, `{my_name}`, `{business_name}` and `{booking_link}` placeholders. Insert one into any draft. The AI also borrows their wording for similar emails.
- **Knowledge → Files & notes:** price lists, menus and policy PDFs. Relevant passages are pulled into drafts. **Ask** answers questions from them, with sources.
- **Settings → Learn my style:** reads your sent mail to match your greeting, sign-off and length. It needs at least 3 sent emails.

## Daily use

- **Today:** what needs a reply, who hasn't answered you, tasks due, today's calendar, and time saved.
- **Inbox:** *Needs reply*, *FYI* (newsletters, receipts and notifications, so you can check nothing was misfiled), *Snoozed*, *Sent*.
  New mail is **sorted first** (a short AI call per email), so the list appears in seconds. Full drafts follow, **urgent first**. If you open one that isn't written yet, it jumps the queue. On slow PCs, choose **Settings → Inbox → Write drafts: only when I open an email**.
- **Fact highlighting:** prices, times, dates, percentages, links and promises (refund, discount, free, guarantee) in every draft are checked against the email, your Business profile, files, saved replies and signature. **Green** = found in your sources, **amber** = check this. Click an amber chip to jump to it.
- **Learns from your edits:** when you change a fact before sending (e.g. $240 → $260), C.A.P. offers to update your Business profile. If you rewrite most of a draft, it offers to save it as a saved reply. Today shows how many drafts went out with little or no editing.
- **Snooze** any email until later today, tomorrow morning, next Monday or next week.
- Tick **Remind me if no answer** to have a reply show up on Today if they go quiet.
- **Keyboard:** `J`/`K` move · `R` rewrite · `E` done · `Ctrl+Enter` send · `1`–`5` switch screens · `?` help.
- New mail is checked automatically (every 10 minutes by default; change this in Settings → Inbox).

## Privacy & safety facts

- The server listens on `127.0.0.1` only. State-changing requests need a per-launch token, and foreign `Host` headers are rejected.
- Nothing is sent, archived or deleted without your click. There is no auto-send.
- **Settings → Your data**: export everything as JSON (API keys excluded) or delete local email history. Gmail itself is untouched.
- C.A.P. makes no compliance or encryption claims. Use full-disk encryption on shared machines.

## Tests

```powershell
.\.venv\Scripts\pip install pytest httpx
.\.venv\Scripts\python.exe -m pytest -q tests
```

All Google and AI calls are mocked. The tests never touch a real account.

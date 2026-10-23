# C.A.P. 2.2.3 — 2026-10-23

## Added
- **Microsoft Store package** (`CAP-<version>-store-x64.msix`). The Store signs it, so no code-signing certificate
  is needed for Store installs. Release builds install a test-signed copy, run the self-test inside it and remove
  it before publishing.
- C.A.P. has its own icon in the taskbar, Start menu and installer.

# C.A.P. 2.2.2 — 2026-10-23

## Added
- **Windows installer** (`CAP-<version>-windows-x64-setup.exe`): installs for the current user without an
  administrator prompt, adds a Start menu entry and an uninstaller in Settings → Apps, and supports silent install
  (`/VERYSILENT /SUPPRESSMSGBOXES /NORESTART`) for the Microsoft Store and IT tools. Uninstalling keeps the user's
  data. The portable ZIP is still offered.
- Release builds install, self-test and uninstall the Windows installer before publishing.
- Code-signing hook: set `CAP_SIGN_COMMAND` and the build signs CAP.exe, the installer and its uninstaller.

# C.A.P. 2.2.1 — 2026-10-19

## Fixed
- **Windows: "Failed to resolve Python.Runtime.Loader.Initialize" on first launch.** When the download was unzipped
  with Windows' Extract All, Windows marked every file as coming from the internet and .NET refused to load the
  window. C.A.P. now clears that mark from its own files when it starts, and explains how to unblock the ZIP if it
  can't.
- The release self-test now also loads the window libraries on each system, so this kind of problem is caught before
  a release is published.

# C.A.P. 2.2.0 — 2026-10-19

## Added
- **Key dates that never run out.** Federal tax deadlines are worked out for every year, moved off weekends and
  Washington, D.C. holidays the way the IRS does.
- **Your own dates:** state deadlines, office dates and anything else, once or every year.
- **Client timelines:** each client card lists its return due date and any dates you add for that client.
- **Upload files** button on Knowledge → Files & notes.
- Builds for Windows, macOS (Apple silicon) and Linux, each checked with `CAP --selftest` before release.

## Fixed
- A starter kit added while a sample inbox was loaded lost some of its saved replies, its safety rules and the
  industry choice when the sample was removed.
- Switching pages quickly could show the previous page's content under the new page's name.
- After an update, the app window could keep running the old interface until restarted twice.
- The Files drop area drew stray dashed lines around each line of text.
- Upload errors repeated the file name.

## Security
- Pages can no longer be framed by other websites (`X-Frame-Options`, `frame-ancestors`), responses are not
  MIME-sniffed, and data from the app's API is never cached.
- LiteLLM no longer downloads its price list from GitHub on start-up, and its telemetry flag is off.
- macOS and Linux builds keep data in the usual per-user app-data folder.

# C.A.P. 2.1.0 — 2026-10-16

Focus: fewer reasons to give up in the first ten minutes (setup, speed, trust).

## Added
- **Connect any email account, no Google Cloud project.** App-password IMAP/SMTP for Gmail, Yahoo, iCloud, AOL, Zoho, Fastmail and business domains, with provider auto-detection and step-by-step instructions. **Sign in with Microsoft** (device code + XOAUTH2) for Outlook.com and Microsoft 365, which no longer accept passwords. Needs a one-time Azure app registration by the vendor (SETUP.md).
- **Fact highlighting** in the draft editor: green = backed by the email or your Knowledge, amber = check it, with a legend of where each fact came from.
- **Learns from edits:** after sending, offers to update the Business profile with prices or policies you changed, or to save a heavily rewritten reply as a saved reply. Tracks how many drafts went out with light edits.
- **Fast first look:** new mail is sorted with a short AI call first, then drafted urgent-first in the background, or only when opened (setting). Opening an undrafted email drafts it immediately.
- **Snooze** (later today, tomorrow, Monday, next week) and a Snoozed tab.
- Passwords and API keys are stored in **Windows Credential Manager** when available. Existing plain-text API keys are moved there automatically.
- The sample inbox brings a sample business profile (only if yours is empty, and removed with the samples) so fact highlighting can be seen right away.

## Fixed
- Background calendar checks could open a Google sign-in window unexpectedly. Google is now only opened when you click Connect.
- Quoted history wasn't stripped from short replies or HTML emails.
- Drafts being written by the background job and by "open to draft" at the same time are no longer duplicated.

# C.A.P. 2.0.0 — 2026-10-16

Refocused from a 13-tab "executive chief of staff" into a private AI inbox assistant for freelancers and small businesses. The v1 code, tests and database are backed up in `_backup_v1_2026-10-16/`. Your existing database upgrades in place: nothing is deleted, and pending drafts, tasks, notes and files carry over.

## Added
- **Business profile**: prices, hours, policies, links, FAQs and "never promise". Fed into every draft so replies quote your real facts.
- **Saved replies** with placeholders. Insert them into drafts, and the AI uses them as style and fact examples.
- **Follow-up radar**: sent replies with no answer after N days appear on Today, with a one-click "Draft a nudge".
- **Suggested tasks** extracted while triaging (no extra AI call), addable with one click.
- **Time-saved counter** with its assumptions shown.
- **First-run setup** in three steps: Ollama auto-detection, Google sign-in file upload in the app, business basics.
- **Sample inbox** to try the app before connecting Gmail.
- **FYI view** showing everything the AI decided not to draft, with "Draft a reply anyway".
- **Background inbox check** with a progress bar. It stops after the first fatal AI error instead of failing 25 times.
- Better retrieval (passage-level, with sources) for drafts and **Ask**.
- HTML-only emails are read (v1 saw an empty body). Quoted history is stripped from the latest message.
- Keyboard flow (`J/K/R/E`, `Ctrl+Enter`), dark mode, and a layout that works on narrow windows.

## Fixed
- **Archive/Delete did nothing in Gmail.** v1 only had `gmail.readonly` + `gmail.send` and swallowed the error. v2 requests `gmail.modify` and reports failures.
- The drafting prompt had duplicated, contradictory instructions. It's rewritten and stricter about not inventing facts or availability.
- The self-review pass raised confidence by +0.05 and doubled drafting time. It's now an opt-in "extra quality check" that doesn't change confidence.
- v1 injected invented "memories" (e.g. focus time 2–4:30 PM) and a fabricated voice profile into drafts as if they were facts. Both are removed.
- A default automation rule made an extra AI call per email. Removed.
- Retried emails could crash on a duplicate insert. Logging is now an upsert.
- The local server accepted commands from any origin. A per-launch token and Host check were added.
- The model list showed a model (`granite3.3:8b`) whether or not it was installed.

## Removed
Auto-send, Analytics/score, Automations engine, Command palette, Writing Studio transforms, multi-session chat archive, Google Tasks mirror, Google Drive import, Daily Digest modal, duplicate privacy pages, month/week calendar grid, personas, local "memory", reminders (merged into tasks), and 70+ cloud model presets (a custom model ID still works). Docling is now optional (`requirements-optional.txt`), which takes over 1 GB off the download.

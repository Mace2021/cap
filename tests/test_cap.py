"""C.A.P. v2 tests. Google and the LLM are always mocked; nothing touches a real account."""

import base64
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import app
import facts
import mail_accounts
import pipeline


def llm_reply(payload):
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}


DRAFT_JSON = {
    "needs_reply": True, "urgency": "IMPORTANT", "summary": "Maria wants a quote.",
    "suggested_replies": ["Yes", "No", "Maybe"], "reply": "Hi Maria,\n\nIt's $240.\n\nBest regards,\nSam",
    "confidence": 0.85, "reason": "Pricing question", "tasks": [{"title": "Hold Friday slot", "due": "2030-01-05"}],
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cap-tests-")
        self.db_path = str(Path(self.tmp.name) / "cap.db")
        pipeline.INITIALIZED_DATABASES.discard(str(Path(self.db_path).resolve()))
        self._orig_init = pipeline.init_db
        orig = self._orig_init
        pipeline.init_db = lambda db_path=None: orig(self.db_path)
        self._orig_token = pipeline.TOKEN_FILE
        pipeline.TOKEN_FILE = str(Path(self.tmp.name) / "token.json")
        self._orig_data = pipeline.DATA_DIR
        pipeline.DATA_DIR = Path(self.tmp.name)
        app.state.update({"connected": False, "email": None, "calendar_enabled": False, "checked_at": 9e18})
        self.client = TestClient(app.app, headers={"X-CAP-Token": app.APP_TOKEN})
        self.conn = pipeline.init_db()

    def tearDown(self):
        self.conn.close()
        pipeline.init_db = self._orig_init
        pipeline.TOKEN_FILE = self._orig_token
        pipeline.DATA_DIR = self._orig_data
        self.tmp.cleanup()

    def add_email(self, gid="m1", action="DRAFT", **extra):
        email = {"id": gid, "sender": "Maria Lopez <maria@example.com>", "subject": "Quote for cleaning",
                 "body": "How much is a move-out clean?", "thread_id": f"t-{gid}", "message_id": f"<{gid}@mail>",
                 "received_at": "2030-01-01T10:00:00+00:00", **extra}
        pipeline.log_email(self.conn, email, action, DRAFT_JSON if action == "DRAFT" else {"summary": ""})
        return email


class ParsingTests(Base):
    def test_parser_clears_reply_when_no_reply_needed_and_clamps(self):
        r = pipeline._parse_model_json(json.dumps({**DRAFT_JSON, "needs_reply": False, "confidence": 7}))
        self.assertEqual(r["reply"], "")
        self.assertEqual(r["confidence"], 1.0)
        self.assertEqual(r["suggested_replies"], [])

    def test_parser_extracts_json_inside_prose_and_strips_placeholders(self):
        raw = "Sure!\n```json\n" + json.dumps({**DRAFT_JSON, "reply": "Hi [Your Name], thanks"}) + "\n```"
        r = pipeline._parse_model_json(raw)
        self.assertNotIn("[Your", r["reply"])
        self.assertEqual(r["tasks"][0]["title"], "Hold Friday slot")

    def test_parser_rejects_unstructured_output(self):
        with self.assertRaises(ValueError):
            pipeline._parse_model_json("I think you should reply nicely.")

    def test_automated_detection(self):
        human = {"sender": "Ana <ana@example.com>", "subject": "Question", "body": "Can you come Friday?"}
        self.assertFalse(pipeline.looks_automated(human))
        self.assertTrue(pipeline.looks_automated({**human, "list_unsubscribe": "<mailto:x>"}))
        self.assertTrue(pipeline.looks_automated({**human, "label_ids": ["INBOX", "CATEGORY_PROMOTIONS"]}))
        self.assertTrue(pipeline.looks_automated({**human, "sender": "no-reply@shop.example"}))
        self.assertTrue(pipeline.looks_automated({**human, "auto_submitted": "auto-generated"}))

    def test_html_only_body_and_quote_stripping(self):
        html_part = base64.urlsafe_b64encode(b"<p>Hello<br>Can you <b>call</b>?</p>").decode()
        payload = {"mimeType": "multipart/alternative", "parts": [{"mimeType": "text/html", "body": {"data": html_part}}]}
        self.assertIn("Can you call", " ".join(pipeline._extract_body(payload).split()))
        body = "Sounds good, see you then.\n\nOn Tue, Jan 1, 2030 at 9:00 AM Sam <sam@x.com> wrote:\n> old text"
        self.assertEqual(pipeline.clean_email_body(body), "Sounds good, see you then.")


class MigrationTests(Base):
    def test_v1_database_is_upgraded_without_losing_data(self):
        path = str(Path(self.tmp.name) / "v1.db")
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE email_log (id INTEGER PRIMARY KEY, gmail_id TEXT UNIQUE NOT NULL, sender TEXT, subject TEXT,
              needs_reply INTEGER, confidence REAL, reason TEXT, draft_reply TEXT, action TEXT NOT NULL, processed_at TEXT NOT NULL);
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE reminders (id INTEGER PRIMARY KEY, title TEXT, remind_at TEXT, notes TEXT, completed INTEGER, created_at TEXT);
            INSERT INTO email_log (gmail_id, sender, subject, action, processed_at, draft_reply) VALUES
              ('a', 'x@y.com', 'Hi', 'AUTO-SEND', '2030-01-01', 'draft'), ('b', 'x@y.com', 'Yo', 'ESCALATE', '2030-01-01', 'd');
            INSERT INTO reminders VALUES (1, 'Call accountant', '2030-01-02T09:00', '', 0, '2030-01-01');
            INSERT INTO settings VALUES ('calibrated_voice_profile', '{"sample_count": 0, "style_summary": "invented"}');
            INSERT INTO settings VALUES ('auto_send_enabled', '1');
        """)
        old.commit()
        old.close()
        conn = self._orig_init(path)
        self.assertEqual({r[0] for r in conn.execute("SELECT action FROM email_log")}, {"DRAFT"})
        self.assertEqual([t["title"] for t in pipeline.list_tasks(conn)], ["Call accountant"])
        self.assertIsNone(pipeline.get_setting(conn, "calibrated_voice_profile"))
        self.assertIsNone(pipeline.get_setting(conn, "auto_send_enabled"))
        conn.close()
        conn = self._orig_init(path)  # second open must not duplicate migrated rows
        self.assertEqual(len(pipeline.list_tasks(conn)), 1)
        conn.close()


class DraftContextTests(Base):
    def test_prompt_uses_profile_knowledge_templates_and_thread_but_not_unrelated_docs(self):
        pipeline.save_business_profile(self.conn, {"business_name": "Brightside Cleaning",
                                                   "services_prices": "Move-out clean $240"})
        pipeline.add_document(self.conn, "pricing.txt", "computer", "text/plain",
                              "Move-out cleaning includes inside cabinets and oven. Deposit 25 percent.")
        pipeline.add_document(self.conn, "recipes.txt", "computer", "text/plain", "Banana bread needs ripe bananas.")
        pipeline.save_template(self.conn, "Move-out pricing", "Hi {first_name}, our move-out cleaning is $240.")
        pipeline.set_setting(self.conn, "user_signature", "Cheers,\nSam")
        email = {"id": "z", "sender": "Maria <maria@example.com>", "subject": "Move-out cleaning quote",
                 "body": "How much is move-out cleaning? Does it include the oven?", "received_at": "2030-01-01"}
        thread = [{"sender": "Maria <maria@example.com>", "body": "Earlier: we talked about Friday."}]
        text = pipeline.build_draft_messages(self.conn, email, "Friendly", "mention the deposit", thread,
                                             include_calendar=False)[1]["content"]
        self.assertIn("Brightside Cleaning", text)
        self.assertIn("pricing.txt", text)
        self.assertNotIn("Banana", text)
        self.assertIn("Move-out pricing", text)
        self.assertIn("Earlier: we talked about Friday.", text)
        self.assertIn("mention the deposit", text)
        self.assertIn("Cheers,\nSam", text)
        self.assertTrue(text.index("EARLIER MESSAGES") < text.index("LATEST MESSAGE"))

    def test_template_placeholders_render(self):
        self.add_email()
        pipeline.set_setting(self.conn, "user_name", "Sam")
        tid = pipeline.save_template(self.conn, "Hello", "Hi {first_name},\nThanks!\n{my_name}")
        self.assertEqual(pipeline.render_template(self.conn, tid, "m1"), "Hi Maria,\nThanks!\nSam")
        self.assertEqual(pipeline.list_templates(self.conn)[0]["uses"], 1)

    def test_regenerate_forces_a_reply_and_stores_tasks(self):
        self.add_email(action="NO_REPLY_NEEDED")
        pipeline.move_to_reply(self.conn, "m1")
        with patch.object(pipeline, "_complete_with_llm",
                          return_value=llm_reply({**DRAFT_JSON, "needs_reply": False})) as llm:
            item = pipeline.regenerate_draft(self.conn, "m1", "Concise", "say yes")
        self.assertIn("The user has decided to reply", llm.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(item["action"], "DRAFT")
        self.assertIn("$240", item["draft_reply"])
        self.assertEqual(item["suggested_tasks"][0]["title"], "Hold Friday slot")
        self.assertEqual(item["ai_draft"], item["draft_reply"])


class FakeMailbox:
    kind = "imap"
    provider = "other"
    address = "me@biz.example"

    def __init__(self, emails=()):
        self.emails = list(emails)
        self.sent, self.archived, self.trashed = [], [], []
        self.fail_archive = False

    def fetch_recent(self, max_results, is_known=lambda _id: False):
        return [e for e in self.emails if not is_known(e["id"])]

    def fetch_thread(self, conn, email_item):
        return pipeline.local_thread(conn, email_item)

    def send_reply(self, email_item, text, from_name=""):
        self.sent.append((email_item, text, from_name))

    def archive(self, email_item):
        if self.fail_archive:
            raise Exception("NO [CANNOT] permission denied")
        self.archived.append(email_item["id"])

    def trash(self, email_item):
        self.trashed.append(email_item["id"])

    def sent_samples(self, limit=30):
        return []

    def close(self):
        pass


def mail(gid, sender="Maria <maria@example.com>", subject="Quote?", body="How much for Friday?", **extra):
    return {"id": gid, "sender": sender, "subject": subject, "body": body, "thread_id": f"t{gid}",
            "message_id": f"<{gid}@x>", "received_at": "2030-01-01T10:00:00+00:00", **extra}


CLASSIFY = {"needs_reply": True, "urgency": "IMPORTANT", "summary": "Maria asks for a quote.", "reason": "q",
            "tasks": []}


def fake_llm(classify=CLASSIFY, draft=DRAFT_JSON, log=None):
    def call(**kw):
        system = kw["messages"][0]["content"]
        is_classify = system.startswith("You sort incoming email")
        if log is not None:
            log.append(("classify" if is_classify else "draft", kw["messages"][-1]["content"]))
        if callable(classify) and is_classify:
            return llm_reply(classify(kw))
        return llm_reply(classify if is_classify else draft)
    return call


class TriageTests(Base):
    def test_sorting_pass_then_drafts_urgent_first_and_bulk_costs_nothing(self):
        box = FakeMailbox([mail("a", subject="Newsletter", list_unsubscribe="<mailto:u>"),
                           mail("b", subject="Normal question"), mail("c", subject="URGENT leak")])
        calls = []
        urgent = lambda kw: {**CLASSIFY, "urgency": "URGENT" if "URGENT" in kw["messages"][-1]["content"] else "IMPORTANT"}
        with patch.object(pipeline, "_complete_with_llm", side_effect=fake_llm(urgent, log=calls)):
            stats = pipeline.run_triage(self.conn, box)
        self.assertEqual((stats["skipped"], stats["needs_reply"], stats["drafted"]), (1, 2, 2))
        kinds = [k for k, _ in calls]
        self.assertEqual(kinds, ["classify", "classify", "draft", "draft"])
        self.assertIn("URGENT leak", calls[2][1])  # urgent email drafted first
        item = pipeline.get_email(self.conn, "b")
        self.assertEqual((item["draft_state"], item["ai_draft"]), ("ready", DRAFT_JSON["reply"]))

    def test_draft_on_open_mode_only_sorts(self):
        pipeline.set_setting(self.conn, "draft_mode", "on_open")
        box = FakeMailbox([mail("b")])
        with patch.object(pipeline, "_complete_with_llm", side_effect=fake_llm()) as llm:
            stats = pipeline.run_triage(self.conn, box)
        self.assertEqual((llm.call_count, stats["drafted"]), (1, 0))
        self.assertEqual(pipeline.get_email(self.conn, "b")["draft_state"], "pending")
        with patch.object(pipeline, "_complete_with_llm", side_effect=fake_llm()):
            r = self.client.post("/api/email/b/draft-now")
        self.assertEqual(r.json()["draft_state"], "ready")
        self.assertIn("check", r.json())

    def test_draft_now_reports_busy_instead_of_duplicating_work(self):
        self.add_email()
        pipeline._DRAFTING.add("m1")
        try:
            self.assertEqual(self.client.post("/api/email/m1/draft-now").status_code, 409)
        finally:
            pipeline._DRAFTING.discard("m1")

    def test_fatal_model_error_stops_early_and_is_retried_next_time(self):
        box = FakeMailbox([mail(f"h{i}") for i in range(3)])
        with patch.object(pipeline, "_complete_with_llm", side_effect=Exception("Connection refused")):
            stats = pipeline.run_triage(self.conn, box)
        self.assertTrue(stats["stopped_early"])
        self.assertIn("Start the Ollama app", stats["errors"][0]["message"])
        with patch.object(pipeline, "_complete_with_llm", side_effect=fake_llm()):
            stats = pipeline.run_triage(self.conn, box)
        self.assertEqual(stats["drafted"], 3)
        self.assertEqual(pipeline.inbox_counts(self.conn)["errors"], 0)

    def test_gmail_api_mailbox_parses_and_skips_known(self):
        data = base64.urlsafe_b64encode(b"How much?").decode()
        msg = {"id": "g1", "threadId": "tg", "internalDate": "1893456000000",
               "payload": {"mimeType": "text/plain", "body": {"data": data},
                           "headers": [{"name": "From", "value": "A <a@x.com>"}, {"name": "Subject", "value": "Q"}]}}
        svc = MagicMock()
        svc.users().messages().list().execute.return_value = {"messages": [{"id": "g1"}, {"id": "old"}]}
        svc.users().messages().get.side_effect = lambda userId, id, format: MagicMock(execute=lambda: msg)
        box = mail_accounts.GmailApiMailbox(svc)
        out = box.fetch_recent(10, is_known=lambda gid: gid == "old")
        self.assertEqual([(e["id"], e["body"]) for e in out], [("g1", "How much?")])


class FollowupAndValueTests(Base):
    def test_followup_due_then_resolved_by_reply_in_thread(self):
        self.add_email()
        pipeline.mark_sent(self.conn, "m1", "Hi, does Friday work?", followup_days=3)
        self.assertEqual(pipeline.list_followups(self.conn), [])
        self.assertEqual(len(pipeline.list_followups(self.conn, include_upcoming=True)), 1)
        past = (pipeline._now() - timedelta(days=1)).isoformat()
        self.conn.execute("UPDATE email_log SET followup_at = ? WHERE gmail_id = 'm1'", (past,))
        self.conn.commit()
        self.assertEqual(len(pipeline.list_followups(self.conn)), 1)
        later = (pipeline._now() + timedelta(minutes=5)).isoformat()
        pipeline.log_email(self.conn, {"id": "m2", "sender": "maria@example.com", "subject": "Re: Quote",
                                       "thread_id": "t-m1", "received_at": later}, "NO_REPLY_NEEDED", {})
        self.assertEqual(pipeline.list_followups(self.conn), [])

    def test_time_saved_counts_real_work_only(self):
        self.add_email()
        pipeline.mark_sent(self.conn, "m1", "text")
        self.add_email("s1", action="SKIPPED")
        pipeline.seed_demo_inbox(self.conn)
        saved = pipeline.time_saved(self.conn)
        self.assertEqual(saved["replies_week"], 1)
        self.assertEqual(saved["minutes_week"], round(5 + 0.5))

    def test_task_from_email_uses_suggestion(self):
        self.add_email()
        pipeline.task_from_email(self.conn, "m1", 0)
        task = pipeline.list_tasks(self.conn)[0]
        self.assertEqual((task["title"], task["due_at"], task["source_email"]), ("Hold Friday slot", "2030-01-05", "m1"))


class ApiTests(Base):
    def test_state_changes_require_token_and_local_host(self):
        anon = TestClient(app.app)
        self.assertEqual(anon.post("/api/demo").status_code, 403)
        self.assertEqual(anon.get("/api/inbox").status_code, 200)
        self.assertEqual(self.client.get("/api/inbox", headers={"host": "evil.example"}).status_code, 403)
        page = self.client.get("/").text
        self.assertIn(app.APP_TOKEN, page)

    def test_demo_flow_never_touches_gmail(self):
        self.client.post("/api/demo")
        items = self.client.get("/api/inbox?view=reply").json()["items"]
        self.assertEqual(len(items), 4)
        with patch.object(pipeline, "get_gmail_service") as gmail:
            r = self.client.post("/api/email/demo-quote/send", json={"text": "Hi Maria!", "followup": True})
            self.client.post("/api/email/demo-news/done")
        self.assertTrue(r.json()["demo"])
        gmail.assert_not_called()
        self.assertEqual(self.client.post("/api/email/demo-quote/send", json={"text": "again"}).status_code, 400)
        self.assertEqual([f["gmail_id"] for f in self.client.get("/api/followups").json()][0], "demo-sent")
        today = self.client.get("/api/today").json()
        self.assertEqual(today["counts"]["reply"], 3)
        self.assertEqual(today["followups"][0]["gmail_id"], "demo-sent")
        self.assertEqual(pipeline.get_business_profile(self.conn)["business_name"], "Brightside Home Cleaning")
        self.assertFalse(self.client.get("/api/status").json()["setup"]["business"])
        demo = self.client.get("/api/email/demo-question").json()["check"]["facts"]
        self.assertTrue(next(f for f in demo if f["text"] == "$35")["supported"])
        self.client.delete("/api/demo")
        self.assertEqual(self.client.get("/api/inbox?view=reply").json()["items"], [])
        self.assertEqual(pipeline.get_business_profile(self.conn)["business_name"], "")

    def test_real_send_requires_connection_then_sends_through_mailbox(self):
        self.add_email()
        self.assertEqual(self.client.post("/api/email/m1/send", json={"text": "Hi"}).status_code, 400)
        box = FakeMailbox()
        pipeline.set_setting(self.conn, "user_name", "Sam")
        with patch.object(app, "open_mailbox", return_value=box):
            r = self.client.post("/api/email/m1/send", json={"text": "Hi Maria", "followup": True})
        self.assertEqual(r.status_code, 200, r.text)
        email_item, text, from_name = box.sent[0]
        self.assertEqual((email_item["message_id"], email_item["thread_id"], text, from_name),
                         ("<m1@mail>", "t-m1", "Hi Maria", "Sam"))
        self.assertEqual(r.json()["followup_days"], 3)

    def test_gmail_api_send_threads_reply(self):
        svc = MagicMock()
        mail_accounts.GmailApiMailbox(svc).send_reply(
            {"sender": "Maria <maria@example.com>", "subject": "Quote", "message_id": "<m1@mail>", "thread_id": "t1"}, "Hi")
        body = svc.users().messages().send.call_args.kwargs["body"]
        raw = base64.urlsafe_b64decode(body["raw"]).decode()
        self.assertEqual(body["threadId"], "t1")
        self.assertIn("In-Reply-To: <m1@mail>", raw)
        self.assertIn("Subject: Re: Quote", raw)

    def test_done_reports_server_failure_instead_of_pretending(self):
        self.add_email()
        box = FakeMailbox()
        box.fail_archive = True
        with patch.object(app, "open_mailbox", return_value=box):
            r = self.client.post("/api/email/m1/done")
        self.assertEqual(r.status_code, 502)
        self.assertFalse(pipeline.get_email(self.conn, "m1")["is_archived"])
        box.fail_archive = False
        with patch.object(app, "open_mailbox", return_value=box):
            self.assertTrue(self.client.post("/api/email/m1/done").json()["archived_on_server"])
        self.assertEqual(box.archived, ["m1"])

    def test_api_key_never_leaves_the_backend(self):
        self.client.post("/api/settings", json={"model": "gpt-4o-mini", "api_key": "sk-secret-123"})
        self.assertNotIn("sk-secret", self.client.get("/api/settings").text)
        self.assertNotIn("sk-secret", self.client.get("/api/export").text)
        self.assertTrue(self.client.get("/api/settings").json()["api_key_configured"])

    def test_credentials_upload_validation(self):
        bad = self.client.post("/api/google/credentials", files={"file": ("c.json", b'{"web": {}}')})
        self.assertIn("Desktop app", bad.json()["detail"])
        good = json.dumps({"installed": {"client_id": "abc.apps.googleusercontent.com", "client_secret": "s"}})
        self.assertEqual(self.client.post("/api/google/credentials",
                                          files={"file": ("c.json", good.encode())}).status_code, 200)
        self.assertTrue((Path(self.tmp.name) / "credentials.json").exists())

    def test_model_test_marks_setup_and_failed_test_keeps_old_model(self):
        with patch.object(pipeline, "_complete_with_llm", side_effect=Exception("model 'x' not found")):
            r = self.client.post("/api/models/test", json={"model": "ollama/x"})
        self.assertIn("ollama pull x", r.json()["detail"])
        self.assertNotEqual(pipeline.get_setting(self.conn, "model"), "ollama/x")
        with patch.object(pipeline, "_complete_with_llm", return_value=llm_reply("PONG")):
            r = self.client.post("/api/models/test", json={"model": "ollama/llama3.1:8b"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.client.get("/api/status").json()["setup"]["ai"])

    def test_model_test_refuses_local_model_too_big_for_ram(self):
        with patch.object(pipeline, "local_model_size", return_value=18 * 10**9), \
                patch.object(pipeline, "total_ram_bytes", return_value=16 * 2**30), \
                patch.object(pipeline, "_complete_with_llm") as llm:
            r = self.client.post("/api/models/test", json={"model": "ollama/big:30b"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("needs about 18 GB", r.json()["detail"])
        llm.assert_not_called()

    def test_ask_cites_sources_and_handles_empty_knowledge(self):
        self.assertIn("nothing in Knowledge", self.client.post("/api/ask", json={"question": "refund policy?"}).json()["answer"])
        self.client.put("/api/profile", json={"policies": "Refunds within 7 days."})
        with patch.object(pipeline, "_complete_with_llm", return_value=llm_reply("Within 7 days [1].")) as llm:
            r = self.client.post("/api/ask", json={"question": "What is the refund policy?"}).json()
        self.assertEqual(r["sources"], ["Business Profile"])
        self.assertIn("Refunds within 7 days", llm.call_args.kwargs["messages"][1]["content"])

    def test_tasks_crud(self):
        tid = self.client.post("/api/tasks", json={"title": "Call Derek", "due_at": "2030-01-02"}).json()["id"]
        self.client.post(f"/api/tasks/{tid}/toggle", json={"completed": True})
        self.assertTrue(self.client.get("/api/tasks").json()[0]["completed"])
        self.assertEqual(self.client.delete("/api/tasks/completed").json()["deleted"], 1)


if __name__ == "__main__":
    unittest.main()


RAW_EMAIL = (b"From: Derek Shaw <derek@example.com>\r\nTo: me@biz.example\r\nSubject: Re: Bathroom\r\n"
             b"Date: Tue, 01 Jan 2030 10:00:00 +0000\r\nMessage-ID: <d2@example.com>\r\n"
             b"In-Reply-To: <me1@biz.example>\r\nReferences: <d1@example.com> <me1@biz.example>\r\n"
             b"Content-Type: text/html; charset=utf-8\r\n\r\n<p>Thursday works.</p><p>On Mon, Derek wrote:</p>")


class FakeIMAP:
    def __init__(self):
        self.capabilities = ("IMAP4REV1", "MOVE")
        self.calls = []

    def select(self, name, readonly=False):
        self.calls.append(("select", name, readonly))
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"777"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Trash) "/" "Deleted Items"',
                      b'(\\HasNoChildren \\Archive) "/" "Archive"', b'(\\HasNoChildren \\Sent) "/" "Sent Items"']

    def uid(self, command, *args):
        self.calls.append(("uid", command) + args)
        if command == "SEARCH":
            return "OK", [b"41 42"]
        if command == "FETCH":
            return "OK", [(b"1 (UID %s BODY[] {10}" % args[0].encode(), RAW_EMAIL), b")"]
        return "OK", [b""]

    def logout(self):
        pass


class AccountTests(Base):
    def test_provider_detection(self):
        self.assertEqual(mail_accounts.detect_provider("Sam@Gmail.com")["imap_host"], "imap.gmail.com")
        ms = mail_accounts.detect_provider("sam@hotmail.com")
        self.assertEqual((ms["method"], ms["smtp_host"]), ("microsoft", "smtp-mail.outlook.com"))
        other = mail_accounts.detect_provider("sam@brightside.co")
        self.assertEqual((other["provider"], other["imap_host"]), ("other", "mail.brightside.co"))
        with self.assertRaises(ValueError):
            mail_accounts.detect_provider("not-an-email")

    def test_password_connect_tests_login_and_keeps_password_out_of_api(self):
        with patch.object(mail_accounts.ImapMailbox, "test", return_value=True):
            r = self.client.post("/api/account/password",
                                 json={"address": "sam@gmail.com", "password": "abcd efgh ijkl mnop"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(pipeline.get_secret(self.conn, "mail_password"), "abcdefghijklmnop")  # spaces removed for Gmail
        status = self.client.get("/api/status").json()
        self.assertEqual((status["connected"], status["account"]["kind"]), (True, "imap"))
        self.assertNotIn("abcdefgh", self.client.get("/api/account").text + self.client.get("/api/export").text)
        self.assertNotIn("abcdefgh", json.dumps(pipeline.export_local_data(self.conn)))
        self.client.delete("/api/account")
        self.assertFalse(self.client.get("/api/status").json()["connected"])

    def test_bad_password_gets_provider_specific_help(self):
        with patch.object(mail_accounts.ImapMailbox, "test",
                          side_effect=Exception("b'[AUTHENTICATIONFAILED] Invalid credentials (Failure)'")):
            r = self.client.post("/api/account/password", json={"address": "sam@gmail.com", "password": "pw"})
        self.assertIn("app password", r.json()["detail"])
        self.assertIsNone(mail_accounts.get_account(self.conn))

    def test_microsoft_accounts_cannot_use_passwords(self):
        r = self.client.post("/api/account/password", json={"address": "sam@outlook.com", "password": "pw"})
        self.assertIn("Sign in with Microsoft", r.json()["detail"])
        r = self.client.post("/api/account/microsoft/start")
        self.assertIn("isn't configured", r.json()["detail"])

    def test_imap_fetch_threads_and_never_marks_read(self):
        account = {"kind": "imap", "provider": "other", "address": "me@biz.example", "imap_host": "x", "imap_port": 993,
                   "smtp_host": "x", "smtp_port": 465}
        box = mail_accounts.ImapMailbox(self.conn, account, password="pw")
        box._imap = FakeIMAP()
        items = box.fetch_recent(10, is_known=lambda gid: gid == "imap:777:41")
        self.assertEqual([i["id"] for i in items], ["imap:777:42"])
        item = items[0]
        self.assertEqual(item["thread_id"], "imap-thread:<d1@example.com>")
        self.assertEqual(item["body"], "Thursday works.")
        fetches = [c for c in box._imap.calls if c[:2] == ("uid", "FETCH")]
        self.assertTrue(all("PEEK" in c[3] for c in fetches))
        self.assertTrue(box._imap.calls[0][2])  # INBOX opened read-only for fetching
        box.archive({"id": "imap:777:42"})
        box.trash({"id": "imap:777:42"})
        moves = [c for c in box._imap.calls if c[:2] == ("uid", "MOVE")]
        self.assertEqual(moves, [("uid", "MOVE", "42", '"Archive"'), ("uid", "MOVE", "42", '"Deleted Items"')])

    def test_imap_reply_headers(self):
        account = {"kind": "imap", "provider": "other", "address": "me@biz.example", "imap_host": "x", "smtp_host": "x"}
        box = mail_accounts.ImapMailbox(self.conn, account, password="pw")
        msg = box.build_reply({"sender": "Derek <derek@example.com>", "subject": "Bathroom", "message_id": "<d2@x>",
                               "references": "<d1@x>"}, "Hi Derek", from_name="Sam")
        self.assertEqual((msg["To"], msg["Subject"], msg["In-Reply-To"], msg["References"]),
                         ("derek@example.com", "Re: Bathroom", "<d2@x>", "<d1@x> <d2@x>"))
        self.assertIn("Sam", msg["From"])

    def test_imap_thread_context_comes_from_local_history(self):
        pipeline.log_email(self.conn, {"id": "x1", "sender": "Derek <d@x.com>", "subject": "Bathroom",
                                       "body": "The bathroom was skipped.", "thread_id": "T",
                                       "received_at": "2030-01-01T09:00:00+00:00"}, "DRAFT", DRAFT_JSON)
        pipeline.mark_sent(self.conn, "x1", "Sorry! We'll come back Thursday.")
        thread = pipeline.local_thread(self.conn, {"id": "x2", "thread_id": "T"})
        self.assertEqual([t["body"] for t in thread], ["The bathroom was skipped.", "Sorry! We'll come back Thursday."])

    def test_api_key_moves_out_of_plain_settings(self):
        pipeline.set_setting(self.conn, "api_key", "sk-legacy")
        _, _, key = pipeline.get_model_settings(self.conn)
        self.assertEqual(key, "sk-legacy")
        self.assertIsNone(pipeline.get_setting(self.conn, "api_key"))
        self.assertNotIn("sk-legacy", json.dumps(pipeline.export_local_data(self.conn)))


class FactTests(Base):
    def setUp(self):
        super().setUp()
        pipeline.save_business_profile(self.conn, {"services_prices": "Move-out clean $240. Oven add-on $35.",
                                                   "policies": "Free re-clean within 48 hours."})
        pipeline.set_setting(self.conn, "user_signature", "Sam · (512) 555-0134")
        self.add_email(body="Can you come Friday afternoon? How much is a move-out clean?")
        self.item = pipeline.get_email(self.conn, "m1")

    def test_supported_and_unverified_facts(self):
        text = ("Hi Maria, a move-out clean is $240 and we can come Friday at 1 PM. We'll add a 10% discount. "
                "Call (512) 555-0134.")
        result = facts.check_text(self.conn, self.item, text)
        by_text = {f["text"]: (f["supported"], f["source"]) for f in result["facts"]}
        self.assertEqual(by_text["$240"], (True, "your profile"))
        self.assertEqual(by_text["Friday"], (True, "the email"))
        self.assertEqual(by_text["(512) 555-0134"], (True, "your signature"))
        self.assertFalse(by_text["1 PM"][0])
        self.assertFalse(by_text["10%"][0])
        self.assertFalse(by_text["discount"][0])
        self.assertEqual(result["unverified"], 3)

    def test_email_detail_includes_fact_check(self):
        r = self.client.get("/api/email/m1").json()
        self.assertIn("check", r)
        self.assertTrue(any(f["text"] == "$240" for f in r["check"]["facts"]))

    def test_price_change_is_learned_into_profile(self):
        self.conn.execute("UPDATE email_log SET ai_draft = ? WHERE gmail_id = 'm1'",
                          ("Hi Maria,\n\nA move-out clean is $240. Friday works.\n\nBest regards,\nSam",))
        self.conn.commit()
        sent = "Hi Maria,\n\nA move-out clean is $260 now. Friday works.\n\nBest regards,\nSam"
        with patch.object(app, "open_mailbox", return_value=FakeMailbox()):
            r = self.client.post("/api/email/m1/send", json={"text": sent}).json()
        self.assertFalse(r["demo"])
        learn = r["learn"]
        self.assertGreater(learn["similarity"], 0.85)
        self.assertEqual(learn["suggestions"][0]["type"], "replace")
        self.assertEqual((learn["suggestions"][0]["old"], learn["suggestions"][0]["new"]), ("$240", "$260"))
        self.client.post("/api/learn", json=learn["suggestions"][0])
        self.assertIn("$260", pipeline.get_business_profile(self.conn)["services_prices"])
        self.assertEqual(pipeline.time_saved(self.conn)["drafts_light_edit_30d"], 1)

    def test_heavy_rewrite_offers_saved_reply_and_new_facts_are_addable(self):
        item = {**self.item, "ai_draft": "Hi Maria, thanks for asking. We'd love to help."}
        sent = ("Hi Maria, our move-out cleans include inside cabinets and the oven. We need 24 hours notice "
                "to cancel, and parking passes are on you. Bookings: https://brightside.example/book. Thanks!")
        learn = facts.learn_from_send(self.conn, item, sent)
        self.assertTrue(learn["offer_template"])
        kinds = {(s["type"], s["field"]) for s in learn["suggestions"]}
        self.assertIn(("add", "links"), kinds)
        self.assertIn(("add", "hours_location"), kinds)


class SnoozeTests(Base):
    def test_snooze_hides_until_time_and_presets(self):
        self.add_email()
        self.client.post("/api/email/m1/snooze", json={"until": "tomorrow"})
        self.assertEqual(self.client.get("/api/inbox?view=reply").json()["items"], [])
        self.assertEqual(self.client.get("/api/inbox?view=snoozed").json()["items"][0]["gmail_id"], "m1")
        self.assertEqual(pipeline.inbox_counts(self.conn)["snoozed"], 1)
        past = (pipeline._now() - timedelta(minutes=1)).isoformat()
        self.conn.execute("UPDATE email_log SET snooze_until = ? WHERE gmail_id = 'm1'", (past,))
        self.conn.commit()
        self.assertEqual(len(self.client.get("/api/inbox?view=reply").json()["items"]), 1)
        self.assertEqual(self.client.post("/api/email/m1/snooze", json={"until": "2001-01-01T09:00"}).status_code, 400)

    def test_snooze_monday_lands_on_a_monday_morning(self):
        from datetime import datetime
        friday = datetime(2030, 1, 4, 15, 0).astimezone()
        monday = datetime.fromisoformat(pipeline.snooze_time("monday", friday)).astimezone()
        self.assertEqual((monday.weekday(), monday.hour), (0, 8))


class IndustryDemoTests(Base):
    def own_setup(self):
        pipeline.save_business_profile(self.conn, {"business_name": "Elvis Consulting", "about": "AI automation"})
        pipeline.set_setting(self.conn, "user_signature", "Thanks,\nElvis")
        pipeline.set_setting(self.conn, "user_name", "Elvis")
        pipeline.save_template(self.conn, "My own reply", "Hi {first_name}, thanks!")
        pipeline.add_document(self.conn, "my-prices.txt", "computer", "text/plain", "Audit $500.")

    def test_industry_demo_loads_firm_switches_and_restores_your_settings(self):
        self.own_setup()
        r = self.client.post("/api/demo", json={"industry": "tax"}).json()
        self.assertEqual(r["business"], "Harbor Lane Tax & Bookkeeping")
        items = self.client.get("/api/inbox?view=reply").json()["items"]
        self.assertEqual(len(items), 7)
        self.assertTrue(all(i["gmail_id"].startswith("demo-tax-") for i in items))
        self.assertEqual(pipeline.get_business_profile(self.conn)["business_name"], "Harbor Lane Tax & Bookkeeping")
        self.assertIn("Dana Whitfield", pipeline._sign_off(self.conn))
        titles = {t["title"] for t in pipeline.list_templates(self.conn)}
        self.assertTrue({"My own reply", "Missing documents reminder"} <= titles)
        self.assertEqual(len(pipeline.list_documents(self.conn)), 2)
        status = self.client.get("/api/status").json()
        self.assertEqual(status["demo"]["id"], "tax")
        self.assertFalse(status["setup"]["business"])
        self.assertEqual({d["id"] for d in status["demo_industries"]}, {"cleaning", "tax", "insurance"})
        self.assertEqual(self.client.get("/api/followups").json()[0]["gmail_id"], "demo-tax-sent")

        # Switching samples replaces the first one completely.
        self.client.post("/api/demo", json={"industry": "insurance"})
        items = self.client.get("/api/inbox?view=reply").json()["items"]
        self.assertTrue(items and all(i["gmail_id"].startswith("demo-ins-") for i in items))
        titles = {t["title"] for t in pipeline.list_templates(self.conn)}
        self.assertNotIn("Missing documents reminder", titles)
        self.assertIn("COI request — details needed", titles)
        self.assertIn("Marcus Bell", pipeline._sign_off(self.conn))

        # Removing samples brings back exactly what you had.
        self.client.delete("/api/demo")
        self.assertEqual(self.client.get("/api/inbox?view=reply").json()["items"], [])
        self.assertEqual(pipeline.get_business_profile(self.conn)["business_name"], "Elvis Consulting")
        self.assertEqual(pipeline._sign_off(self.conn), "Thanks,\nElvis")
        self.assertEqual(pipeline.get_setting(self.conn, "user_name"), "Elvis")
        self.assertEqual([t["title"] for t in pipeline.list_templates(self.conn)], ["My own reply"])
        self.assertEqual([d["name"] for d in pipeline.list_documents(self.conn)], ["my-prices.txt"])
        self.assertIsNone(self.client.get("/api/status").json()["demo"])

    def test_unset_signature_is_unset_again_after_demo(self):
        self.client.post("/api/demo", json={"industry": "insurance"})
        self.client.delete("/api/demo")
        self.assertIsNone(pipeline.get_setting(self.conn, "user_signature"))
        self.assertIsNone(pipeline.get_setting(self.conn, "user_name"))
        self.assertEqual(pipeline.get_business_profile(self.conn)["business_name"], "")

    def test_unknown_industry_is_rejected(self):
        self.assertEqual(self.client.post("/api/demo", json={"industry": "rocketry"}).status_code, 400)

    def test_industry_drafts_quote_facts_from_the_sample_profile(self):
        self.client.post("/api/demo", json={"industry": "tax"})
        facts = {f["text"]: f["supported"] for f in self.client.get("/api/email/demo-tax-quote").json()["check"]["facts"]}
        self.assertTrue(facts["$450"] and facts["$125"])
        self.assertFalse(facts["$575"])  # a computed total is flagged for a human to check
        self.client.post("/api/demo", json={"industry": "insurance"})
        facts = {f["text"]: f["supported"] for f in self.client.get("/api/email/demo-ins-claim").json()["check"]["facts"]}
        self.assertTrue(facts["(555) 010-4477"])

    def test_sample_drafts_never_ask_for_sensitive_numbers_by_email(self):
        for key in ("tax", "insurance"):
            for item in pipeline.DEMO_PACKS[key]["emails"]:
                self.assertNotIn("123-45-6789", item["reply"])
                self.assertNotIn("000123456789", item["reply"])


class SecurityHeaderTests(Base):
    def test_pages_cannot_be_framed_and_api_is_not_cached(self):
        page = self.client.get("/")
        self.assertEqual(page.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", page.headers["content-security-policy"])
        self.assertEqual(page.headers["x-content-type-options"], "nosniff")
        self.assertEqual(page.headers["cache-control"], "no-cache")
        self.assertEqual(self.client.get("/api/tracker").headers["cache-control"], "no-store")
        refused = self.client.get("/api/tracker", headers={"host": "evil.example"})
        self.assertEqual((refused.status_code, refused.headers["x-frame-options"]), (403, "DENY"))


class StarterKitTests(Base):
    def test_kit_adds_replies_rules_and_checklist_without_overwriting(self):
        pipeline.save_template(self.conn, "Missing documents reminder", "My own wording")
        pipeline.save_business_profile(self.conn, {"business_name": "Lake Tax", "never_say": "Never discuss fees."})
        r = self.client.post("/api/industry/kit", json={"industry": "tax"}).json()
        kit = pipeline.industry.KITS["tax"]
        self.assertEqual(r["templates_added"], len(kit["templates"]) - 1)
        self.assertFalse(r["rules_added"])
        self.assertTrue(r["checklist_added"])
        mine = [t for t in pipeline.list_templates(self.conn) if t["title"] == "Missing documents reminder"]
        self.assertEqual([t["body"] for t in mine], ["My own wording"])
        self.assertEqual(pipeline.get_business_profile(self.conn)["never_say"], "Never discuss fees.")
        self.assertEqual(pipeline.get_industry(self.conn), "tax")
        again = self.client.post("/api/industry/kit", json={"industry": "tax"}).json()
        self.assertEqual((again["templates_added"], again["checklist_added"]), (0, False))
        self.assertEqual(len(pipeline.list_documents(self.conn)), 1)
        opts = self.client.get("/api/industry").json()
        tax = next(k for k in opts["kits"] if k["id"] == "tax")
        self.assertEqual(tax["installed"], tax["templates"])

    def test_kit_added_during_sample_inbox_survives_clearing_it(self):
        for key in ("tax", "insurance"):
            with self.subTest(key):
                self.conn.execute("DELETE FROM templates"); self.conn.execute("DELETE FROM documents")
                pipeline.save_business_profile(self.conn, {"business_name": "Real Biz"})
                pipeline.set_industry(self.conn, "")
                self.client.post("/api/demo", json={"industry": key})
                r = self.client.post("/api/industry/kit", json={"industry": key}).json()
                kit = pipeline.industry.KITS[key]
                self.assertEqual((r["templates_added"], r["rules_added"]), (len(kit["templates"]), True))
                self.client.delete("/api/demo")
                titles = {t["title"] for t in pipeline.list_templates(self.conn)}
                self.assertTrue({t for t, _ in kit["templates"]} <= titles)
                profile = pipeline.get_business_profile(self.conn)
                self.assertEqual((profile["business_name"], profile["never_say"]), ("Real Biz", kit["never_say"]))
                self.assertEqual(pipeline.get_industry(self.conn), key)
                self.assertEqual([d["name"] for d in pipeline.list_documents(self.conn)], [kit["checklist"][0]])
                opts = next(k for k in self.client.get("/api/industry").json()["kits"] if k["id"] == key)
                self.assertEqual(opts["installed"], opts["templates"])
                pipeline.save_business_profile(self.conn, {})

    def test_empty_profile_gets_safety_rules(self):
        self.client.post("/api/industry/kit", json={"industry": "insurance"})
        self.assertIn("carrier confirms", pipeline.get_business_profile(self.conn)["never_say"])
        self.assertEqual(self.client.post("/api/industry/kit", json={"industry": "x"}).status_code, 400)

    def test_kit_templates_render_placeholders(self):
        pipeline.save_business_profile(self.conn, {"business_name": "Lake Tax"})
        pipeline.set_setting(self.conn, "user_name", "Dana")
        self.client.post("/api/industry/kit", json={"industry": "tax"})
        self.add_email()
        tid = next(t["id"] for t in pipeline.list_templates(self.conn) if t["title"].startswith("Welcome"))
        text = pipeline.render_template(self.conn, tid, "m1")
        self.assertIn("Hi Maria,", text)
        self.assertIn("Thank you for choosing Lake Tax.", text)
        self.assertNotIn("{", text)

    def test_industry_guidance_and_sensitive_warning_reach_the_prompt(self):
        email = {"id": "x", "sender": "Rosa <rosa@example.com>", "subject": "Info",
                 "body": "My SSN is 123-45-6789, thanks"}
        plain = pipeline.build_draft_messages(self.conn, email, include_calendar=False)[1]["content"]
        self.assertNotIn("INDUSTRY GUIDANCE", plain)
        self.assertIn("SENSITIVE DATA WARNING", plain)
        self.assertNotIn("123-45-6789", plain.split("SENSITIVE DATA WARNING")[1])
        pipeline.set_industry(self.conn, "tax")
        guided = pipeline.build_draft_messages(self.conn, email, include_calendar=False)[1]["content"]
        self.assertIn("INDUSTRY GUIDANCE (Tax & bookkeeping)", guided)
        seen = {}
        def fake(**kwargs):
            seen["messages"] = kwargs["messages"]
            return llm_reply({"needs_reply": True, "urgency": "URGENT", "summary": "s", "reason": "r", "tasks": []})
        with patch.object(pipeline, "_complete_with_llm", fake):
            pipeline.classify_email(self.conn, email)
        self.assertIn("Business type: Tax/bookkeeping firm", seen["messages"][1]["content"])

    def test_send_guard_blocks_sensitive_numbers_until_confirmed(self):
        self.client.post("/api/demo")
        bad = self.client.post("/api/email/demo-quote/send", json={"text": "Your SSN 123-45-6789 is on file."})
        self.assertEqual(bad.status_code, 409)
        self.assertIn("6789", bad.json()["detail"])
        self.assertNotIn("123-45-6789", bad.json()["detail"])
        ok = self.client.post("/api/email/demo-quote/send",
                              json={"text": "Your SSN 123-45-6789 is on file.", "allow_sensitive": True})
        self.assertEqual(ok.status_code, 200)

    def test_sensitive_emails_are_flagged_in_inbox_and_reader(self):
        self.client.post("/api/demo", json={"industry": "tax"})
        items = {i["gmail_id"]: i for i in self.client.get("/api/inbox?view=reply").json()["items"]}
        self.assertTrue(items["demo-tax-ssn"]["sensitive"])
        self.assertFalse(items["demo-tax-quote"]["sensitive"])
        detail = self.client.get("/api/email/demo-tax-ssn").json()["sensitive"]
        self.assertIn("Social Security", detail["summary"])
        self.assertEqual(pipeline.get_industry(self.conn), "tax")  # demo follows the sample business
        self.client.delete("/api/demo")
        self.assertEqual(pipeline.get_industry(self.conn), "")

    def test_find_sensitive_catches_numbers_and_ignores_ordinary_ones(self):
        find = pipeline.industry.find_sensitive
        kinds = lambda t: [h["kind"] for h in find(t)]
        self.assertEqual(kinds("SSN 123-45-6789"), ["ssn"])
        self.assertEqual(kinds("my social security number is 123456789"), ["ssn"])
        self.assertEqual(kinds("routing 021000021 account 0001234567"), ["routing", "account"])
        self.assertEqual(kinds("card 4111 1111 1111 1111"), ["card"])
        self.assertEqual(kinds("DL number: D1234567"), ["license"])
        self.assertEqual(kinds("portal password is hunter22"), ["password"])
        for ordinary in ["Call (555) 010-4477 or 555-010-4477", "Fee $1,200 due 2026-04-15", "Policy 123456",
                         "CA License #0A12345", "Invoice 1001, 3 bedrooms, 1,450 sq ft", "card 4111 1111 1111 1112"]:
            self.assertEqual(find(ordinary), [], ordinary)
        self.assertTrue(all("6789" in h["masked"] and "123" not in h["masked"] for h in find("SSN 123-45-6789")))

    def test_security_summary_reflects_the_ai_engine(self):
        pipeline.save_business_profile(self.conn, {"business_name": "Lake Tax"})
        pipeline.set_setting(self.conn, "model", "ollama/qwen3.5:9b")
        text = self.client.get("/api/security-summary").text
        self.assertIn("Lake Tax — how C.A.P. handles client email", text)
        self.assertIn("not sent to any AI company", text)
        self.assertIn(str(pipeline.DATA_DIR), text)
        pipeline.set_setting(self.conn, "model", "anthropic/claude-haiku-5-5")
        text = self.client.get("/api/security-summary?download=true")
        self.assertIn("sent to Anthropic", text.text)
        self.assertIn("attachment", text.headers["content-disposition"])


class FraudShieldTests(Base):
    def test_assess_flags_real_attacks_and_leaves_normal_mail_alone(self):
        contacts = pipeline.fraud.Contacts({"priya@ramandesign.example": 2, "maria@clientco.com": 3},
                                           {"priya raman": {"priya@ramandesign.example"}})
        level = lambda e: pipeline.fraud.assess(e, contacts)["level"]
        self.assertEqual(level({"sender": "Priya Raman <priya@ramandesiqn.example>", "subject": "Bank",
                                "body": "Please update our bank details and send the refund to our new account."}), "high")
        self.assertEqual(level({"sender": "IRS e-Services <alerts@irs-gov-secure.com>", "subject": "Action required",
                                "body": "Verify your account here: https://irs-gov-secure.com/login"}), "high")
        self.assertEqual(level({"sender": "Maria <maria@clientco.com>", "subject": "favor",
                                "body": "Can you buy gift cards for a client today?"}), "high")
        self.assertEqual(level({"sender": "Luis <luis@rivera.example>", "reply_to": "luis@protonmail.com",
                                "subject": "Claim", "body": "Send the claim payment to a different account."}), "high")
        self.assertEqual(level({"sender": "New <jd@new.example>", "subject": "New client - my tax documents",
                                "body": "My W-2 is attached.", "attachments": ["W2.zip"]}), "medium")
        for benign in [{"sender": "Michelle <m@example.com>", "subject": "IRS letter",
                        "body": "I got a CP2000 from the IRS saying I owe $2,300. Should I pay it?"},
                       {"sender": "Maria <maria@clientco.com>", "subject": "Invoice", "body": "I paid the invoice today."},
                       {"sender": "Dave <d@example.com>", "subject": "Renewal", "body": "My renewal payment is due today."},
                       {"sender": "Dana <d@harbor.example>", "subject": "Refund", "body": "See https://www.irs.gov/refunds"}]:
            self.assertIsNone(level(benign), benign["subject"])

    def test_demo_fraud_is_flagged_in_inbox_reader_and_prompt(self):
        self.client.post("/api/demo", json={"industry": "tax"})
        items = {i["gmail_id"]: i for i in self.client.get("/api/inbox?view=reply").json()["items"]}
        self.assertEqual(items["demo-tax-fraud"]["fraud"], "high")
        self.assertIsNone(items["demo-tax-quote"]["fraud"])
        fyi = {i["gmail_id"]: i for i in self.client.get("/api/inbox?view=fyi").json()["items"]}
        self.assertEqual(fyi["demo-tax-phish"]["fraud"], "high")
        detail = self.client.get("/api/email/demo-tax-fraud").json()["fraud"]
        self.assertIn("lookalike", [s["code"] for s in detail["signals"]])
        self.assertTrue(any("phone" in a for a in detail["advice"]))
        task = self.client.post("/api/email/demo-tax-fraud/verify-task").json()
        self.assertTrue(task["task_id"])
        email = pipeline.get_email(self.conn, "demo-tax-fraud")
        prompt = pipeline.build_draft_messages(self.conn, {**email, "id": "demo-tax-fraud"}, include_calendar=False)
        self.assertIn("FRAUD WARNING", prompt[1]["content"])
        alerts = self.client.get("/api/security").json()["alerts"]["fraud"]
        self.assertTrue({"demo-tax-fraud", "demo-tax-phish"} <= {a["gmail_id"] for a in alerts})
        self.client.post("/api/demo", json={"industry": "insurance"})
        self.assertEqual(self.client.get("/api/email/demo-ins-fraud").json()["fraud"]["level"], "high")

    def test_classify_marks_likely_fraud_urgent(self):
        self.add_email("m0")  # Maria is now a known contact
        email = {"id": "x1", "sender": "Maria Lopez <maria@examp1e.com>", "subject": "New bank",
                 "body": "Please update our banking details for the refund."}
        with patch.object(pipeline, "_complete_with_llm", return_value=llm_reply(
                {"needs_reply": True, "urgency": "INFORMATIONAL", "summary": "s", "reason": "r", "tasks": []})):
            result = pipeline.classify_email(self.conn, email)
        self.assertEqual(result["urgency"], "URGENT")
        self.assertTrue(result["reason"].startswith("Possible fraud"))

    def test_reply_to_and_attachments_are_stored(self):
        pipeline.log_email(self.conn, {"id": "r1", "sender": "a@x.com", "subject": "s", "body": "b",
                                       "reply_to": "b@y.com", "attachments": ["a.zip"]}, "DRAFT", {})
        item = pipeline.get_email(self.conn, "r1")
        self.assertEqual((item["reply_to"], item["attachments"]), ("b@y.com", ["a.zip"]))


class PrivacyFirewallTests(Base):
    def capture(self, model, content):
        seen = {}
        def fake(**kwargs):
            seen.update(kwargs)
            return llm_reply("ok")
        with patch("litellm.completion", fake):
            pipeline._complete_with_llm(model=model, messages=[{"role": "user", "content": content}])
        return seen["messages"][0]["content"]

    def test_cloud_calls_are_masked_and_logged_local_calls_untouched(self):
        sent = self.capture("anthropic/claude-haiku-5-5", "SSN 123-45-6789, account 000123456789")
        self.assertNotIn("6789", sent.replace("[SSN REMOVED]", ""))
        self.assertIn("[SSN REMOVED]", sent)
        self.assertIn("Masked 2 sensitive numbers before sending text to Anthropic",
                      pipeline.list_audit(self.conn)[0]["detail"])
        self.assertIn("123-45-6789", self.capture("ollama/qwen3.5:9b", "SSN 123-45-6789"))
        pipeline.set_setting(self.conn, "cloud_redaction", "0")
        self.assertIn("123-45-6789", self.capture("gpt-4o-mini", "SSN 123-45-6789"))


class RetentionAndAuditTests(Base):
    def test_retention_purges_old_handled_text_only(self):
        old = (pipeline._now() - timedelta(days=200)).isoformat()
        for gid, received in (("old", old), ("new", pipeline._iso()), ("old-open", old)):
            pipeline.log_email(self.conn, {"id": gid, "sender": "a@x.com", "subject": gid, "body": "SSN 123-45-6789",
                                           "received_at": received}, "DRAFT", {"reply": "hi"})
        pipeline.mark_sent(self.conn, "old", "hi")
        pipeline.mark_sent(self.conn, "new", "hi")
        pipeline.set_setting(self.conn, "retention_days", "90")
        pipeline.set_setting(self.conn, "mask_handled", "1")
        result = pipeline.apply_retention(self.conn)
        self.assertEqual(result, {"purged": 1, "masked": 1})
        self.assertIsNone(pipeline.get_email(self.conn, "old")["body"])
        self.assertEqual(pipeline.get_email(self.conn, "new")["body"], "SSN •••••6789")
        self.assertEqual(pipeline.get_email(self.conn, "old-open")["body"], "SSN 123-45-6789")  # not handled yet
        actions = [a["action"] for a in pipeline.list_audit(self.conn)]
        self.assertIn("Retention", actions)
        self.assertIn("Masking", actions)

    def test_security_actions_are_audited_and_exportable(self):
        self.client.post("/api/demo")
        self.client.post("/api/email/demo-quote/send", json={"text": "Hi Maria"})
        self.client.get("/api/export")
        self.client.post("/api/security/settings", json={"retention_days": 365})
        self.assertEqual(self.client.post("/api/security/settings", json={"retention_days": 7}).status_code, 400)
        actions = [a["action"] for a in pipeline.list_audit(self.conn)]
        for expected in ("Sample inbox loaded", "Reply sent (sample)", "Data exported", "Security settings changed"):
            self.assertIn(expected, actions)
        csv_text = self.client.get("/api/audit.csv").text
        self.assertTrue(csv_text.startswith("time,action,detail"))

    def test_security_score_rises_as_protections_are_turned_on(self):
        before = self.client.get("/api/security").json()
        self.assertEqual(len(before["checks"]), 10)
        self.client.post("/api/security/settings", json={"retention_days": 365, "mask_handled": True,
                                                         "bitlocker_confirmed": True})
        after = self.client.get("/api/security").json()
        self.assertGreater(after["score"], before["score"])


class AppLockTests(Base):
    def tearDown(self):
        app.LOCK.update({"locked": None, "failures": 0, "retry_at": 0.0})
        super().tearDown()

    def test_lock_blocks_data_until_the_pin_is_entered(self):
        app.LOCK.update({"locked": None, "failures": 0, "retry_at": 0.0})
        self.assertEqual(self.client.post("/api/lock/pin", json={"pin": "12"}).status_code, 400)
        r = self.client.post("/api/lock/pin", json={"pin": "2468"}).json()
        self.assertRegex(r["recovery_code"], r"^[0-9A-F]{4}(-[0-9A-F]{4}){3}$")
        self.assertNotIn("2468", pipeline.get_setting(self.conn, "lock_pin"))
        self.client.post("/api/lock")
        self.assertEqual(self.client.get("/api/inbox").status_code, 423)
        self.assertEqual(self.client.get("/api/export").status_code, 423)
        self.assertTrue(self.client.get("/api/lock/status").json()["locked"])
        self.assertEqual(self.client.post("/api/lock/unlock", json={"pin": "0000"}).status_code, 401)
        self.assertEqual(self.client.post("/api/lock/unlock", json={"pin": "2468"}).status_code, 200)
        self.assertEqual(self.client.get("/api/inbox").status_code, 200)
        self.assertNotIn("lock_pin", json.dumps(self.client.get("/api/export").json()))

    def test_lock_after_restart_rate_limit_and_recovery_code(self):
        app.LOCK.update({"locked": None, "failures": 0, "retry_at": 0.0})
        code = self.client.post("/api/lock/pin", json={"pin": "2468"}).json()["recovery_code"]
        app.LOCK["locked"] = None  # simulate a restart: a PIN means C.A.P. starts locked
        self.assertEqual(self.client.get("/api/tasks").status_code, 423)
        for _ in range(5):
            self.client.post("/api/lock/unlock", json={"pin": "9999"})
        self.assertEqual(self.client.post("/api/lock/unlock", json={"pin": "2468"}).status_code, 429)
        app.LOCK["retry_at"] = 0.0
        r = self.client.post("/api/lock/unlock", json={"pin": code.lower()}).json()
        self.assertTrue(r["recovered"])
        self.assertFalse(self.client.get("/api/lock/status").json()["enabled"])
        self.client.post("/api/lock/pin", json={"pin": "1357"})
        self.assertEqual(self.client.request("DELETE", "/api/lock/pin", json={"current_pin": "0000"}).status_code, 401)
        self.assertEqual(self.client.request("DELETE", "/api/lock/pin", json={"current_pin": "1357"}).status_code, 200)


class TrackerTests(Base):
    def test_due_dates_and_document_mentions_are_read_from_email(self):
        from datetime import date
        t = pipeline.tracker
        sat = date(2026, 10, 17)
        self.assertEqual(t.parse_due("need it by Friday at the latest", sat), "2026-10-23")
        self.assertEqual(t.parse_due("closing on the 28th", sat), "2026-10-28")
        self.assertEqual(t.parse_due("renews Nov 3", sat), "2026-11-03")
        self.assertEqual(t.parse_due("tomorrow please", sat), "2026-10-18")
        self.assertIsNone(t.parse_due("thanks!", sat))
        self.assertEqual(t.detect_documents("I uploaded my W-2, two 1099-INTs and my mortgage statement."),
                         ["W-2", "1099-INT", "1098 mortgage interest"])
        self.assertEqual(t.detect_documents("Do you still need my W-2?"), [])
        self.assertEqual(t.detect_documents("Attached is the 1098-T.", ["1098 mortgage interest", "1098-T tuition"]),
                         ["1098-T tuition"])

    def test_client_tracker_docs_status_and_reminder(self):
        pipeline.set_industry(self.conn, "tax")
        pipeline.save_business_profile(self.conn, {"business_name": "Lake Tax",
                                                   "links": "Portal: https://portal.laketax.example  Book: https://x.example"})
        c = self.client.post("/api/tracker", json={"kind": "client", "name": "Ana Ruiz", "email": "Ana@Example.com",
                                                    "data": {"tax_year": "2026", "docs": [{"name": "W-2"}, {"name": "1099-INT"}]}}).json()
        self.assertEqual((c["status"], c["email"], c["docs_outstanding"]), ("waiting_docs", "ana@example.com", ["W-2", "1099-INT"]))
        r = self.client.get(f"/api/tracker/{c['id']}/reminder").json()
        self.assertIn("- W-2\n- 1099-INT", r["text"])
        self.assertIn("https://portal.laketax.example", r["text"])
        self.assertTrue(r["mailto"].startswith("mailto:ana@example.com?subject="))
        done = self.client.post(f"/api/tracker/{c['id']}/docs", json={"names": ["W-2", "1099-INT"]}).json()
        self.assertEqual((done["docs_outstanding"], done["status"]), ([], "in_prep"))
        self.assertEqual(self.client.post("/api/tracker", json={"kind": "boat", "name": "x"}).status_code, 400)
        self.assertEqual(self.client.put(f"/api/tracker/{c['id']}", json={"status": "nope"}).status_code, 400)
        self.assertEqual(self.client.get("/api/tracker").json()["items"][0]["name"], "Ana Ruiz")

    def test_tax_sample_reader_offers_to_tick_off_documents(self):
        self.client.post("/api/demo", json={"industry": "tax"})
        kevin = self.client.get("/api/email/demo-tax-status").json()["tracker"]
        self.assertEqual(kevin["docs"]["names"], ["W-2", "1099-INT", "1098 mortgage interest"])
        self.client.post(f"/api/tracker/{kevin['docs']['client_id']}/docs", json={"names": kevin["docs"]["names"]})
        after = pipeline.tracker.get_item(self.conn, kevin["docs"]["client_id"])
        self.assertEqual((after["docs_outstanding"], after["status"]), ([], "in_prep"))
        self.assertEqual(self.client.get("/api/email/demo-tax-quote").json()["tracker"]["tracked"][0]["name"], "Jason Park")
        michelle = self.client.get("/api/email/demo-tax-irs").json()["tracker"]
        self.assertEqual(michelle["offers"][0]["kind"], "client")
        dates = pipeline.tracker.key_dates(self.conn, "tax", today=__import__("datetime").date(2026, 10, 17))
        self.assertEqual(dates["deadlines"][0], {"date": "2027-01-15", "label": "Q4 2026 estimated tax payment due",
                                                 "days_left": 90, "source": "federal"})
        self.assertEqual(dates["counts"]["Waiting on documents"], 1)  # Jason; Kevin moved on once his docs were in
        self.assertEqual(dates["counts"]["In preparation"], 2)
        self.client.delete("/api/demo")
        self.assertEqual(pipeline.tracker.list_items(self.conn), [])

    def test_federal_deadlines_roll_forward_every_year(self):
        t = pipeline.tracker
        y2027 = dict(t.federal_deadlines(2027))
        self.assertIn("2027-02-01", y2027)  # Jan 31, 2027 is a Sunday
        self.assertEqual(y2027["2027-01-15"], "Q4 2026 estimated tax payment due")
        self.assertEqual(y2027["2027-10-15"], "Extended 2026 individual returns due")
        self.assertIn("2024-01-16", dict(t.federal_deadlines(2024)))  # Jan 15, 2024 was Martin Luther King Jr. Day
        self.assertIn("2023-04-18", dict(t.federal_deadlines(2023)))  # weekend, then Emancipation Day
        self.assertIn("2022-04-18", dict(t.federal_deadlines(2022)))  # Emancipation Day observed Friday Apr 15
        self.assertIn("2029-04-17", dict(t.federal_deadlines(2029)))  # Sunday, then Emancipation Day Monday
        late = t.key_dates(self.conn, "tax", today=__import__("datetime").date(2031, 12, 1))
        self.assertEqual(late["deadlines"][0]["label"], "Q4 2031 estimated tax payment due")

    def test_custom_key_dates_and_client_timeline(self):
        D = __import__("datetime").date
        pipeline.set_industry(self.conn, "tax")
        c = self.client.post("/api/tracker", json={"kind": "client", "name": "Ana Ruiz"}).json()
        state = self.client.post("/api/key-dates", json={"date": "2026-04-15", "label": "State returns due",
                                                          "yearly": True}).json()
        meet = self.client.post("/api/key-dates", json={"date": "2026-11-02", "label": "Review meeting",
                                                         "client_id": c["id"]}).json()
        self.assertEqual(self.client.post("/api/key-dates", json={"date": "soon", "label": "x"}).status_code, 400)
        self.assertEqual(self.client.post("/api/key-dates", json={"date": "2026-11-02", "label": " "}).status_code, 400)
        self.assertEqual(self.client.post("/api/key-dates", json={"date": "2026-11-02", "label": "x",
                                                                  "client_id": 9999}).status_code, 400)
        dates = pipeline.tracker.key_dates(self.conn, "tax", today=D(2027, 1, 1))
        state_row = next(d for d in dates["deadlines"] if d.get("id") == state["id"])
        self.assertEqual((state_row["date"], state_row["source"]), ("2027-04-15", "custom"))  # comes round again
        self.assertFalse([d for d in dates["deadlines"] if d.get("id") == meet["id"]])  # one-off, already past
        soon = pipeline.tracker.key_dates(self.conn, "tax", today=D(2026, 10, 30))
        self.assertEqual(next(d for d in soon["deadlines"] if d.get("id") == meet["id"])["client_name"], "Ana Ruiz")
        listed = self.client.get("/api/tracker").json()["custom_dates"]
        self.assertEqual({d["label"] for d in listed}, {"State returns due", "Review meeting"})
        edited = self.client.put(f"/api/key-dates/{meet['id']}", json={"date": "2026-11-09", "label": "Review meeting",
                                                                        "client_id": c["id"]}).json()
        self.assertEqual(edited["set_date"], "2026-11-09")
        self.client.delete(f"/api/tracker/{c['id']}")
        self.assertEqual([d["label"] for d in pipeline.tracker.list_dates(self.conn)], ["State returns due"])
        self.client.delete(f"/api/key-dates/{state['id']}")
        self.assertEqual(pipeline.tracker.list_dates(self.conn), [])

    def test_insurance_tracker_offers_and_coming_up(self):
        self.client.post("/api/demo", json={"industry": "insurance"})
        today = self.client.get("/api/today").json()["key_dates"]
        kinds = {i["kind"] for i in today["items"]}
        self.assertEqual(kinds, {"coi", "renewal"})
        self.assertTrue(all(i["days_left"] <= 60 for i in today["items"]))
        coi = self.client.get("/api/email/demo-ins-coi").json()["tracker"]
        self.assertEqual(coi["tracked"][0]["kind"], "coi")
        self.assertFalse([o for o in coi["offers"] if o["kind"] == "coi"])  # already tracked
        pipeline.log_email(self.conn, {"id": "n1", "sender": "Jo Diaz <jo@diazroof.example>", "subject": "COI needed",
                                       "body": "Please send a certificate of insurance for Lakeview Homes by tomorrow."},
                           "DRAFT", {"reply": "ok"})
        offer = self.client.get("/api/email/n1").json()["tracker"]["offers"][0]
        self.assertEqual((offer["kind"], offer["prefill"]["data"]["holder"]), ("coi", "Lakeview Homes"))
        self.assertTrue(offer["prefill"]["due_at"])
        renewal = next(i for i in self.client.get("/api/tracker").json()["items"] if i["name"] == "Dave Henderson")
        text = self.client.get(f"/api/tracker/{renewal['id']}/reminder").json()["text"]
        self.assertIn("Homeowners policy", text)
        self.assertIn("Marcus Bell", text)

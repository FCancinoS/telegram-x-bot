import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

import app
import media_bundles


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.db = app.connect(":memory:")
        self.now = app.parse_time("2026-10-04T12:00:00-06:00")
        self.cfg = app.Config(chat="-1001", senders=("7", "8"),
                              cta="https://t.me/luckybetpicksfree", fallback=True)
        self.link = "https://www.playdoit.mx?shareCode=231C7QTYLG4"

    def tearDown(self):
        self.db.close()

    def put(self, number, text="", photo=False, sender=7, seconds=0, **extra):
        msg = {"chat": {"id": -1001, "type": "supergroup"}, "from": {"id": sender},
               "message_id": number, "date": int((self.now + timedelta(seconds=seconds)).timestamp()),
               "text": text}
        if photo:
            msg["photo"] = [{"file_id": f"small{number}", "file_unique_id": f"s{number}", "width": 90, "height": 90},
                            {"file_id": f"large{number}", "file_unique_id": f"u{number}", "width": 800, "height": 1200}]
        msg.update(extra)
        with self.db:
            app.ingest(self.db, self.cfg, {"message": msg})

    def sequence(self, free="Free Parlay☘️☘️☘️"):
        self.put(1, photo=True)
        self.put(2, "Acabo de hacer una apuesta en Playdoit!\n" + self.link, seconds=20)
        if free:
            self.put(3, free, seconds=30)

    def select(self, seconds=121):
        now = self.now + timedelta(seconds=seconds)
        with self.db:
            media_bundles.assemble(self.db, self.cfg, now)
        return app.select_pick(self.db, self.cfg, now)

    def test_screenshot_pattern_preserves_link_photo_and_free_label(self):
        self.sequence()
        selected = self.select()
        self.assertEqual(selected[0], "bundle:-1001:1")
        self.assertIn("Free Parlay", selected[3])
        self.assertIn(self.link, selected[3])
        self.assertIn(self.cfg.cta, selected[3])
        payload = json.loads(self.db.execute("SELECT payload FROM messages WHERE id=?", (selected[0],)).fetchone()[0])
        self.assertEqual(payload["media"]["file_id"], "large1")

    def test_waits_for_separate_messages(self):
        self.sequence()
        self.assertIsNone(self.select(90))
        self.assertIsNotNone(self.select(121))

    def test_image_and_caption_in_one_message(self):
        self.put(1, photo=True, caption="Free Pick MLB ⚾️☘️\n" + self.link)
        self.assertIsNotNone(self.select())

    def test_hidden_link_entity(self):
        self.put(1, photo=True)
        self.put(2, "Enlace", entities=[{"type": "text_link", "url": self.link}])
        self.put(3, "Free Pick")
        self.assertIn(self.link, self.select()[3])

    def test_different_tipsters_cannot_mix(self):
        self.put(1, photo=True)
        self.put(2, self.link, sender=8)
        self.put(3, "Free Pick", sender=8)
        self.assertIsNone(self.select())

    def test_different_topics_cannot_mix(self):
        self.put(1, photo=True, message_thread_id=10)
        self.put(2, self.link, message_thread_id=20)
        self.put(3, "Free Pick", message_thread_id=20)
        self.assertIsNone(self.select())

    def test_late_link_not_associated(self):
        self.put(1, photo=True)
        self.put(2, self.link, seconds=301)
        self.put(3, "Free Pick", seconds=302)
        self.assertIsNone(self.select(400))

    def test_two_images_without_completed_ticket_are_ambiguous(self):
        self.put(1, photo=True)
        self.put(2, photo=True)
        self.put(3, self.link)
        self.put(4, "Free Pick")
        self.assertIsNone(self.select())
        self.assertTrue(all("ambiguous" in r[0] for r in self.db.execute("SELECT state FROM bundle_state")))

    def test_two_complete_tickets_do_not_mix(self):
        self.sequence()
        self.put(4, photo=True, seconds=35)
        self.put(5, "https://www.playdoit.mx?shareCode=AbcSecond", seconds=40)
        self.put(6, "Free Pick", seconds=45)
        self.select(150)
        picks = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM messages WHERE id LIKE 'bundle:%' ORDER BY id")]
        self.assertEqual(len(picks), 2)
        self.assertEqual(picks[0]["playdoit_url"], self.link)
        self.assertEqual(picks[1]["media"]["file_id"], "large4")

    def test_multiple_links_are_ambiguous(self):
        self.sequence()
        self.put(4, "https://www.playdoit.mx?shareCode=other", seconds=40)
        self.assertIsNone(self.select(150))

    def test_edited_link_replaces_previous_link(self):
        self.sequence()
        newer = "https://www.playdoit.mx?shareCode=NewCode"
        self.put(2, newer, seconds=20, edit_date=int((self.now + timedelta(seconds=50)).timestamp()))
        self.assertIsNone(self.select(130))
        self.assertIn(newer, self.select(150)[3])
        self.assertNotIn(self.link, self.select(150)[3])

    def test_missing_link_and_wrong_domain_not_sent(self):
        self.put(1, photo=True)
        self.put(2, "https://www.playdoit.mx.evil.test?shareCode=a")
        self.put(3, "Free Pick")
        self.assertIsNone(self.select())

    def test_album_left_for_review(self):
        self.put(1, photo=True, caption="Free Pick " + self.link, media_group_id="album1")
        self.assertIsNone(self.select())

    def test_no_automatic_x_post_without_verified_event_time(self):
        self.sequence()
        self.cfg.delivery = "x_api"
        self.assertIsNone(self.select())

    def test_fallback_delivers_image_for_manual_review(self):
        self.sequence(free=None)
        later = self.now + timedelta(hours=3)
        with self.db:
            app.setmeta(self.db, "coverage_since", app.stamp(self.now - timedelta(hours=12)))
            app.setmeta(self.db, "last_poll", app.stamp(later))
        self.assertEqual(self.select(3 * 3600)[2], "fallback")

    def test_tick_passes_media_and_records_once(self):
        self.sequence()
        self.cfg.dry, self.cfg.authorized = False, True
        publisher = Mock(return_value="telegram:100")
        app.tick(self.db, self.cfg, self.now + timedelta(seconds=121), publisher)
        app.tick(self.db, self.cfg, self.now + timedelta(hours=1), publisher)
        self.assertEqual(publisher.call_count, 1)
        self.assertEqual(publisher.call_args.kwargs["media"]["file_id"], "large1")
        row = self.db.execute("SELECT status,media_json FROM posts").fetchone()
        self.assertEqual(row[0], "draft_sent")
        self.assertEqual(json.loads(row[1])["file_id"], "large1")

    def test_http_photo_caption_and_button_in_one_request(self):
        response = SimpleNamespace(status_code=200, json=lambda: {"ok": True, "result": {"message_id": 4}})
        post = Mock(return_value=response)
        with patch.dict(sys.modules, {"requests": SimpleNamespace(post=post)}), patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_REVIEW_CHAT_ID": "123"}):
            result = app.send_draft("Free Pick\n" + self.link, {"type": "photo", "file_id": "image1"})
        self.assertEqual(result, "telegram:4")
        self.assertTrue(post.call_args.args[0].endswith("/sendPhoto"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["photo"], "image1")
        self.assertIn(self.link, payload["caption"])
        self.assertIn("adjúntala", payload["caption"])
        self.assertIn("reply_markup", payload)
        self.assertNotIn("text", payload)

    def test_image_document_uses_send_document(self):
        self.put(1, caption="Free Pick " + self.link,
                 document={"mime_type": "image/png", "file_id": "doc1", "file_unique_id": "du1"})
        self.assertIsNotNone(self.select())
        with patch.dict(os.environ, {"TELEGRAM_REVIEW_CHAT_ID": "123"}):
            payload = app.draft_payload("test", {"type": "document", "file_id": "doc1"})
        self.assertEqual(payload["document"], "doc1")
        self.assertNotIn("photo", payload)

    def test_legacy_database_migration_keeps_posts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "old.sqlite3")
            old = sqlite3.connect(path)
            old.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, source TEXT UNIQUE, fingerprint TEXT UNIQUE, day TEXT, kind TEXT, text TEXT, status TEXT, at TEXT, x_id TEXT, error TEXT)")
            old.execute("INSERT INTO posts(source,text) VALUES ('old','conservar')")
            old.commit()
            old.close()
            migrated = app.connect(path)
            self.assertEqual(migrated.execute("SELECT text,media_json FROM posts").fetchone()[0], "conservar")
            migrated.close()


if __name__ == "__main__":
    unittest.main()

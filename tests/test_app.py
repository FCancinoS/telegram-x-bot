import json
import os
import unittest
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from pathlib import Path
import app


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.db = app.connect(":memory:")
        self.cfg = app.Config(chat="-1001", senders=("7",), fallback=True,
                              cta="https://t.me/luckybetpicksfree")
        self.now = app.parse_time("2026-10-04T12:00:00-06:00")

    def tearDown(self):
        self.db.close()

    def put(self, free=True, number=1, start="2026-10-04T19:00:00-06:00", sender=7, raw=None):
        text = raw if raw is not None else (("Free pick\n" if free else "") + f"Evento: Equipo A vs B\nPick: Más de 2.5 goles\nCuota: 1.90\nInicio: {start}")
        update = {"message": {"chat": {"id": -1001, "type": "supergroup"}, "from": {"id": sender},
                  "message_id": number, "date": int(self.now.timestamp()), "text": text}}
        with self.db:
            app.ingest(self.db, self.cfg, update)
        return update

    def coverage(self, now):
        with self.db:
            app.setmeta(self.db, "coverage_since", app.stamp(app.parse_time("2026-10-04T00:00:00-06:00")))
            app.setmeta(self.db, "last_poll", app.stamp(now))

    def test_free_immediate_and_duplicate_after_restart(self):
        self.put()
        app.tick(self.db, self.cfg, self.now)
        self.put(number=2)
        app.tick(self.db, self.cfg, self.now + timedelta(hours=1))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)

    def test_other_group_participant_ignored(self):
        self.put(sender=99)
        self.assertIsNone(app.select_pick(self.db, self.cfg, self.now))

    def test_no_fallback_before_cutoff(self):
        self.put(False)
        self.coverage(self.now)
        self.assertIsNone(app.select_pick(self.db, self.cfg, self.now))

    def test_fallback_at_15_and_only_once(self):
        self.put(False)
        later = self.now + timedelta(hours=3)
        self.coverage(later)
        self.assertEqual(app.select_pick(self.db, self.cfg, later)[2], "fallback")
        app.tick(self.db, self.cfg, later)
        self.put(False, 2)
        app.tick(self.db, self.cfg, later + timedelta(minutes=5))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)

    def test_startup_midday_suppresses_fallback(self):
        self.put(False)
        later = self.now + timedelta(hours=3)
        with self.db:
            app.record_poll(self.db, later)
        self.assertIsNone(app.select_pick(self.db, self.cfg, later))

    def test_malformed_free_still_suppresses_fallback(self):
        self.put(False)
        self.put(number=2, raw="Free pick\nSolo imagen")
        later = self.now + timedelta(hours=3)
        self.coverage(later)
        self.assertIsNone(app.select_pick(self.db, self.cfg, later))

    def test_expired_free_suppresses_fallback(self):
        self.put(start="2026-10-04T13:00:00-06:00")
        self.put(False, 2)
        later = self.now + timedelta(hours=3)
        self.coverage(later)
        self.assertIsNone(app.select_pick(self.db, self.cfg, later))

    def test_old_day_not_published(self):
        self.put()
        self.assertIsNone(app.select_pick(self.db, self.cfg, self.now + timedelta(days=1)))

    def test_edit_cannot_erase_free_observation(self):
        self.put()
        self.put(False)
        later = self.now + timedelta(hours=3)
        self.coverage(later)
        self.assertIsNone(app.select_pick(self.db, self.cfg, later))

    def test_timeout_does_not_retry_publication(self):
        self.put()
        self.cfg.dry, self.cfg.authorized = False, True
        calls = []
        def timeout(text):
            calls.append(text)
            raise TimeoutError()
        app.tick(self.db, self.cfg, self.now, timeout)
        app.tick(self.db, self.cfg, self.now + timedelta(hours=1), timeout)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.db.execute("SELECT status FROM posts").fetchone()[0], "unknown")

    def test_draft_not_recorded_as_x_publication(self):
        self.put()
        self.cfg.dry, self.cfg.authorized = False, True
        app.tick(self.db, self.cfg, self.now, lambda _: "telegram:8")
        self.assertEqual(self.db.execute("SELECT status FROM posts").fetchone()[0], "draft_sent")

    def test_slots_respect_event_deadline(self):
        self.cfg.free_mode = "slots"
        self.put(start="2026-10-04T13:00:00-06:00")
        self.assertIsNone(app.select_pick(self.db, self.cfg, self.now))
        self.assertIsNotNone(app.select_pick(self.db, self.cfg, self.now + timedelta(minutes=30)))

    def test_missing_timezone_rejected(self):
        self.put(start="2026-10-04T19:00:00")
        self.assertIsNone(app.select_pick(self.db, self.cfg, self.now))

    def test_long_pick_is_not_truncated(self):
        self.put(raw="Free pick\nEvento: " + "A" * 300 + "\nPick: Más de 2.5\nCuota: 1.90\nInicio: 2026-10-04T19:00:00-06:00")
        self.assertIsNone(app.select_pick(self.db, self.cfg, self.now))

    def test_poll_gap_invalidates_coverage(self):
        self.coverage(self.now)
        later = self.now + timedelta(minutes=4)
        with self.db:
            app.record_poll(self.db, later)
        self.assertEqual(app.getmeta(self.db, "coverage_since"), app.stamp(later))

    def test_draft_button_preserves_text(self):
        text = "Equipo A & B\nCuota +120 #pick"
        with patch.dict(os.environ, {"TELEGRAM_REVIEW_CHAT_ID": "123"}):
            payload = app.draft_payload(text)
        url = payload["reply_markup"]["inline_keyboard"][0][0]["url"]
        self.assertEqual(parse_qs(urlparse(url).query)["text"], [text])

    def test_500_unique_phrases(self):
        phrases = json.loads(Path(app.__file__).with_name("phrases.json").read_text(encoding="utf-8"))
        self.assertEqual(len(set(phrases)), 500)

    def test_daily_cap(self):
        self.cfg.daily = 1
        self.put()
        app.tick(self.db, self.cfg, self.now)
        self.put(number=2, start="2026-10-04T20:00:00-06:00")
        app.tick(self.db, self.cfg, self.now + timedelta(hours=1))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0], 1)

    def test_real_free_headings(self):
        for text in ("Free Pick MLB ⚾️☘️☘️☘️", "Free Parlay", "Free Pick☘️☘️☘️"):
            self.assertIsNotNone(app.FREE.search(text))

    def test_slots_leave_polling_margin(self):
        self.cfg.free_mode = "slots"
        self.put(start="2026-10-04T13:00:00-06:00")
        self.assertIsNotNone(app.select_pick(self.db, self.cfg, self.now + timedelta(minutes=29)))


if __name__ == "__main__":
    unittest.main()

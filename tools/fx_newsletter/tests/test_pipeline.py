"""네트워크 없이 파이프라인 전 구간을 검증한다."""

from __future__ import annotations

import base64
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fx_newsletter import cards, commentary, fetch, indicators, mailer
from fx_newsletter.config import MailConfig
from fx_newsletter.tests.fixtures import synthetic_payload

END = dt.date(2026, 9, 4)  # 금요일


class FetchTest(unittest.TestCase):
    def test_converts_eur_base_to_krw_pairs(self):
        payload = {
            "base": "EUR",
            "rates": {"2026-09-04": {"KRW": 1450.0, "USD": 1.10, "JPY": 160.0, "CNY": 7.25}},
        }
        # 관측치가 1건이면 시계열이 너무 짧다고 거부해야 한다.
        with self.assertRaises(fetch.FetchError):
            fetch.to_series(payload)

        payload["rates"]["2026-09-03"] = {"KRW": 1440.0, "USD": 1.10, "JPY": 160.0, "CNY": 7.25}
        series = fetch.to_series(payload)

        self.assertAlmostEqual(series["USD"].latest, 1450.0 / 1.10, places=6)
        self.assertAlmostEqual(series["EUR"].latest, 1450.0, places=6)
        # 엔화는 100엔 기준으로 표시한다.
        self.assertAlmostEqual(series["JPY"].latest, 100 * 1450.0 / 160.0, places=6)
        self.assertAlmostEqual(series["CNY"].latest, 1450.0 / 7.25, places=6)
        self.assertEqual(series["JPY"].pair.display_name, "100JPY/KRW")

    def test_skips_days_with_missing_currency(self):
        payload = {
            "base": "EUR",
            "rates": {
                "2026-09-02": {"KRW": 1430.0, "USD": 1.10, "JPY": 160.0, "CNY": 7.25},
                "2026-09-03": {"USD": 1.10, "JPY": 160.0, "CNY": 7.25},  # KRW 결측
                "2026-09-04": {"KRW": 1450.0, "USD": 1.10, "JPY": 160.0, "CNY": 7.25},
            },
        }
        series = fetch.to_series(payload)
        self.assertEqual(series["USD"].dates, (dt.date(2026, 9, 2), dt.date(2026, 9, 4)))

    def test_falls_back_to_second_host(self):
        seen: list[str] = []

        def flaky_get(url: str) -> dict:
            seen.append(url)
            if "frankfurter.dev" in url:
                raise RuntimeError("gateway 403")
            return synthetic_payload(END, days=40)

        payload = fetch.fetch_raw(END - dt.timedelta(days=40), END, session_get=flaky_get)
        self.assertTrue(payload["rates"])
        self.assertEqual(len(seen), 2)
        # 기준통화 EUR은 symbols에서 빠져야 한다.
        self.assertIn("symbols=CNY,JPY,KRW,USD", seen[0])

    def test_raises_when_all_hosts_fail(self):
        def always_fail(url: str) -> dict:
            raise RuntimeError("down")

        with self.assertRaises(fetch.FetchError):
            fetch.fetch_raw(END - dt.timedelta(days=40), END, session_get=always_fail)


class IndicatorTest(unittest.TestCase):
    def test_sma_and_change_lookup_span_weekends(self):
        series = fetch.to_series(synthetic_payload(END))["USD"]
        snapshot = indicators.build_snapshot(series)

        self.assertEqual(snapshot.latest_date, END)
        self.assertIsNotNone(snapshot.week)
        # 금요일 기준 7일 전은 지난 금요일(고시 있는 날)이어야 한다.
        self.assertEqual(snapshot.week.ref_date, dt.date(2026, 8, 28))
        self.assertAlmostEqual(snapshot.sma20, sum(series.values[-20:]) / 20, places=6)
        self.assertAlmostEqual(snapshot.sma60, sum(series.values[-60:]) / 60, places=6)
        self.assertLessEqual(snapshot.latest, snapshot.high52)
        self.assertGreaterEqual(snapshot.latest, snapshot.low52)
        self.assertEqual(len(snapshot.spark), 60)

    def test_rsi_bounds(self):
        rising = tuple(float(100 + i) for i in range(40))
        falling = tuple(float(140 - i) for i in range(40))
        self.assertAlmostEqual(indicators.rsi(rising), 100.0, places=6)
        self.assertAlmostEqual(indicators.rsi(falling), 0.0, places=6)
        self.assertIsNone(indicators.rsi((1.0, 2.0)))

    def test_flat_series_has_no_volatility_and_neutral_position(self):
        flat = tuple(1000.0 for _ in range(40))
        self.assertAlmostEqual(indicators.annualized_volatility(flat), 0.0, places=9)
        # 표준편차가 0이면 볼린저 폭이 0이라 위치를 정의할 수 없다.
        self.assertIsNone(indicators.bollinger_pct(flat))

    def test_bollinger_within_range_for_normal_series(self):
        series = fetch.to_series(synthetic_payload(END))["EUR"]
        value = indicators.bollinger_pct(series.values)
        self.assertIsNotNone(value)
        self.assertGreater(value, -50)
        self.assertLess(value, 150)

    def test_trend_labels(self):
        self.assertEqual(indicators._trend_label(110, 105, 100), "상승 추세")
        self.assertEqual(indicators._trend_label(90, 95, 100), "하락 추세")
        self.assertEqual(indicators._trend_label(100, 105, 100), "상승 속 조정")
        self.assertEqual(indicators._trend_label(100, 95, 100), "하락 속 반등")
        self.assertEqual(indicators._trend_label(100, None, None), "판단 보류")


class CommentaryTest(unittest.TestCase):
    def setUp(self):
        series_map = fetch.to_series(synthetic_payload(END))
        self.snapshots = indicators.build_snapshots(series_map)

    def test_rule_based_covers_every_pair(self):
        note = commentary.rule_based(self.snapshots)
        self.assertEqual(note.source, "rules")
        self.assertEqual(set(note.pair_comments), {"USD", "JPY", "EUR", "CNY"})
        self.assertTrue(note.headline)
        self.assertTrue(note.summary)
        self.assertEqual(len(note.watchpoints), 3)

    def test_build_uses_rules_when_ai_disabled(self):
        self.assertEqual(commentary.build(self.snapshots, use_ai=False).source, "rules")

    def test_build_falls_back_when_claude_raises(self):
        original = commentary._call_claude
        commentary._call_claude = lambda snapshots: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            import os

            os.environ["ANTHROPIC_API_KEY"] = "test-key"
            note = commentary.build(self.snapshots, use_ai=True)
        finally:
            commentary._call_claude = original
            os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertEqual(note.source, "rules")

    def test_payload_has_no_raw_series(self):
        payload = commentary.snapshots_to_payload(self.snapshots)
        self.assertEqual(len(payload), 4)
        self.assertNotIn("spark", payload[0])


class RenderAndMailTest(unittest.TestCase):
    def setUp(self):
        series_map = fetch.to_series(synthetic_payload(END))
        self.snapshots = indicators.build_snapshots(series_map)
        self.note = commentary.rule_based(self.snapshots)

    def test_renders_one_card_per_pair_plus_cover_and_outlook(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            paths = cards.render_all(self.snapshots, self.note, END, Path(tmp))
            self.assertEqual(len(paths), len(self.snapshots) + 2)
            for path in paths:
                self.assertTrue(path.exists())
                with Image.open(path) as image:
                    self.assertEqual(image.size, (1080, 1080))
            self.assertEqual(paths[0].name, "01-cover.png")
            self.assertEqual(paths[-1].name, "06-outlook.png")

    def _smtp_config(self):
        return MailConfig(
            provider="smtp",
            sender_name="주간 환율 브리핑",
            sender_address="me@example.com",
            recipients=("me@example.com",),
            host="smtp.gmail.com",
            port=465,
            user="me@example.com",
            password="apppassword",
        )

    def _resend_config(self):
        return MailConfig(
            provider="resend",
            sender_name="주간 환율 브리핑",
            sender_address="onboarding@resend.dev",
            recipients=("me@example.com",),
            resend_api_key="re_test",
        )

    def test_smtp_message_embeds_every_card_inline(self):
        config = self._smtp_config()
        with tempfile.TemporaryDirectory() as tmp:
            paths = cards.render_all(self.snapshots, self.note, END, Path(tmp))
            message = mailer.build_message(config, self.snapshots, self.note, END, paths)

        self.assertIn(self.note.headline, message["Subject"])
        self.assertEqual(message["From"], "주간 환율 브리핑 <me@example.com>")

        html_parts = [p for p in message.walk() if p.get_content_type() == "text/html"]
        images = [p for p in message.walk() if p.get_content_type() == "image/png"]
        self.assertEqual(len(html_parts), 1)
        self.assertEqual(len(images), len(paths))

        html = html_parts[0].get_content()
        for part in images:
            self.assertIn(f"cid:{part['Content-ID'].strip('<>')}", html)

        plain = [p for p in message.walk() if p.get_content_type() == "text/plain"][0].get_content()
        for snapshot in self.snapshots:
            self.assertIn(snapshot.display_name, plain)

    def test_resend_payload_shape(self):
        config = self._resend_config()
        with tempfile.TemporaryDirectory() as tmp:
            paths = cards.render_all(self.snapshots, self.note, END, Path(tmp))
            inline = mailer.resend_payload(config, self.snapshots, self.note, END, paths, True)
            plain = mailer.resend_payload(config, self.snapshots, self.note, END, paths, False)

        self.assertEqual(inline["to"], ["me@example.com"])
        self.assertEqual(inline["from"], "주간 환율 브리핑 <onboarding@resend.dev>")
        self.assertEqual(len(inline["attachments"]), len(paths))

        # 인라인 요청은 content_id를 달고 HTML이 그것을 cid:로 참조한다.
        for attachment in inline["attachments"]:
            self.assertIn("content_id", attachment)
            self.assertIn(f"cid:{attachment['content_id']}", inline["html"])
            # content는 base64 문자열이어야 한다.
            base64.b64decode(attachment["content"], validate=True)

        # 첨부 전용 요청에는 content_id도 img 태그도 없다.
        for attachment in plain["attachments"]:
            self.assertNotIn("content_id", attachment)
        self.assertNotIn("<img", plain["html"])
        self.assertIn("첨부파일", plain["html"])

    def test_resend_sends_inline_on_success(self):
        config = self._resend_config()
        calls = []

        def fake_post(api_key, payload):
            calls.append((api_key, payload))
            return SimpleNamespace(status_code=200, text="{}")

        with tempfile.TemporaryDirectory() as tmp:
            paths = cards.render_all(self.snapshots, self.note, END, Path(tmp))
            mailer.send_via_resend(config, self.snapshots, self.note, END, paths, post=fake_post)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "re_test")
        self.assertIn("content_id", calls[0][1]["attachments"][0])

    def test_resend_falls_back_to_attachment_only_on_4xx(self):
        """content_id를 모르는 API라도 메일은 나가야 한다."""
        config = self._resend_config()
        calls = []

        def fake_post(api_key, payload):
            calls.append(payload)
            if "content_id" in payload["attachments"][0]:
                return SimpleNamespace(status_code=422, text="unknown field content_id")
            return SimpleNamespace(status_code=200, text="{}")

        with tempfile.TemporaryDirectory() as tmp:
            paths = cards.render_all(self.snapshots, self.note, END, Path(tmp))
            mailer.send_via_resend(config, self.snapshots, self.note, END, paths, post=fake_post)

        self.assertEqual(len(calls), 2)
        self.assertNotIn("content_id", calls[1]["attachments"][0])
        self.assertNotIn("<img", calls[1]["html"])

    def test_resend_raises_on_server_error(self):
        config = self._resend_config()

        def fake_post(api_key, payload):
            return SimpleNamespace(status_code=500, text="boom")

        with tempfile.TemporaryDirectory() as tmp:
            paths = cards.render_all(self.snapshots, self.note, END, Path(tmp))
            with self.assertRaises(mailer.SendError):
                mailer.send_via_resend(config, self.snapshots, self.note, END, paths, post=fake_post)

    def test_resend_does_not_retry_a_rejected_attachment_only_send(self):
        """첨부 전용까지 거부당하면 조용히 성공한 척하지 않고 실패시킨다."""
        config = self._resend_config()
        calls = []

        def fake_post(api_key, payload):
            calls.append(payload)
            return SimpleNamespace(status_code=422, text="nope")

        with tempfile.TemporaryDirectory() as tmp:
            paths = cards.render_all(self.snapshots, self.note, END, Path(tmp))
            with self.assertRaises(mailer.SendError):
                mailer.send_via_resend(config, self.snapshots, self.note, END, paths, post=fake_post)
        self.assertEqual(len(calls), 2)

    def test_env_picks_resend_when_key_present(self):
        import os

        os.environ.update({"RESEND_API_KEY": "re_x", "FX_MAIL_TO": "a@b.com"})
        try:
            config = MailConfig.from_env()
        finally:
            for key in ("RESEND_API_KEY", "FX_MAIL_TO"):
                os.environ.pop(key, None)

        self.assertEqual(config.provider, "resend")
        self.assertEqual(config.sender_address, "onboarding@resend.dev")
        self.assertEqual(config.recipients, ("a@b.com",))
        self.assertEqual(config.validate(), [])

    def test_env_falls_back_to_smtp_and_strips_app_password_spaces(self):
        import os

        os.environ.update(
            {"FX_SMTP_USER": "a@b.com", "FX_SMTP_PASSWORD": "abcd efgh ijkl mnop"}
        )
        try:
            config = MailConfig.from_env()
        finally:
            for key in ("FX_SMTP_USER", "FX_SMTP_PASSWORD"):
                os.environ.pop(key, None)

        self.assertEqual(config.provider, "smtp")
        self.assertEqual(config.password, "abcdefghijklmnop")
        # 발신·수신 주소가 모두 SMTP 사용자로 채워진다.
        self.assertEqual(config.sender_address, "a@b.com")
        self.assertEqual(config.recipients, ("a@b.com",))
        self.assertEqual(config.validate(), [])

    def test_validate_reports_missing_per_provider(self):
        resend = MailConfig(provider="resend", sender_name="n", sender_address="", recipients=())
        self.assertEqual(
            set(resend.validate()), {"FX_MAIL_TO", "FX_MAIL_FROM", "RESEND_API_KEY"}
        )
        smtp = MailConfig(provider="smtp", sender_name="n", sender_address="", recipients=())
        self.assertEqual(
            set(smtp.validate()),
            {"FX_MAIL_TO", "FX_MAIL_FROM", "FX_SMTP_USER", "FX_SMTP_PASSWORD"},
        )


if __name__ == "__main__":
    unittest.main()

from autotrade.journal import Journal, redact


def test_secrets_are_redacted():
    out = redact({"appkey": "AAA", "nested": {"app_secret": "BBB", "qty": 3},
                  "list": [{"access_token": "CCC"}]})
    assert out["appkey"] == "***REDACTED***"
    assert out["nested"]["app_secret"] == "***REDACTED***"
    assert out["list"][0]["access_token"] == "***REDACTED***"
    assert out["nested"]["qty"] == 3


def test_account_numbers_are_masked():
    assert redact("계좌 50123456 입금") == "계좌 5012**** 입금"


def test_journal_is_append_only(tmp_path):
    j = Journal(tmp_path / "j.jsonl", echo=False)
    j.write("first", a=1)
    j2 = Journal(tmp_path / "j.jsonl", echo=False)
    j2.write("second", b=2)
    events = [e["event"] for e in j.read_all()]
    assert events == ["first", "second"]


def test_journal_writes_do_not_leak_secrets(tmp_path):
    path = tmp_path / "j.jsonl"
    Journal(path, echo=False).write("auth", appsecret="do-not-log-me")
    assert "do-not-log-me" not in path.read_text(encoding="utf-8")


def test_malformed_lines_are_skipped(tmp_path):
    path = tmp_path / "j.jsonl"
    j = Journal(path, echo=False)
    j.write("ok", a=1)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n")
    assert len(j.read_all()) == 1

import email
import email.message
import re
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from gmail_auth import (
    DEFAULT_USER_AGENT,
    GLaDOSAuthenticator,
    GmailReader,
    generate_browser_fingerprint,
    perform_gmail_login,
)


def test_generate_browser_fingerprint():
    fp = generate_browser_fingerprint("TestUser@Gmail.Com")
    assert fp.startswith("fp1.")
    assert re.match(r"^fp1.[0-9a-f]{16,64}$", fp)
    # 大小写不敏感归一化
    assert fp == generate_browser_fingerprint("testuser@gmail.com")


def test_authenticator_send_verification_code_success():
    auth = GLaDOSAuthenticator("glados.cloud")
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.ok = True
    mock_resp.json.return_value = {"code": 0, "message": "access code sent"}

    with patch.object(auth.session, "post", return_value=mock_resp) as post_mock:
        assert auth.send_verification_code("user@example.com") is True
        assert post_mock.call_count == 1
        headers = post_mock.call_args.kwargs["headers"]
        assert "authorization" in headers
        assert headers["authorization"].startswith("fp1.")
        payload = post_mock.call_args.kwargs["json"]
        assert payload["address"] == "user@example.com"
        assert payload["site"] == "glados.network"


def test_authenticator_send_verification_code_requires_captcha(caplog):
    auth = GLaDOSAuthenticator("glados.cloud")
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.ok = True
    mock_resp.json.return_value = {
        "code": 0,
        "captcha_required": True,
        "sitekey": "do-not-log",
    }

    with patch.object(auth.session, "post", return_value=mock_resp):
        assert auth.send_verification_code("user@example.com") is False

    assert "人机验证" in caplog.text
    assert "发码请求成功" not in caplog.text
    assert "do-not-log" not in caplog.text


def test_authenticator_send_verification_code_failure():
    auth = GLaDOSAuthenticator("glados.cloud")
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.ok = True
    mock_resp.json.return_value = {"code": -1, "message": "rate limit"}

    with patch.object(auth.session, "post", return_value=mock_resp):
        assert auth.send_verification_code("user@example.com") is False

    mock_resp.ok = False
    mock_resp.status_code = 500
    with patch.object(auth.session, "post", return_value=mock_resp):
        assert auth.send_verification_code("user@example.com") is False


def test_authenticator_login_with_code_success():
    auth = GLaDOSAuthenticator("glados.cloud")
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.ok = True
    mock_resp.json.return_value = {"code": 0, "message": "login success"}
    auth.session.cookies.set("koa:sess", "sess_val")
    auth.session.cookies.set("koa:sess.sig", "sig_val")

    with patch.object(auth.session, "post", return_value=mock_resp) as post_mock:
        cookie_str = auth.login_with_code("user@example.com", "123456")
        assert "koa:sess=sess_val" in cookie_str
        assert "koa:sess.sig=sig_val" in cookie_str
        payload = post_mock.call_args.kwargs["json"]
        assert payload["email"] == "user@example.com"
        assert payload["mailcode"] == "123456"


def test_authenticator_login_with_code_failure():
    auth = GLaDOSAuthenticator("glados.cloud")
    mock_resp = MagicMock(spec=requests.Response)
    mock_resp.ok = True
    mock_resp.json.return_value = {"code": 1, "message": "mailcode error"}

    with patch.object(auth.session, "post", return_value=mock_resp):
        assert auth.login_with_code("user@example.com", "999999") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Your verification code is: 123456. Expires in 10 mins.", "123456"),
        ("Your access code is 654321", "654321"),
        ("GLaDOS 验证码为 789012，请在10分钟内填写", "789012"),
        ("<p>Please verify: <b>987654</b></p>", "987654"),
        ("Security token *456789*", "456789"),
        ("Hello, no numbers here", None),
        ("", None),
    ],
)
def test_gmail_reader_extract_code(text, expected):
    assert GmailReader.extract_code(text) == expected


def test_gmail_reader_extract_body_multipart():
    msg = email.message.EmailMessage()
    msg.set_content("Hello Plain Body")
    msg.add_alternative("<p>Hello HTML Body</p>", subtype="html")
    body = GmailReader.extract_body(msg)
    assert "Hello Plain Body" in body
    assert "Hello HTML Body" in body


def test_gmail_reader_fetch_latest_code():
    reader = GmailReader("test@gmail.com", "app_pass_word_123")
    mock_mail = MagicMock()
    reader.mail = mock_mail

    # select 返回 OK
    mock_mail.select.return_value = ("OK", [b"1"])
    # 模拟 search FROM "glados" 返回 [b"101 102"]
    mock_mail.search.return_value = ("OK", [b"101 102"])

    # 构造邮件 102 (最新)
    now_rfc2822 = email.utils.formatdate(time.time(), usegmt=True)
    msg_bytes = (
        f"From: notify@glados.cloud\r\nSubject: GLaDOS Verification Code\r\n"
        f"Date: {now_rfc2822}\r\n\r\nYour code is: 582910\r\n"
    ).encode("utf-8")

    mock_mail.fetch.return_value = ("OK", [(b"102 (RFC822)", msg_bytes)])

    code = reader.fetch_latest_code(min_timestamp=time.time() - 30, timeout=1)
    assert code == "582910"


def test_perform_gmail_login_flow():
    fetch_mock = MagicMock(return_value="654321")
    with patch(
        "gmail_auth.GLaDOSAuthenticator.send_verification_code", return_value=True
    ), patch(
        # connect 返回 reader 本身，确保 with 块内调用的是被 patch 的 fetch_latest_code。
        "gmail_auth.GmailReader.connect", autospec=True, side_effect=lambda reader: reader
    ), patch(
        "gmail_auth.GmailReader.close"
    ), patch(
        "gmail_auth.GmailReader.fetch_latest_code", fetch_mock
    ), patch(
        "gmail_auth.GLaDOSAuthenticator.login_with_code", return_value="koa:sess=test_ok"
    ), patch(
        "gmail_auth.time.time", return_value=1000
    ):
        cookie = perform_gmail_login("test@gmail.com", "app_password_123")
        assert cookie == "koa:sess=test_ok"
    assert fetch_mock.call_args.kwargs["min_timestamp"] == 700


def test_perform_gmail_login_missing_credentials():
    assert perform_gmail_login("", "pwd") is None
    assert perform_gmail_login("user@gmail.com", "") is None


def test_perform_gmail_login_code_timeout():
    with patch(
        "gmail_auth.GLaDOSAuthenticator.send_verification_code", return_value=True
    ), patch(
        "gmail_auth.GmailReader.connect", return_value=MagicMock()
    ), patch(
        "gmail_auth.GmailReader.fetch_latest_code", return_value=None
    ):
        assert perform_gmail_login("test@gmail.com", "app_password_123") is None


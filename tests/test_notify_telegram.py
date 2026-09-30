import io
import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from scripts import notify_telegram


def response(body):
    result = MagicMock()
    result.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
    return result


def test_success_requires_api_acknowledgement(monkeypatch):
    opener = MagicMock(return_value=response({"ok": True}))
    monkeypatch.setattr(notify_telegram.urllib.request, "urlopen", opener)
    notify_telegram.send_alert("secret", "123", "签到失败")
    request = opener.call_args.args[0]
    assert json.loads(request.data)["text"] == "签到失败"
    assert json.loads(request.data)["chat_id"] == "123"
    assert opener.call_args.kwargs["timeout"] == 20


def test_api_rejection_is_not_success(monkeypatch):
    monkeypatch.setattr(
        notify_telegram.urllib.request, "urlopen",
        MagicMock(return_value=response({"ok": False, "description": "secret"})),
    )
    with pytest.raises(RuntimeError, match="未确认") as error:
        notify_telegram.send_alert("secret", "123", "failed")
    assert "secret" not in str(error.value)


def test_network_retry_is_bounded_and_safe(monkeypatch):
    opener = MagicMock(side_effect=urllib.error.URLError("https://host/botsecret"))
    monkeypatch.setattr(notify_telegram.urllib.request, "urlopen", opener)
    monkeypatch.setattr(notify_telegram.time, "sleep", MagicMock())
    with pytest.raises(RuntimeError, match="已重试 3 次") as error:
        notify_telegram.send_alert("secret", "123", "failed")
    assert opener.call_count == 3
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("status,retries", [(401, 1), (403, 1), (429, 3), (503, 3)])
def test_http_failure_does_not_expose_token(monkeypatch, status, retries):
    error = urllib.error.HTTPError("https://host/botsecret", status, "secret", {}, None)
    opener = MagicMock(side_effect=error)
    monkeypatch.setattr(notify_telegram.urllib.request, "urlopen", opener)
    monkeypatch.setattr(notify_telegram.time, "sleep", MagicMock())
    with pytest.raises(RuntimeError, match="HTTP {}".format(status)) as caught:
        notify_telegram.send_alert("secret", "123", "failed")
    assert opener.call_count == retries
    assert "secret" not in str(caught.value)


def test_dependency_failure_has_fallback_and_run_link():
    message = notify_telegram.failure_message({
        "GITHUB_REPOSITORY": "pjpjq/Gladoscheckin", "GITHUB_RUN_ID": "123",
    })
    assert "依赖安装" in message
    assert message.endswith("https://github.com/pjpjq/Gladoscheckin/actions/runs/123")


def test_message_is_bounded():
    assert len(notify_telegram.failure_message({"CHECKIN_FAILURE_SUMMARY": "坏" * 5000})) < 4096


def test_missing_secrets_is_failure(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify_telegram.main() == 1
    assert "请配置" in capsys.readouterr().out


def test_main_acknowledges_delivery(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(notify_telegram, "send_alert", MagicMock())
    assert notify_telegram.main() == 0
    assert "已确认" in capsys.readouterr().out


@pytest.mark.parametrize("body", [[], None, "invalid"])
def test_non_object_response_is_rejected(monkeypatch, body):
    monkeypatch.setattr(
        notify_telegram.urllib.request, "urlopen", MagicMock(return_value=response(body)),
    )
    with pytest.raises(RuntimeError, match="未确认"):
        notify_telegram.send_alert("secret", "123", "failed")


def test_notification_failure_propagates(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(
        notify_telegram, "send_alert", MagicMock(side_effect=RuntimeError("发送失败")),
    )
    assert notify_telegram.main() == 1
    assert "发送失败" in capsys.readouterr().out

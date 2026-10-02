import json
import os
import runpy
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import requests

import checkin
from checkin import API, DEVICE_USER_AGENTS, CheckinResult, get_failed_cookies, set_github_output


@pytest.fixture(autouse=True)
def mock_push_service(monkeypatch):
    """确保单测中永远不向真实 PushDeer 服务发送请求"""
    send_mock = MagicMock(return_value=True)
    monkeypatch.setattr(checkin.PushService, "send", send_mock)
    return send_mock


def test_empty_cookies(monkeypatch, tmp_path):
    """测试配置缺失 Cookie 时返回 1 且写入 GITHUB_OUTPUT"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.delenv("GLADOS_COOKIES", raising=False)

    exit_code = checkin.main()
    assert exit_code == 1

    content = output_file.read_text(encoding="utf-8")
    assert "failure_summary=配置缺失Cookie" in content


def test_config_exception(monkeypatch, tmp_path):
    """测试 Config 初始化抛出异常时 main() 返回 1 且 failure_summary 为异常类名"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    # GLADOS_COOKIES 设为空白分隔符会使 Config._load_config 抛出 ValueError
    monkeypatch.setenv("GLADOS_COOKIES", "   &   ")

    exit_code = checkin.main()
    assert exit_code == 1

    content = output_file.read_text(encoding="utf-8")
    assert "failure_summary=ValueError" in content


def test_no_results(monkeypatch, tmp_path):
    """测试签到未产生任何结果时 main() 返回 1"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GLADOS_COOKIES", "cookie1")

    with patch.object(checkin.Checker, "checkin_all", autospec=True) as mock_all:
        # checkin_all 执行后 self.results 保持为空列表
        exit_code = checkin.main()
        assert exit_code == 1

    content = output_file.read_text(encoding="utf-8")
    assert "failure_summary=未产生签到结果" in content


def test_multi_account_partial_failure(monkeypatch, tmp_path):
    """测试多账号部分失败：账号1成功，账号2两域名全失败，返回 1 且定位账号2"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GLADOS_COOKIES", "cookie1&cookie2")

    def mock_checkin(self):
        self.results = [
            CheckinResult(cookie_index=1, domain="glados.cloud", status="签到成功"),
            CheckinResult(cookie_index=1, domain="railgun.info", status="签到失败"),
            CheckinResult(cookie_index=2, domain="glados.cloud", status="签到失败"),
            CheckinResult(cookie_index=2, domain="railgun.info", status="签到失败"),
        ]

    with patch.object(checkin.Checker, "checkin_all", mock_checkin):
        exit_code = checkin.main()
        assert exit_code == 1

    content = output_file.read_text(encoding="utf-8")
    assert "failure_summary=账号2签到失败" in content
    # 绝不能包含原始 cookie
    assert "cookie1" not in content
    assert "cookie2" not in content


def test_unknown_status(monkeypatch, tmp_path):
    """测试未知 status：既非精确'签到成功'亦非精确'重复签到'，判定为失败"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GLADOS_COOKIES", "cookie1")

    def mock_checkin(self):
        self.results = [
            CheckinResult(cookie_index=1, domain="glados.cloud", status="未知状态"),
            CheckinResult(cookie_index=1, domain="railgun.info", status="部分成功"),
        ]

    with patch.object(checkin.Checker, "checkin_all", mock_checkin):
        exit_code = checkin.main()
        assert exit_code == 1

    content = output_file.read_text(encoding="utf-8")
    assert "failure_summary=账号1签到失败" in content


def test_repeat_success(monkeypatch, tmp_path):
    """测试 status 为'重复签到'视同成功，main() 返回 0 且无 failure_summary"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GLADOS_COOKIES", "cookie1")

    def mock_checkin(self):
        self.results = [
            CheckinResult(cookie_index=1, domain="glados.cloud", status="重复签到"),
            CheckinResult(cookie_index=1, domain="railgun.info", status="签到失败"),
        ]

    with patch.object(checkin.Checker, "checkin_all", mock_checkin):
        exit_code = checkin.main()
        assert exit_code == 0

    assert not output_file.exists() or "failure_summary" not in output_file.read_text(encoding="utf-8")


def test_backup_domain_failed_primary_succeeded(monkeypatch, tmp_path):
    """测试备用域名失败但主域成功，任一域名成功即算成功，返回 0"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GLADOS_COOKIES", "cookie1")

    def mock_checkin(self):
        self.results = [
            CheckinResult(cookie_index=1, domain="glados.cloud", status="签到成功"),
            CheckinResult(cookie_index=1, domain="railgun.info", status="签到失败"),
        ]

    with patch.object(checkin.Checker, "checkin_all", mock_checkin):
        exit_code = checkin.main()
        assert exit_code == 0

    assert not output_file.exists() or "failure_summary" not in output_file.read_text(encoding="utf-8")


def test_exchange_failure_does_not_affect_checkin_success(monkeypatch, tmp_path):
    """测试既有兑换失败不被计入签到失败，只要签到状态成功即返回 0"""
    output_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GLADOS_COOKIES", "cookie1")

    def mock_checkin(self):
        self.results = [
            CheckinResult(
                cookie_index=1,
                domain="glados.cloud",
                status="签到成功",
                exchange="兑换失败: 积分不足",
            ),
            CheckinResult(
                cookie_index=1,
                domain="railgun.info",
                status="签到失败",
                exchange="兑换失败: 网络超时",
            ),
        ]

    with patch.object(checkin.Checker, "checkin_all", mock_checkin):
        exit_code = checkin.main()
        assert exit_code == 0

    assert not output_file.exists() or "failure_summary" not in output_file.read_text(encoding="utf-8")


def test_get_failed_cookies_helper():
    """单元测试 get_failed_cookies 处理 CheckinResult 和字典形态结果"""
    results_cr = [
        CheckinResult(cookie_index=1, domain="glados.cloud", status="签到成功"),
        CheckinResult(cookie_index=2, domain="glados.cloud", status="签到失败"),
        CheckinResult(cookie_index=2, domain="railgun.info", status="签到失败"),
        CheckinResult(cookie_index=3, domain="glados.cloud", status="重复签到"),
    ]
    failed = get_failed_cookies(3, results_cr)
    assert failed == [2]

    # 测试字典结构输入
    results_dict = [
        {"cookie_index": 1, "domain": "glados.cloud", "status": "签到失败"},
        {"cookie_index": 1, "domain": "railgun.info", "status": "签到成功"},
        {"cookie_index": 2, "domain": "glados.cloud", "status": "未知状态"},
    ]
    failed_dict = get_failed_cookies(2, results_dict)
    assert failed_dict == [2]


def test_set_github_output_sanitizes_newlines(monkeypatch, tmp_path):
    """测试 set_github_output 去除换行符并单行输出"""
    out_file = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))

    set_github_output("failure_summary", "账号1签到失败\n账号2签到失败\r\n")
    content = out_file.read_text(encoding="utf-8")
    assert content == "failure_summary=账号1签到失败 账号2签到失败\n"


def test_mac_chrome_checkin_uses_mac_ua_without_retry():
    response = MagicMock()
    response.json.return_value = {"code": 0, "points": 3, "message": "签到成功"}
    with API("glados.cloud", 1) as api:
        with patch.object(api.session, "post", return_value=response) as post:
            result = api.checkin("cookie1")
        assert result["status"] == "签到成功"
        assert post.call_count == 1
        assert post.call_args.kwargs["headers"]["user-agent"] == DEVICE_USER_AGENTS["macOS"]


def test_device_mismatch_retries_once_for_this_account_only(caplog):
    mismatch = MagicMock()
    mismatch.json.return_value = {
        "code": 4,
        "reason": "device-mismatch",
        "loginDevice": "Windows",
        "message": "Automated check-in detected",
    }
    success = MagicMock()
    success.json.return_value = {"code": 1, "message": "already checked in"}
    with API("glados.cloud", 1) as first:
        with patch.object(first.session, "post", side_effect=[mismatch, success]) as post:
            assert first.checkin("cookie1")["status"] == "重复签到"
        assert [call.kwargs["headers"]["user-agent"] for call in post.call_args_list] == [
            DEVICE_USER_AGENTS["macOS"], DEVICE_USER_AGENTS["Windows"]
        ]
        assert first.headers["user-agent"] == DEVICE_USER_AGENTS["Windows"]
    with API("glados.cloud", 2) as second:
        with patch.object(second.session, "post", return_value=success) as post:
            assert second.checkin("cookie2")["status"] == "重复签到"
        assert post.call_args.kwargs["headers"]["user-agent"] == DEVICE_USER_AGENTS["macOS"]
    assert "cookie1" not in caplog.text
    assert "cookie2" not in caplog.text


@pytest.mark.parametrize("reason,device", [
    ("other", "Windows"),
    ("device-mismatch", "unknown-device"),
    ("device-mismatch", "macOS"),
])
def test_checkin_does_not_retry_without_different_known_device(reason, device, caplog):
    response = MagicMock()
    response.json.return_value = {
        "code": 4, "reason": reason, "loginDevice": device,
        "message": "Automated check-in detected",
    }
    with API("glados.cloud", 1) as api:
        with patch.object(api.session, "post", return_value=response) as post:
            assert api.checkin("cookie1")["status"] == "签到失败"
        assert post.call_count == 1
    if device == "unknown-device":
        assert "loginDevice=unknown" in caplog.text


def test_checkin_logs_only_known_reason_and_device(caplog):
    response = MagicMock()
    response.json.return_value = {
        "code": 4, "reason": "secret=never-log", "loginDevice": ["unexpected"],
        "message": "Automated check-in detected secret=never-log",
    }
    with API("glados.cloud", 1) as api:
        with patch.object(api.session, "post", return_value=response) as post:
            assert api.checkin("cookie1")["status"] == "签到失败"
        assert post.call_count == 1
    assert "reason=other, loginDevice=unknown" in caplog.text
    assert "secret=never-log" not in caplog.text


def test_checkin_retries_at_most_once_when_mismatch_persists():
    response = MagicMock()
    response.json.return_value = {
        "code": 4, "reason": "device-mismatch", "loginDevice": "Windows",
        "message": "Automated check-in detected",
    }
    with API("glados.cloud", 1) as api:
        with patch.object(api.session, "post", return_value=response) as post:
            assert api.checkin("cookie1")["status"] == "签到失败"
        assert post.call_count == 2


def test_device_mismatch_on_http_403_is_retried_without_logging_response(caplog):
    rejected = requests.Response()
    rejected.status_code = 403
    rejected._content = json.dumps({
        "code": 4, "reason": "device-mismatch", "loginDevice": "Windows",
        "message": "secret=never-log",
    }).encode()
    accepted = requests.Response()
    accepted.status_code = 200
    accepted._content = b'{"code": 1, "message": "already checked in"}'
    with API("glados.cloud", 1) as api:
        with patch.object(api.session, "post", side_effect=[rejected, accepted]) as post:
            assert api.checkin("cookie1")["status"] == "重复签到"
        assert post.call_count == 2
        assert post.call_args.kwargs["headers"]["user-agent"] == DEVICE_USER_AGENTS["Windows"]
    assert "secret=never-log" not in caplog.text


def test_system_exit_in_dunder_main(monkeypatch):
    """测试 __main__ 调用 SystemExit(main())"""
    monkeypatch.delenv("GLADOS_COOKIES", raising=False)
    monkeypatch.delenv("PUSHDEER_SENDKEY", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        # 模拟执行 checkin.py 的 __main__
        runpy.run_path(str(Path(__file__).resolve().parents[1] / "checkin.py"), run_name="__main__")
    assert exc_info.value.code == 1

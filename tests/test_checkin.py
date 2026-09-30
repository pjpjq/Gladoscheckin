import os
import runpy
from unittest.mock import MagicMock, patch
import pytest

import checkin
from checkin import CheckinResult, get_failed_cookies, set_github_output


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


def test_system_exit_in_dunder_main(monkeypatch):
    """测试 __main__ 调用 SystemExit(main())"""
    with patch("checkin.main", return_value=1):
        with pytest.raises(SystemExit) as exc_info:
            # 模拟执行 checkin.py 的 __main__
            runpy.run_path("/tmp/glados-notify.Oj7pfd/repo/checkin.py", run_name="__main__")
        assert exc_info.value.code == 1

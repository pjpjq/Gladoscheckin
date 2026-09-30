"""Send one failure alert, without requiring the check-in dependencies."""

import datetime
import json
import os
import time
import urllib.error
import urllib.request


def failure_message(environ):
    """Include a safe account summary and a direct link to the failed run."""
    summary = environ.get("CHECKIN_FAILURE_SUMMARY", "").strip()
    if not summary:
        summary = "签到任务未正常完成（可能是依赖安装、运行异常或超时），请查看日志。"
    server = environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = environ.get("GITHUB_REPOSITORY", "")
    run_id = environ.get("GITHUB_RUN_ID", "")
    run_url = "{}/{}/actions/runs/{}".format(server, repo, run_id)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return "❌ GLaDOS 自动签到失败\n时间：{}\n{}\n日志：{}".format(
        timestamp, summary[:3000], run_url
    )


def send_alert(token, chat_id, message):
    """Require Telegram's API acknowledgement; never print credential-bearing URLs."""
    payload = json.dumps(
        {"chat_id": chat_id, "text": message, "disable_web_page_preview": True}
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.telegram.org/bot{}/sendMessage".format(token),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = json.load(response)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise RuntimeError("Telegram API 未确认通知发送成功")
            return
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError("Telegram 请求失败（HTTP {}）".format(exc.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 2:
                raise RuntimeError("Telegram 网络请求失败，已重试 3 次") from None
        except (ValueError, TypeError):
            raise RuntimeError("Telegram 返回了无效响应") from None
        time.sleep(2 ** (attempt + 1))


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram 通知失败：请配置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID Secrets")
        return 1
    try:
        send_alert(token, chat_id, failure_message(os.environ))
    except RuntimeError as exc:
        print(str(exc))
        return 1
    print("Telegram 已确认失败通知发送成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

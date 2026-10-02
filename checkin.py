import requests
import json
import os
import logging
from enum import Enum
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
from pypushdeer import PushDeer
from logging_config import init_logger


class CheckinStatus(Enum):
    """签到状态枚举"""

    SUCCESS = 0
    REPEAT = 1
    FAILURE = -1


class ExchangePlan(Enum):
    """兑换计划枚举"""

    PLAN100 = "plan100"
    PLAN200 = "plan200"
    PLAN500 = "plan500"


class APIEndpoint(Enum):
    """API端点枚举"""

    CHECKIN = "/api/user/checkin"
    STATUS = "/api/user/status"
    POINTS = "/api/user/points"
    EXCHANGE = "/api/user/exchange"


class LogEmoji:
    """日志 Emoji 常量"""

    SUCCESS = "✅"
    FAIL = "❌"
    REPEAT = "🔄"
    PENDING = "⏳"
    CHECKIN = "🎫"
    STATUS = "📊"
    POINTS = "💰"
    EXCHANGE = "🎁"
    START = "🚀"
    END = "🏁"
    COOKIE = "🍪"
    DOMAIN = "🌐"
    WARNING = "⚠️"
    ERROR = "🔴"
    INFO = "ℹ️"


def log_method(func):
    """日志装饰器"""

    def wrapper(self, *args, **kwargs):
        method_name = func.__name__
        emoji_map = {
            "checkin": LogEmoji.CHECKIN,
            "get_status": LogEmoji.STATUS,
            "get_points": LogEmoji.POINTS,
            "exchange": LogEmoji.EXCHANGE,
        }
        emoji = emoji_map.get(method_name, LogEmoji.INFO)
        try:
            result = func(self, *args, **kwargs)
            return result
        except Exception as e:
            logger.error(
                f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.ERROR} {method_name} 执行失败: {e}"
            )
            # 对于不同的方法，返回不同的默认值
            if method_name == "checkin":
                return {
                    "status": "签到失败",
                    "points": "0",
                    "message": f"执行失败: {e}",
                }
            elif method_name == "get_status":
                return "获取失败"
            elif method_name == "get_points":
                return "获取失败", 0
            elif method_name == "exchange":
                return f"兑换失败: {e}"
            else:
                raise

    return wrapper


class Config:
    """应用配置管理"""

    ENV_PUSH_KEY = "PUSHDEER_SENDKEY"
    ENV_COOKIES = "GLADOS_COOKIES"
    ENV_EXCHANGE_PLAN = "GLADOS_EXCHANGE_PLAN"

    """默认兑换计划"""
    DEFAULT_EXCHANGE_PLAN = "plan500"

    """默认域名"""
    DOMAINS = ["glados.cloud", "railgun.info"]

    """兑换计划列表"""
    EXCHANGE_PLANS = {
        ExchangePlan.PLAN100.value: 100,
        ExchangePlan.PLAN200.value: 200,
        ExchangePlan.PLAN500.value: 500,
    }

    def __init__(self):
        self.push_key: str = ""
        self.cookies_list: List[str] = []
        self.exchange_plan: str = self.DEFAULT_EXCHANGE_PLAN
        self._load_config()

    def _load_config(self) -> None:
        """加载配置"""
        push_key_env: Optional[str] = os.environ.get(self.ENV_PUSH_KEY)
        raw_cookies_env: Optional[str] = os.environ.get(self.ENV_COOKIES)
        exchange_plan_env: Optional[str] = os.environ.get(self.ENV_EXCHANGE_PLAN)

        if not push_key_env:
            logger.warning(
                f"{LogEmoji.WARNING} 环境变量 '{self.ENV_PUSH_KEY}' 未设置。"
            )
            self.push_key = ""
        else:
            self.push_key = push_key_env

        if not raw_cookies_env:
            logger.warning(f"{LogEmoji.WARNING} 环境变量 '{self.ENV_COOKIES}' 未设置。")
            self.cookies_list = []
        else:
            self.cookies_list = [
                cookie.strip()
                for cookie in raw_cookies_env.split("&")
                if cookie.strip()
            ]
            if not self.cookies_list:
                raise ValueError(
                    f"环境变量 '{self.ENV_COOKIES}' 已设置，但未包含任何有效的 Cookie。"
                )

        if not exchange_plan_env:
            logger.warning(
                f"{LogEmoji.WARNING} 环境变量 '{self.ENV_EXCHANGE_PLAN}' 未设置，将使用默认兑换计划 {self.DEFAULT_EXCHANGE_PLAN}。"
            )
            self.exchange_plan = self.DEFAULT_EXCHANGE_PLAN
        else:
            if exchange_plan_env in self.EXCHANGE_PLANS:
                self.exchange_plan = exchange_plan_env
                logger.info(
                    f"{LogEmoji.SUCCESS} 使用指定的兑换计划: {self.exchange_plan}"
                )
            else:
                logger.warning(
                    f"{LogEmoji.WARNING} 环境变量 '{self.ENV_EXCHANGE_PLAN}' 的值 '{exchange_plan_env}' 无效，将使用默认兑换计划 {self.DEFAULT_EXCHANGE_PLAN}。"
                )
                self.exchange_plan = self.DEFAULT_EXCHANGE_PLAN

        logger.info(
            f"{LogEmoji.INFO} 共加载了 {len(self.cookies_list)} 个 Cookie 用于签到。"
        )
        logger.info(
            f"{LogEmoji.INFO} 当前 {self.ENV_PUSH_KEY} {'已设置' if push_key_env else '未设置'}。"
        )
        logger.info(
            f"{LogEmoji.INFO} 当前 {self.ENV_EXCHANGE_PLAN}: {self.exchange_plan}。"
        )


DEVICE_USER_AGENTS = {
    "macOS": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "iPhone": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Android": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
}


class API:
    """API 调用"""

    CHECKIN_URL = APIEndpoint.CHECKIN.value
    STATUS_URL = APIEndpoint.STATUS.value
    POINTS_URL = APIEndpoint.POINTS.value
    EXCHANGE_URL = APIEndpoint.EXCHANGE.value

    def __init__(self, domain: str, cookie_index: int = 0):
        self.domain: str = domain
        self.cookie_index: int = cookie_index
        self.headers: Dict[str, str] = self._get_headers()
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def __del__(self):
        """析构方法，关闭 session"""
        self.close()

    def close(self) -> None:
        """关闭 session"""
        if hasattr(self, "session"):
            try:
                self.session.close()
            except Exception as e:
                logger.error(f"{LogEmoji.ERROR} 关闭 session 时发生错误: {e}")

    def __enter__(self):
        """进入上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器"""
        self.close()
        return False

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        return {
            "origin": f"https://{self.domain}",
            "user-agent": DEVICE_USER_AGENTS["macOS"],
        }

    def _get_full_url(self, path: str) -> str:
        """获取完整 URL"""
        return f"https://{self.domain}{path}"

    def _make_request(
        self, url: str, method: str, data: Optional[Dict] = None, cookies: str = ""
    ) -> Optional[requests.Response]:
        """发送 HTTP 请求"""
        session_headers = self.headers.copy()
        session_headers["cookie"] = cookies

        try:
            if method.upper() == "POST":
                response = self.session.post(
                    url,
                    headers=session_headers,
                    data=json.dumps(data),
                    timeout=(60, 120),  # 连接超时 60 秒，读取超时 120 秒
                )
            elif method.upper() == "GET":
                response = self.session.get(
                    url,
                    headers=session_headers,
                    timeout=(60, 120),  # 连接超时 60 秒，读取超时 120 秒
                )
            else:
                logger.error(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.ERROR} 不支持的 HTTP 方法: {method}"
                )
                return None

            if not response.ok:
                logger.warning(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.WARNING} 向 {url} 发起的请求失败，状态码 {response.status_code}。响应内容: {response.text}"
                )
                return None
            return response
        except requests.exceptions.RequestException as e:
            logger.error(
                f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.ERROR} 向 {url} 发起请求时发生网络错误: {e}"
            )
            return None

    def _get_checkin_data(self) -> Dict[str, str]:
        """获取签到数据"""
        return {"token": self.domain}

    @log_method
    def checkin(self, cookies: str) -> Dict[str, str]:
        """执行签到"""
        url = self._get_full_url(self.CHECKIN_URL)
        checkin_data = self._get_checkin_data()
        response = self._make_request(url, "POST", checkin_data, cookies)

        result = {"status": "签到失败", "points": "0", "message": ""}

        if response:
            data = response.json()
            if data.get("code") == 4:
                reason = data.get("reason")
                login_device = data.get("loginDevice")
                safe_reason = "device-mismatch" if reason == "device-mismatch" else "other"
                safe_device = login_device if isinstance(login_device, str) and login_device in DEVICE_USER_AGENTS else "unknown"
                logger.warning(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] "
                    f"{LogEmoji.WARNING} 签到被拒绝: reason={safe_reason}, loginDevice={safe_device}"
                )
                if reason == "device-mismatch":
                    retry_ua = DEVICE_USER_AGENTS.get(safe_device)
                    if retry_ua and retry_ua != self.headers["user-agent"]:
                        self.headers["user-agent"] = retry_ua
                        self.session.headers["user-agent"] = retry_ua
                        response = self._make_request(url, "POST", checkin_data, cookies)
                        data = response.json() if response else {"message": "网络请求失败"}

            code = data.get("code", 1)
            message = "签到被服务端拒绝" if code == 4 else data.get("message", "无消息字段")
            points = str(data.get("points", 0))

            # 只记录必要的字段
            if code == CheckinStatus.SUCCESS.value:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.SUCCESS} {{ code : {code}, points : {points}, message : {message} }}"
                )
                result["status"] = "签到成功"
                result["points"] = points
                result["message"] = message
            elif code == CheckinStatus.REPEAT.value:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.REPEAT} {{ code : {code}, message : {message} }}"
                )
                result["status"] = "重复签到"
                result["points"] = "0"
                result["message"] = message
            else:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.FAIL} {{ code : {code}, message : {message} }}"
                )
                result["status"] = "签到失败"
                result["points"] = "0"
                result["message"] = message
        else:
            logger.warning(
                f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.WARNING} 签到失败"
            )
            result["status"] = "签到失败"
            result["message"] = "网络请求失败"

        return result

    @log_method
    def get_status(self, cookies: str) -> str:
        """获取状态"""
        url = self._get_full_url(self.STATUS_URL)
        response = self._make_request(url, "GET", cookies=cookies)

        if response:
            data = response.json()
            code = data.get("code", -1)
            left_days = data.get("data", {}).get("leftDays", None)
            # 只记录必要的字段
            if left_days is not None:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.SUCCESS} {{ code : {code}, leftDays : {left_days} }}"
                )
                return f"{int(float(left_days))} 天"
            else:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.FAIL} {{ code : {code}, leftDays : {left_days} }}"
                )
                return "获取失败"
        else:
            logger.warning(
                f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.WARNING} 获取状态失败"
            )
            return "获取失败"

    @log_method
    def get_points(self, cookies: str) -> Tuple[str, int]:
        """获取积分"""
        url = self._get_full_url(self.POINTS_URL)
        response = self._make_request(url, "GET", cookies=cookies)

        if response:
            data = response.json()
            code = data.get("code", -1)
            points = data.get("points", None)
            # 只记录必要的字段
            if points is not None:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.SUCCESS} {{ code : {code}, points : {points} }}"
                )
                points_str = f"{int(float(points))} 积分"
                points_num = int(float(points))
                return points_str, points_num
            else:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.FAIL} {{ code : {code}, points : {points} }}"
                )
                return "获取失败", 0
        else:
            logger.warning(
                f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.WARNING} 获取积分失败"
            )
            return "获取失败", 0

    @log_method
    def exchange(self, cookies: str, plan: str, required_points: int) -> str:
        """执行兑换"""
        url = self._get_full_url(self.EXCHANGE_URL)
        response = self._make_request(url, "POST", {"planType": plan}, cookies)

        if response:
            data = response.json()
            code = data.get("code", -1)
            message = data.get("message", "未知错误")
            # 只记录必要的字段
            if code == 0:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.SUCCESS} {{ code : {code}, message : {message} }}"
                )
                return f"兑换成功: {plan}"
            else:
                logger.info(
                    f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.FAIL} {{ code : {code}, message : {message} }}"
                )
                return f"兑换失败: {message}"
        else:
            logger.warning(
                f"{LogEmoji.COOKIE}[{self.cookie_index}] {LogEmoji.DOMAIN}[{self.domain}] {LogEmoji.WARNING} 兑换失败"
            )
            return "兑换失败"


@dataclass()
class CheckinResult:
    """签到结果"""

    cookie_index: int
    domain: str
    status: str = "签到失败"
    points: str = "0"
    days: str = "获取失败"
    points_total: str = "获取失败"
    exchange: str = "未兑换"

    def to_dict(self) -> Dict[str, Union[str, int]]:
        """转换为字典"""
        return asdict(self)

    @property
    def is_success(self) -> bool:
        """是否签到成功"""
        return "成功" in self.status

    @property
    def is_failure(self) -> bool:
        """是否签到失败"""
        return "失败" in self.status

    @property
    def is_repeat(self) -> bool:
        """是否重复签到"""
        return "重复" in self.status

    @property
    def short_description(self) -> str:
        """简短描述"""
        return f"[{self.domain}] {self.status} - {self.points}积分"


class PushService:
    """推送服务"""

    def __init__(self, push_key: str):
        self.push_key = push_key

    def send(self, title: str, content: str) -> bool:
        """发送推送"""
        if not self.push_key:
            logger.info(f"{LogEmoji.INFO} 未设置推送密钥，跳过推送通知。")
            return False

        try:
            pushdeer = PushDeer(pushkey=self.push_key)
            pushdeer.send_text(title, desp=content)
            logger.info(f"{LogEmoji.SUCCESS} 推送通知发送成功。")
            return True
        except Exception as e:
            logger.error(f"{LogEmoji.ERROR} 发送推送通知失败: {e}")
            return False


class Checker:
    """签到器"""

    def __init__(self, config: Config):
        self.config = config
        self.results = []

    def checkin_all(self):
        """执行所有签到任务"""
        cookie_count = len(self.config.cookies_list)
        domain_count = len(self.config.DOMAINS)
        total_tasks = cookie_count * domain_count
        task_idx = 0

        logger.info(
            f"{LogEmoji.INFO} 共 {cookie_count} 个 Cookie, {domain_count} 个域名, 共 {total_tasks} 个任务"
        )

        for cookie_idx, cookie in enumerate(self.config.cookies_list, 1):
            logger.info(
                f"{LogEmoji.START} ========== 开始处理 Cookie {cookie_idx} =========="
            )

            for domain in self.config.DOMAINS:
                task_idx += 1
                logger.info(
                    f"{LogEmoji.INFO} ----- 任务 {task_idx}/{total_tasks}: Cookie {cookie_idx} on {domain} -----"
                )

                result = self._checkin_on_domain(cookie, cookie_idx, domain)
                self.results.append(result)

                logger.info(
                    f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] {LogEmoji.INFO} 结果: {result.status}, 获得 {result.points} 积分, "
                    f"剩余 {result.days}, 总 {result.points_total}, {result.exchange}"
                )

    def _checkin_on_domain(
        self, cookie: str, cookie_idx: int, domain: str
    ) -> CheckinResult:
        """在指定域名上执行签到"""
        result = CheckinResult(cookie_idx, domain)

        # 使用上下文管理器管理API对象的生命周期
        with API(domain, cookie_idx) as api:
            # 1. 签到
            logger.info(
                f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] {LogEmoji.CHECKIN} 执行签到"
            )
            checkin_result = api.checkin(cookie)
            result.status = checkin_result["status"]
            result.points = checkin_result["points"]

            # 2. 获取状态
            logger.info(
                f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] {LogEmoji.STATUS} 查询剩余天数"
            )
            result.days = api.get_status(cookie)

            # 3. 获取积分
            logger.info(
                f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] {LogEmoji.POINTS} 查询总积分"
            )
            points_str, points_num = api.get_points(cookie)
            result.points_total = points_str

            # 4. 执行兑换
            required_points = self.config.EXCHANGE_PLANS.get(
                self.config.exchange_plan, 500
            )
            logger.info(
                f"{LogEmoji.COOKIE}[{cookie_idx}] {LogEmoji.DOMAIN}[{domain}] {LogEmoji.EXCHANGE} 开始兑换 {self.config.exchange_plan} (需要 {required_points} 积分)"
            )
            result.exchange = api.exchange(
                cookie, self.config.exchange_plan, required_points
            )

        return result

    def get_results(self) -> List[Dict[str, str]]:
        """获取所有结果"""
        return [result.to_dict() for result in self.results]

    def format_results(self) -> Tuple[str, str]:
        """格式化结果"""
        results = self.get_results()

        success_count = sum(1 for r in results if "成功" in r["status"])
        fail_count = sum(1 for r in results if "失败" in r["status"])
        repeat_count = sum(1 for r in results if "重复" in r["status"])

        title = (
            f"GLaDOS 签到, 成功{success_count}, 失败{fail_count}, 重复{repeat_count}"
        )

        content_lines = []
        for i, res in enumerate(results, 1):
            line_parts = [
                f"#{i}",
                f"[Cookie {res['cookie_index']}]",
                f"[{res['domain']}]",
                f"P:{res['points']}",
                f"剩余:{res['days']}",
                f"总积分:{res['points_total']}",
                f"| {res['status']}",
                f"| {res['exchange']}",
            ]
            line = " ".join(line_parts)
            content_lines.append(line)

        content = "\n".join(content_lines)
        return title, content

    def get_failed_cookies(self) -> List[int]:
        """获取所有签到失败的 Cookie 序号列表"""
        cookie_count = len(self.config.cookies_list) if self.config else 0
        return get_failed_cookies(cookie_count, self.results)


# 初始化日志
logger = init_logger()


def get_failed_cookies(
    cookies_count: int, results: List[Union[CheckinResult, Dict]]
) -> List[int]:
    """返回所有签到失败的 Cookie 序号列表（1-based）。

    每个 Cookie 若任一域名结果 status 精确为 '签到成功' 或 '重复签到' 就算该账号成功，否则失败。
    """
    failed = []
    for idx in range(1, cookies_count + 1):
        account_results = [
            r
            for r in results
            if (
                r.cookie_index
                if hasattr(r, "cookie_index")
                else r.get("cookie_index")
            )
            == idx
        ]
        has_success = any(
            (r.status if hasattr(r, "status") else r.get("status"))
            in ("签到成功", "重复签到")
            for r in account_results
        )
        if not has_success:
            failed.append(idx)
    return failed


def set_github_output(key: str, value: str) -> None:
    """写入 GitHub Actions 输出参数"""
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    try:
        sanitized_value = str(value).replace("\r", " ").replace("\n", " ").strip()
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"{key}={sanitized_value}\n")
    except Exception as e:
        logger.warning(f"{LogEmoji.WARNING} 写入 GITHUB_OUTPUT 失败: {e}")


def main() -> int:
    """主函数"""
    exit_code = 0
    failure_summary = ""
    try:
        # 1. 加载配置
        logger.info(f"{LogEmoji.START} 步骤 1: 加载配置")
        config = Config()

        if not config.cookies_list:
            logger.error(f"{LogEmoji.ERROR} 未找到有效的 Cookie，退出程序。")
            title, content = "# 未找到 cookies!", ""
            exit_code = 1
            failure_summary = "配置缺失Cookie"
        else:
            # 2. 执行签到
            logger.info(f"{LogEmoji.START} 步骤 2: 执行签到")
            checker = Checker(config)
            checker.checkin_all()

            # 3. 格式化结果
            logger.info(f"{LogEmoji.START} 步骤 3: 格式化结果")
            title, content = checker.format_results()
            logger.info(
                f"\n{LogEmoji.END}========== 推送内容 ==========\n{title}\n{content}"
            )

            if not checker.results:
                exit_code = 1
                failure_summary = "未产生签到结果"
            else:
                failed_cookies = checker.get_failed_cookies()
                if failed_cookies:
                    exit_code = 1
                    failure_summary = "; ".join(
                        f"账号{idx}签到失败" for idx in failed_cookies
                    )
                else:
                    exit_code = 0

    except Exception as e:
        logger.error(f"{LogEmoji.ERROR} 主程序执行过程中发生未预期的错误: {e}")
        title, content = "# 脚本执行出错", str(e)
        exit_code = 1
        failure_summary = type(e).__name__

    # 4. 发送推送
    logger.info(f"{LogEmoji.START} 步骤 4: 发送推送")
    push_service = PushService(config.push_key if "config" in locals() else "")
    push_service.send(title, content)
    logger.info(f"{LogEmoji.END} 签到完成")

    if exit_code != 0:
        if failure_summary:
            set_github_output("failure_summary", failure_summary)
        return exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

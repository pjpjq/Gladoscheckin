import email
import email.utils
import hashlib
import imaplib
import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CODE_PATTERNS = [
    r"(?:verification code|access code|security code|verify code|验证码|动态码)[^\d\r\n]{0,30}?([0-9]{5,8})",
    r"\*([0-9]{5,8})\*",
    r">\s*([0-9]{6})\s*<",
    r"(?:code|Code)[^\d\r\n]{0,15}?([0-9]{5,8})",
    r"\b([0-9]{6})\b",
]


def generate_browser_fingerprint(email_address: str) -> str:
    """生成合规的 GLaDOS 请求头指纹 (fp1.前48位十六进制)."""
    normalized = email_address.strip().lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"fp1.{digest[:48]}"


class GLaDOSAuthenticator:
    """GLaDOS 邮箱验证码登录与 Cookie 换取器"""

    AUTH_PATH = "/api/authorization"
    LOGIN_PATH = "/api/login"

    def __init__(self, domain: str = "glados.cloud", user_agent: Optional[str] = None):
        self.domain = domain.strip()
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.session = requests.Session()
        self.session.headers.update(
            {
                "origin": f"https://{self.domain}",
                "referer": f"https://{self.domain}/login",
                "user-agent": self.user_agent,
                "content-type": "application/json",
            }
        )

    def close(self) -> None:
        """关闭会话"""
        try:
            self.session.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def send_verification_code(self, email_address: str) -> bool:
        """向 GLaDOS 请求发送邮箱验证码"""
        clean_email = email_address.strip().lower()
        fp = generate_browser_fingerprint(clean_email)
        headers = {"authorization": fp}
        url = f"https://{self.domain}{self.AUTH_PATH}"
        payload = {
            "address": clean_email,
            "site": "glados.network",
        }
        try:
            resp = self.session.post(url, headers=headers, json=payload, timeout=(15, 30))
            if not resp.ok:
                logger.warning(
                    f"向 {self.domain} 发送验证码 HTTP 失败: status={resp.status_code}"
                )
                return False
            data = resp.json()
            code = data.get("code")
            if data.get("captcha_required"):
                logger.warning("GLaDOS 要求人机验证，验证码尚未发送，停止自动收码")
                return False
            if code == 0:
                logger.info(f"验证码发码请求成功: {data.get('message', 'ok')}")
                return True
            logger.warning(f"验证码发码被拒绝: code={code}, message={data.get('message')}")
            return False
        except Exception as e:
            logger.error(f"发送验证码时发生网络或解析错误: {e}")
            return False

    def login_with_code(self, email_address: str, mailcode: str) -> Optional[str]:
        """使用邮箱和验证码登录换取 Cookie 字符串"""
        clean_email = email_address.strip().lower()
        clean_code = mailcode.strip()
        url = f"https://{self.domain}{self.LOGIN_PATH}"
        payload = {
            "method": "email",
            "site": "glados.network",
            "email": clean_email,
            "mailcode": clean_code,
        }
        try:
            resp = self.session.post(url, json=payload, timeout=(15, 30))
            if not resp.ok:
                logger.warning(f"登录请求 HTTP 失败: status={resp.status_code}")
                return None
            data = resp.json()
            if data.get("code") != 0:
                logger.warning(
                    f"登录失败: code={data.get('code')}, message={data.get('message')}"
                )
                return None

            cookie_parts = []
            for k, v in self.session.cookies.items():
                cookie_parts.append(f"{k}={v}")

            # 补充可能只在本次响应中但未进入 session 的 cookie
            if hasattr(resp, "cookies"):
                for k, v in resp.cookies.items():
                    entry = f"{k}={v}"
                    if entry not in cookie_parts:
                        cookie_parts.append(entry)

            cookie_str = "; ".join(cookie_parts)
            if not cookie_str:
                logger.warning("登录成功但响应中未包含有效 Cookie")
                return None
            return cookie_str
        except Exception as e:
            logger.error(f"登录换取 Cookie 发生错误: {e}")
            return None


class GmailReader:
    """Gmail IMAP 邮件读取与验证码解析"""

    def __init__(
        self,
        email_address: str,
        app_password: str,
        imap_host: str = "imap.gmail.com",
        imap_port: int = 993,
    ):
        self.email_address = email_address.strip()
        self.app_password = app_password.strip().replace(" ", "")
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.mail: Optional[imaplib.IMAP4_SSL] = None

    def connect(self):
        """建立 IMAP SSL 连接并登录"""
        self.mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=30)
        self.mail.login(self.email_address, self.app_password)
        return self

    def close(self) -> None:
        """关闭连接"""
        if self.mail is not None:
            try:
                self.mail.close()
            except Exception:
                pass
            try:
                self.mail.logout()
            except Exception:
                pass
            self.mail = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @staticmethod
    def extract_body(msg: email.message.Message) -> str:
        """从邮件对象中提取纯文本及 HTML 正文"""
        parts = []
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition", ""))
                if ctype in ("text/plain", "text/html") and "attachment" not in disp:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        try:
                            parts.append(payload.decode(charset, errors="replace"))
                        except Exception:
                            parts.append(payload.decode("utf-8", errors="replace"))
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                try:
                    parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    parts.append(payload.decode("utf-8", errors="replace"))
        return "\n".join(parts)

    @staticmethod
    def extract_code(text: str) -> Optional[str]:
        """从邮件文本中提取验证码"""
        if not text:
            return None
        for pat in CODE_PATTERNS:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def _check_mailbox(
        self, mailbox: str, min_timestamp: float, sender_keyword: str
    ) -> Optional[str]:
        """在指定的邮箱文件夹中查找最新验证码"""
        if not self.mail:
            return None
        try:
            status, _ = self.mail.select(f'"{mailbox}"' if " " in mailbox else mailbox)
            if status != "OK":
                return None
        except Exception:
            return None

        msg_ids = []
        try:
            status, data = self.mail.search(None, f'(FROM "{sender_keyword}")')
            if status == "OK" and data and data[0]:
                msg_ids = data[0].split()
        except Exception:
            pass

        if not msg_ids:
            try:
                status, data = self.mail.search(None, f'(SUBJECT "{sender_keyword}")')
                if status == "OK" and data and data[0]:
                    msg_ids = data[0].split()
            except Exception:
                pass

        if not msg_ids:
            try:
                status, data = self.mail.search(None, "ALL")
                if status == "OK" and data and data[0]:
                    msg_ids = data[0].split()[-5:]
            except Exception:
                pass

        for mid in reversed(msg_ids):
            try:
                status, msg_data = self.mail.fetch(mid, "(RFC822)")
                if status != "OK" or not msg_data:
                    continue
                raw_bytes = msg_data[0][1]
                msg = email.message_from_bytes(raw_bytes)

                date_header = msg.get("Date")
                if date_header:
                    try:
                        msg_dt = email.utils.parsedate_to_datetime(date_header)
                        if msg_dt.timestamp() < min_timestamp:
                            break
                    except Exception:
                        pass

                sender = msg.get("From", "").lower()
                subject = msg.get("Subject", "").lower()
                if sender_keyword not in sender and sender_keyword not in subject:
                    continue

                body = self.extract_body(msg)
                code = self.extract_code(body)
                if code:
                    return code
            except Exception as e:
                logger.debug(f"解析邮件 {mid} 出错: {e}")
                continue

        return None

    def fetch_latest_code(
        self,
        min_timestamp: Optional[float] = None,
        timeout: int = 60,
        poll_interval: int = 3,
        sender_keyword: str = "glados",
    ) -> Optional[str]:
        """轮询 Gmail 获取符合时间戳条件的最新验证码"""
        start_time = time.time()
        min_ts = min_timestamp if min_timestamp is not None else (start_time - 60)

        mailboxes = ["INBOX", "[Gmail]/Spam", "[Gmail]/All Mail"]
        while time.time() - start_time < timeout:
            for mb in mailboxes:
                code = self._check_mailbox(mb, min_ts, sender_keyword)
                if code:
                    return code
            time.sleep(poll_interval)
        return None


def perform_gmail_login(
    email_address: str,
    app_password: str,
    domain: str = "glados.cloud",
    timeout: int = 60,
    poll_interval: int = 3,
    user_agent: Optional[str] = None,
) -> Optional[str]:
    """通过 Gmail 接收验证码并完成 GLaDOS 登录换取 Cookie 字符串"""
    if not email_address or not app_password:
        logger.warning("未提供有效的 Gmail 地址或应用密码，跳过接码登录")
        return None

    t0 = time.time()
    with GLaDOSAuthenticator(domain=domain, user_agent=user_agent) as auth:
        logger.info(f"正在向 {domain} 请求为 {email_address} 发送验证码...")
        if not auth.send_verification_code(email_address):
            logger.error(f"向 {domain} 请求验证码失败")
            return None

        logger.info(f"验证码请求已提交，等待 Gmail IMAP 收信 (超时 {timeout} 秒)...")
        try:
            with GmailReader(email_address, app_password) as reader:
                code = reader.fetch_latest_code(
                    # 允许验证码邮件在 CI 排队或邮件投递延迟后仍被使用。
                    min_timestamp=t0 - 5 * 60,
                    timeout=timeout,
                    poll_interval=poll_interval,
                )
        except Exception as e:
            logger.error(f"连接 Gmail IMAP 收信时发生错误: {e}")
            return None

        if not code:
            logger.error(f"在指定时间内未从 Gmail 收到来自 GLaDOS 的验证码")
            return None

        # 安全脱敏日志
        masked_code = f"{code[:2]}***{code[-1:]}" if len(code) >= 3 else "***"
        logger.info(f"成功提取到验证码 {masked_code}，正在向 {domain} 换取登录态...")

        cookie_str = auth.login_with_code(email_address, code)
        if cookie_str:
            logger.info(f"GLaDOS 账号 {email_address} 重新登录成功并获取新 Cookie！")
            return cookie_str
        logger.error(f"GLaDOS 账号 {email_address} 验证码登录失败")
        return None


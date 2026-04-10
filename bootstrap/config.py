import os, sys
from pathlib import Path
import logging
logger = logging.getLogger(__name__)

def load_env(path: str):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        # срезаем хвостовой комментарий, если значение не в кавычках
        if not (v.startswith('"') and v.endswith('"')) and not (v.startswith("'") and v.endswith("'")):
            if "#" in v:
                v = v.split("#", 1)[0].rstrip()
        os.environ.setdefault(k, v)

def must(k: str) -> str:
    v = os.environ.get(k, "")
    if not v:
        print(f"ENV {k} is required", file=sys.stderr)
        sys.exit(1)
    return v

# Загружаем .env
load_env(os.environ.get("BOT_ENV_FILE", "/opt/sms/.env"))

class Config:
    def __init__(self):
        self.BOT_TOKEN       = must("BOT_TOKEN")
        self.ADMIN_LOGIN     = must("ADMIN_LOGIN")              # Telegram username (без @)

        self.TG_HOST         = must("TG_HOST")
        self.TG_PORT         = int(os.environ.get("TG_PORT","5038"))
        self.TG_USER         = must("TG_USER")
        self.TG_PASS         = must("TG_PASS")
        self.TG_DEFAULT_SIM  = int(os.environ.get("TG_DEFAULT_SIM","1"))

        self.ASTERISK_CLI    = os.environ.get("ASTERISK_CLI","/usr/sbin/asterisk")
        self.ASTERISK_LOG    = os.environ.get("ASTERISK_LOG","/var/log/asterisk/messages")
        self.OS_LOG          = os.environ.get("OS_LOG","/var/log/syslog")

        self.GIT_REPO_DIR    = os.environ.get("GIT_REPO_DIR","/opt/sms")
        self.GIT_BRANCH      = os.environ.get("GIT_BRANCH","main")
        self.BOT_SERVICE_NAME= os.environ.get("BOT_SERVICE_NAME","bot.service")
        self.GIT_REMOTE_URL  = os.environ.get("GIT_REMOTE_URL","")

        # Файл для кэша chat_id администратора
        self.ADMIN_CHAT_FILE = Path("/opt/sms/.admin_chat_id")

        # Telegram proxy bootstrap
        self.TG_PROXY_FILE = Path(os.environ.get("TG_PROXY_FILE", str(Path(self.GIT_REPO_DIR) / "proxy.txt")))
        self.TG_PROXY_TEST_TIMEOUT = float(os.environ.get("TG_PROXY_TEST_TIMEOUT", "10"))
        self.TG_PROXY_STABILITY_CHECKS = int(os.environ.get("TG_PROXY_STABILITY_CHECKS", "3"))
        self.TG_PROXY_STABILITY_DELAY = float(os.environ.get("TG_PROXY_STABILITY_DELAY", "0.5"))
        raw_proxy_urls = os.environ.get(
            "TG_PROXY_GITHUB_URLS",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt,"
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt,"
            "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
        )
        self.TG_PROXY_GITHUB_URLS = [x.strip() for x in raw_proxy_urls.split(",") if x.strip()]

        # Email transport
        self.EMAIL_ENABLED = os.environ.get("EMAIL_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
        self.EMAIL_TO = os.environ.get("EMAIL_TO", "")
        self.EMAIL_TO_LIST = [x.strip() for x in self.EMAIL_TO.split(",") if x.strip()]

        self.EMAIL_SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST", "")
        self.EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT", "465"))
        self.EMAIL_SMTP_USER = os.environ.get("EMAIL_SMTP_USER", "")
        self.EMAIL_SMTP_PASS = os.environ.get("EMAIL_SMTP_PASS", "")
        self.EMAIL_SMTP_SSL = os.environ.get("EMAIL_SMTP_SSL", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.EMAIL_SMTP_STARTTLS = os.environ.get("EMAIL_SMTP_STARTTLS", "0").strip().lower() in {"1", "true", "yes", "on"}

        self.EMAIL_IMAP_HOST = os.environ.get("EMAIL_IMAP_HOST", "")
        self.EMAIL_IMAP_PORT = int(os.environ.get("EMAIL_IMAP_PORT", "993"))
        self.EMAIL_IMAP_USER = os.environ.get("EMAIL_IMAP_USER", self.EMAIL_SMTP_USER)
        self.EMAIL_IMAP_PASS = os.environ.get("EMAIL_IMAP_PASS", self.EMAIL_SMTP_PASS)
        self.EMAIL_IMAP_MAILBOX = os.environ.get("EMAIL_IMAP_MAILBOX", "INBOX")
        self.EMAIL_ALLOWED_SENDERS = os.environ.get("EMAIL_ALLOWED_SENDERS", self.EMAIL_TO)
        self.EMAIL_ALLOWED_SENDERS_SET = {x.strip().lower() for x in self.EMAIL_ALLOWED_SENDERS.split(",") if x.strip()}
        self.EMAIL_COMMAND_HASH = os.environ.get("EMAIL_COMMAND_HASH", "").strip()
        self.EMAIL_POLL_INTERVAL = int(os.environ.get("EMAIL_POLL_INTERVAL", "30"))

        self.EVENT_STORE_SMS_URL = os.environ.get("EVENT_STORE_SMS_URL", "").strip()
        self.EVENT_STORE_CALL_URL = os.environ.get("EVENT_STORE_CALL_URL", "").strip()
        self.EVENT_STORE_AUTH_TOKEN = os.environ.get("EVENT_STORE_AUTH_TOKEN", "").strip()
        self.EVENT_STORE_TIMEOUT_SECONDS = float(os.environ.get("EVENT_STORE_TIMEOUT_SECONDS", "20"))


CONFIG = Config()

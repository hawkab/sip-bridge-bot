import os, sys
from pathlib import Path

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
load_env("/opt/sms/.env")

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
        self.WG_IFACE        = os.environ.get("WG_IFACE","wg0")

        self.GIT_REPO_DIR    = os.environ.get("GIT_REPO_DIR","/opt/sms")
        self.GIT_BRANCH      = os.environ.get("GIT_BRANCH","main")
        self.BOT_SERVICE_NAME= os.environ.get("BOT_SERVICE_NAME","bot.service")
        self.GIT_REMOTE_URL  = os.environ.get("GIT_REMOTE_URL","")

        # Файл для кэша chat_id администратора
        self.ADMIN_CHAT_FILE = Path("/opt/sms/.admin_chat_id")

CONFIG = Config()

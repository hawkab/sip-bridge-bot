#!/usr/bin/env python3
import asyncio, os, shlex, subprocess, urllib.parse, textwrap, traceback, re, sys
from typing import Optional, Dict, Tuple
from pathlib import Path
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, filters

# ================= ENV =================
def load_env(path):
    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            if not line.strip() or line.strip().startswith("#"): continue
            if "=" in line:
                k,v = line.split("=",1)
                os.environ.setdefault(k.strip(), v.strip())
load_env("/opt/sms/.env")

def must(k):
    v = os.environ.get(k, "")
    if not v:
        print(f"ENV {k} is required", file=sys.stderr)
        sys.exit(1)
    return v

BOT_TOKEN     = must("BOT_TOKEN")
ADMIN_LOGIN   = must("ADMIN_LOGIN")  # username без @
TG_HOST       = must("TG_HOST")
TG_PORT       = int(os.environ.get("TG_PORT","5038"))
TG_USER       = must("TG_USER")
TG_PASS       = must("TG_PASS")
TG_DEFAULT_SIM= int(os.environ.get("TG_DEFAULT_SIM","1"))
ASTERISK_CLI  = os.environ.get("ASTERISK_CLI","/usr/sbin/asterisk")
ASTERISK_LOG  = os.environ.get("ASTERISK_LOG","/var/log/asterisk/messages")
OS_LOG        = os.environ.get("OS_LOG","/var/log/syslog")
WG_IFACE      = os.environ.get("WG_IFACE","wg0")
GIT_REPO_DIR    = os.environ.get("GIT_REPO_DIR","/opt/sms")
GIT_BRANCH      = os.environ.get("GIT_BRANCH","main")
BOT_SERVICE_NAME= os.environ.get("BOT_SERVICE_NAME","bot.service")

# ================= STATE =================
pending_reply: Dict[int, Tuple[str,int]] = {}  # chat_id -> (phone, sim)
ADMIN_CHAT_FILE = Path("/opt/sms/.admin_chat_id")

def set_admin_chat_id(chat_id: int):
    try: ADMIN_CHAT_FILE.write_text(str(chat_id))
    except Exception: pass

def get_admin_chat_id() -> Optional[int]:
    try: return int(ADMIN_CHAT_FILE.read_text().strip())
    except Exception: return None

def _is_admin_user(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.username and u.username.lower() == ADMIN_LOGIN.lower())

def only_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_admin_user(update):
            return
        if update.effective_chat:
            set_admin_chat_id(update.effective_chat.id)
        return await func(update, context)
    return wrapper

# ================= UTILS =================
def git_pull(repo_dir: str, branch: str) -> str:
    logs = []
    def add(cmd): logs.append("$ "+" ".join(cmd) + "\n" + run_argv_loose(cmd))
    # Попытка fast-forward pull
    add(["git","-C",repo_dir,"rev-parse","--abbrev-ref","HEAD"])
    add(["git","-C",repo_dir,"fetch","--all","--prune"])
    add(["git","-C",repo_dir,"checkout",branch])
    add(["git","-C",repo_dir,"pull","--ff-only","origin",branch])
    return "\n\n".join(logs)

def _write_tmp(name: str, content: str) -> str:
    p = f"/tmp/{name}"
    Path(p).write_text(content, encoding="utf-8")
    return p

def get_journal(unit: str | None, n: int = 200) -> str:
    # Используем journalctl, если файлов нет
    if unit:
        return run(f"journalctl -u {unit} -n {n} --no-pager")
    return run(f"journalctl -n {n} --no-pager")

def get_os_logs(n: int = 200) -> str:
    return file_tail(OS_LOG, n) if os.path.exists(OS_LOG) else get_journal(None, n)

def get_asterisk_logs(n: int = 200) -> str:
    return file_tail(ASTERISK_LOG, n) if os.path.exists(ASTERISK_LOG) else get_journal("asterisk", n)


def render_resp(r: dict) -> str:
    line = f"{r.get('Response')} — {r.get('Message') or ''}".strip()
    outs = r.get("Outputs") or []
    if outs:
        line += "\n" + "\n".join(outs)
    return line

def run_argv(argv: list[str]) -> str:
    try:
        out = subprocess.check_output(argv, stderr=subprocess.STDOUT, timeout=10)
        return out.decode(errors="ignore").strip()
    except Exception as e:
        return f"ERR: {e}"
def run_argv_loose(argv: list[str]) -> str:
    # Не бросает исключение при exit!=0, возвращает stdout или код возврата
    p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=10)
    out = (p.stdout or "").strip()
    return out if out else f"exit={p.returncode}"
def get_asterisk_uptime_text() -> str:
    # Порядок попыток: rasterisk → asterisk
    tries = [
        (["/usr/sbin/rasterisk", "-x", "core show uptime"], True),
        (["rasterisk", "-x", "core show uptime"], True),
        (["/usr/sbin/asterisk", "-rx", "core show uptime"], False),
        (["asterisk", "-rx", "core show uptime"], False),
    ]
    last = ""
    for argv, _ in tries:
        out = run_argv_loose(argv)
        last = out
        if out and "Unable to connect to remote asterisk" not in out and "Unknown command" not in out:
            return out
    return last or "n/a"


def run(cmd: str) -> str:
    try:
        out = subprocess.check_output(shlex.split(cmd), stderr=subprocess.STDOUT, timeout=10)
        return out.decode(errors="ignore").strip()
    except Exception as e:
        return f"ERR: {e}"

def file_tail(path: str, n: int=200) -> str:
    if not os.path.exists(path): return f"{path} not found"
    try:
        out = subprocess.check_output(["tail","-n",str(n),path], timeout=10)
        return out.decode(errors="ignore")
    except Exception as e:
        return f"ERR: {e}"

def bytes2hr(n: int) -> str:
    for unit in ['B','KB','MB','GB','TB']:
        if n < 1024: return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"

def get_status() -> str:
    uptime = run("uptime -p")
    # temp
    temp = "n/a"
    try:
        t = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
        temp = f"{int(t)/1000:.1f} °C"
    except Exception:
        t = run("/usr/bin/vcgencmd measure_temp")
        if "temp=" in t: temp = t.replace("temp=","").strip()
    # disk
    st = os.statvfs("/")
    free = st.f_bavail * st.f_frsize
    total = st.f_blocks * st.f_frsize
    # mem
    mem_free = 0
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        kv = {k.strip():int(v.split()[0])*1024 for k,v in (line.split(":",1) for line in meminfo)}
        mem_free = kv.get("MemAvailable", kv.get("MemFree",0))
    except Exception: pass
    # vpn
    wg_active = run(f"systemctl is-active wg-quick@{WG_IFACE}")
    wg_show   = run("wg show")
    # asterisk
    ast_active = run("systemctl is-active asterisk")
    ast_uptime = get_asterisk_uptime_text()
    return textwrap.dedent(f"""
    🖥️ *Server status*
    Uptime: `{uptime}`
    Temp: `{temp}`
    Disk: `{bytes2hr(total-free)}/{bytes2hr(total)} used`
    RAM free: `{bytes2hr(mem_free)}`
    VPN ({WG_IFACE}): `{wg_active}`
    Asterisk: `{ast_active}`

    WireGuard:
    ```
    {wg_show}
    ```

    Asterisk uptime:
    ```
    {ast_uptime}
    ```
    """).strip()

def norm_sim(sim) -> int:
    s = str(sim or "").strip()
    m = re.search(r"\d+", s)
    return int(m.group()) if m else TG_DEFAULT_SIM

# ================= Yeastar TG SMS API (TCP) =================
class YeastarSMSClient:
    """
    AMI-подобный TCP API TG200.
    - Event: ReceivedSMS (входящие)
    - Action: smscommand (команды), типичный ответ: Response: Follows + Output: ... --END COMMAND--
    """
    def __init__(self, host: str, port: int, user: str, pwd: str):
        self.host, self.port, self.user, self.pwd = host, port, user, pwd
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.on_sms = None
        self.resp_queue: asyncio.Queue = asyncio.Queue()

    async def connect_forever(self):
        while True:
            try:
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                await self._login_and_drain()
                await asyncio.gather(self._read_loop(), self._keepalive())
            except Exception:
                await asyncio.sleep(3)

    async def _login_and_drain(self):
        # Логин
        self.writer.write(f"Action: Login\r\nUsername: {self.user}\r\nSecret: {self.pwd}\r\n\r\n".encode())
        await self.writer.drain()
        # Подождём и выбросим возможный "Authentication accepted", чтобы он не мешал /sms
        try:
            first = await asyncio.wait_for(self.resp_queue.get(), timeout=1.5)
            # просто игнорируем
        except asyncio.TimeoutError:
            pass

    async def _keepalive(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self._send_raw("Action: smscommand\r\ncommand: gsm show spans\r\n\r\n")
            except Exception:
                break

    async def _read_loop(self):
        buf = b""
        while True:
            chunk = await self.reader.read(4096)
            if not chunk:
                raise RuntimeError("Disconnected")
            buf += chunk
            while b"\r\n\r\n" in buf:
                block, buf = buf.split(b"\r\n\r\n", 1)
                self._handle_block(block.decode(errors="ignore"))

    @staticmethod
    def _parse_block(text: str) -> dict:
        # Собираем Response/Message + ВСЕ Output: (может быть много)
        kv = {}
        outputs = []
        for line in text.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip(); v = v.strip()
            if k.lower() == "output":
                outputs.append(v)
            else:
                kv[k] = v
        if outputs:
            kv["Outputs"] = outputs
        kv["_raw"] = text
        return kv

    def _handle_block(self, block: str):
        kv = self._parse_block(block)
        # входящие SMS — отдельное событие
        if kv.get("Event") == "ReceivedSMS":
            sender = kv.get("Sender","")
            sim    = kv.get("GsmPort","") or kv.get("Port","")
            when   = kv.get("Recvtime","") or kv.get("Time","")
            raw    = kv.get("Content","")
            try:
                text = urllib.parse.unquote(raw)
            except Exception:
                text = raw
            if self.on_sms:
                try:
                    self.on_sms(sender, sim, when, text)
                except Exception:
                    traceback.print_exc()
            return

        # Ответы на команды: с Response/Message/Output — кладём в очередь
        if any(k in kv for k in ("Response", "Message", "Outputs")):
            try:
                self.resp_queue.put_nowait(kv)
            except Exception:
                pass

    async def _send_raw(self, s: str):
        if not self.writer:
            raise RuntimeError("not connected")
        self.writer.write(s.encode())
        await self.writer.drain()

    async def send_command(self, command: str, wait: float = 3.0) -> dict:
        """
        Отправить команду и собрать полный ответ:
        - если Response: Follows — дособираем все Output: до '--END COMMAND--'
        """
        await self._send_raw(f"Action: smscommand\r\ncommand: {command}\r\n\r\n")
        try:
            first = await asyncio.wait_for(self.resp_queue.get(), timeout=wait)
        except asyncio.TimeoutError:
            return {"Response":"Timeout","Message":"No reply from TG"}

        # Если обычный ответ — вернём как есть
        if first.get("Response","").lower() != "follows":
            return first

        # Иначе дособираем Output: пока не встретим END
        outputs = list(first.get("Outputs", []))
        end_seen = any(line.strip().endswith("--END COMMAND--") for line in outputs)
        deadline = asyncio.get_event_loop().time() + wait
        while not end_seen and asyncio.get_event_loop().time() < deadline:
            try:
                more = await asyncio.wait_for(self.resp_queue.get(), timeout=0.8)
                outputs += more.get("Outputs", [])
                end_seen = any(line.strip().endswith("--END COMMAND--") for line in outputs)
            except asyncio.TimeoutError:
                break

        return {
            "Response": first.get("Response","Follows"),
            "Message": first.get("Message",""),
            "Outputs": outputs
        }


# ================= TELEGRAM =================
@only_admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/status — статус сервера\n"
        "/logs_os [N] — последние строки системного журнала\n"
        "/logs_sip [N] — последние строки журнала Asterisk\n"
        "/vpn_on /vpn_off — включить/выключить WireGuard\n"
        "/asterisk_restart — рестарт Asterisk\n"
        "/reboot — перезагрузка сервера\n"
        "/update — git pull + рестарт бота"
    )


@only_admin
async def cmd_sms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Формат: /sms <номер> <текст>")
    number = context.args[0]
    text = " ".join(context.args[1:])
    sim = TG_DEFAULT_SIM
    ys: YeastarSMSClient = context.bot_data["ys"]
    r1,r2,r3 = await ys.send_sms(number, text, sim)
    msg = "📤 SMS → {} (SIM {})\n1) {}\n2) {}\n3) {}".format(
        number, sim, render_resp(r1), render_resp(r2), render_resp(r3)
    )
    await update.message.reply_text(msg)

@only_admin
async def cmd_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    pr = pending_reply.get(chat_id)
    if not pr:
        return await update.message.reply_text("Нет адресата. Нажмите «Ответить» под SMS или используйте /sms.")
    number, sim = pr
    text = " ".join(context.args) if context.args else (update.message.text or "").replace("/reply","",1).strip()
    if not text:
        return await update.message.reply_text("Формат: /reply <текст>")
    ys: YeastarSMSClient = context.bot_data["ys"]
    r1,r2,r3 = await ys.send_sms(number, text, sim)
    msg = "📤 Ответ → {} (SIM {})\n1) {}\n2) {}\n3) {}".format(
        number, sim, render_resp(r1), render_resp(r2), render_resp(r3)
    )
    await update.message.reply_text(msg)

@only_admin
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data.startswith("reply:"):
        _, number, sim_raw = data.split(":",2)
        sim_i = norm_sim(sim_raw)
        chat_id = q.message.chat.id
        pending_reply[chat_id] = (number, sim_i)
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(f"Ответ адресован на {number} (SIM {sim_i}). Напишите /reply <текст>.")

@only_admin
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_markdown(get_status())

@only_admin
async def cmd_logs_os(update, context):
    n = int(context.args[0]) if (context.args and context.args[0].isdigit()) else 200
    txt = get_os_logs(n)
    # всегда отправляем файлом, чтобы избежать ограничений Markdown/длины
    fname = f"os_{time.strftime('%Y%m%d_%H%M%S')}.log"
    p = _write_tmp(fname, txt)
    with open(p, "rb") as f:
        await update.message.reply_document(document=f, filename=fname)

@only_admin
async def cmd_logs_sip(update, context):
    n = int(context.args[0]) if (context.args and context.args[0].isdigit()) else 200
    txt = get_asterisk_logs(n)
    fname = f"sip_{time.strftime('%Y%m%d_%H%M%S')}.log"
    p = _write_tmp(fname, txt)
    with open(p, "rb") as f:
        await update.message.reply_document(document=f, filename=fname)

@only_admin
async def cmd_vpn_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    out = run(f"sudo systemctl start wg-quick@{WG_IFACE}")
    await update.message.reply_text(f"VPN ON: {out}")

@only_admin
async def cmd_vpn_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    out = run(f"sudo systemctl stop wg-quick@{WG_IFACE}")
    await update.message.reply_text(f"VPN OFF: {out}")

@only_admin
async def cmd_ast_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    out = run("sudo systemctl restart asterisk")
    await update.message.reply_text(f"Asterisk restart: {out}")

@only_admin
async def cmd_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Перезагрузить", callback_data="reboot:yes"),
                                InlineKeyboardButton("Отмена", callback_data="reboot:no")]])
    await update.message.reply_text("Подтвердите перезагрузку:", reply_markup=kb)

@only_admin
async def on_reboot_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "reboot:yes":
        await q.edit_message_text("Перезагружаюсь…")
        subprocess.Popen(["sudo","/sbin/reboot"])
    else:
        await q.edit_message_text("Отменено.")

@only_admin
async def ys_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ys: YeastarSMSClient = context.bot_data["ys"]
    r = await ys.send_command("gsm show spans")
    await update.message.reply_text(f"{r.get('Response')} — {r.get('Message')}")

@only_admin
async def ys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Формат: /ys_cmd <raw command>")
    ys: YeastarSMSClient = context.bot_data["ys"]
    cmd = " ".join(context.args)
    r = await ys.send_command(cmd, wait=3.0)
    await update.message.reply_text(
        "Ответ TG:\n" + "\n".join(f"{k}: {v}" for k,v in r.items()) or "нет данных"
    )

@only_admin
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⬇️ Обновляюсь из Git и перезапускаю сервис…")
    log = git_pull(GIT_REPO_DIR, GIT_BRANCH)
    # отправим лог отдельным файлом (может быть длинный)
    from pathlib import Path
    import time
    fname = f"update_{time.strftime('%Y%m%d_%H%M%S')}.log"
    p = f"/tmp/{fname}"
    Path(p).write_text(log, encoding="utf-8")
    with open(p,"rb") as f:
        await update.message.reply_document(document=f, filename=fname, caption="Git pull log")

    # Перезапуск бота
    out = run_argv_loose(["sudo","systemctl","restart",BOT_SERVICE_NAME])
    # Ответим перед тем, как процесс завершится (на всякий случай)
    await update.message.reply_text(f"🔁 systemctl restart {BOT_SERVICE_NAME}\n{out}")


# ======== INCOMING SMS -> TELEGRAM ========
async def start_ys_reader(app):
    ys: YeastarSMSClient = app.bot_data["ys"]
    async def sms_cb(sender, sim, when, text):
        admin_chat = get_admin_chat_id()
        if not admin_chat: return
        sim_i = norm_sim(sim)
        pending_reply[admin_chat] = (sender, sim_i)
        msg = f"📩 *SMS*\nОт: `{sender}`\nSIM: `{sim_i}`\nВремя: `{when}`\n\n{text}"
        await app.bot.send_message(chat_id=admin_chat, text=msg, parse_mode="Markdown")
    ys.on_sms = lambda s,p,w,t: asyncio.create_task(sms_cb(s,p,w,t))
    asyncio.create_task(ys.connect_forever())

# ================= MAIN =================
def main():
    ys = YeastarSMSClient(TG_HOST, TG_PORT, TG_USER, TG_PASS)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.bot_data["ys"] = ys

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logs_os", cmd_logs_os))
    app.add_handler(CommandHandler("logs_sip", cmd_logs_sip))
    app.add_handler(CommandHandler("vpn_on", cmd_vpn_on))
    app.add_handler(CommandHandler("vpn_off", cmd_vpn_off))
    app.add_handler(CommandHandler("asterisk_restart", cmd_ast_restart))
    app.add_handler(CommandHandler("reboot", cmd_reboot))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CallbackQueryHandler(on_reboot_button, pattern=r"^reboot:(yes|no)$"))

    app.post_init = start_ys_reader
    app.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

if __name__ == "__main__":
    main()

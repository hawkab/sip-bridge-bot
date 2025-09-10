import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes, Application
from config import CONFIG
from auth import only_admin, get_admin_chat_id
from utils import (
    get_status, get_os_logs, get_asterisk_logs, _write_tmp,
    norm_sim, git_pull, run_argv_loose, get_app_version_text
)


from ys_client import YeastarSMSClient

# ======= Commands =======

@only_admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Доступные команды:\n"
        "/status — статус сервера\n"
        "/logs_os [N] — последние строки системного журнала\n"
        "/logs_sip [N] — последние строки журнала Asterisk\n"
        "/vpn_on /vpn_off — включить/выключить WireGuard\n"
        "/asterisk_restart — рестарт Asterisk\n"
        "/reboot — перезагрузка сервера\n"
        "/update — git pull + рестарт бота\n"
        "/ys_ping\n"
        "/ys_cmd <raw>"
    )

@only_admin
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_markdown(get_status())

@only_admin
async def cmd_logs_os(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = int(context.args[0]) if (context.args and context.args[0].isdigit()) else 200
    txt = get_os_logs(n)
    fname = f"os_{time.strftime('%Y%m%d_%H%M%S')}.log"
    p = _write_tmp(fname, txt)
    with open(p, "rb") as f:
        await update.message.reply_document(document=f, filename=fname)

@only_admin
async def cmd_logs_sip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = int(context.args[0]) if (context.args and context.args[0].isdigit()) else 200
    txt = get_asterisk_logs(n)
    fname = f"sip_{time.strftime('%Y%m%d_%H%M%S')}.log"
    p = _write_tmp(fname, txt)
    with open(p, "rb") as f:
        await update.message.reply_document(document=f, filename=fname)

@only_admin
async def cmd_vpn_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils import run
    out = run(f"sudo systemctl start wg-quick@{CONFIG.WG_IFACE}")
    await update.message.reply_text(f"VPN ON: {out}")

@only_admin
async def cmd_vpn_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils import run
    out = run(f"sudo systemctl stop wg-quick@{CONFIG.WG_IFACE}")
    await update.message.reply_text(f"VPN OFF: {out}")

@only_admin
async def cmd_ast_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from utils import run
    out = run("sudo systemctl restart asterisk")
    await update.message.reply_text(f"Asterisk restart: {out}")

@only_admin
async def cmd_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Перезагрузить", callback_data="reboot:yes"),
         InlineKeyboardButton("Отмена", callback_data="reboot:no")]
    ])
    await update.message.reply_text("Подтвердите перезагрузку:", reply_markup=kb)

@only_admin
async def on_reboot_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import subprocess
    q = update.callback_query
    await q.answer()
    if q.data == "reboot:yes":
        await q.edit_message_text("Перезагружаюсь…")
        subprocess.Popen(["sudo","/sbin/reboot"])
    else:
        await q.edit_message_text("Отменено.")

# ---------- Yeastar raw / sms ----------
@only_admin
async def ys_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ys: YeastarSMSClient = context.bot_data["ys"]
    r = await ys.send_command("gsm show spans")
    await update.message.reply_text(f"{r.get('Response')} — {r}")

@only_admin
async def ys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Формат: /ys_cmd <raw command>")
    ys: YeastarSMSClient = context.bot_data["ys"]
    cmd = " ".join(context.args)
    r = await ys.send_command(cmd, wait=3.0)
    lines = [f"{k}: {v}" for k,v in r.items()]
    await update.message.reply_text("Ответ TG:\n" + ("\n".join(lines) if lines else "нет данных"))

@only_admin
async def cmd_sms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Формат: /sms <номер> <текст>")
    number = context.args[0]
    text = " ".join(context.args[1:])
    sim = CONFIG.TG_DEFAULT_SIM
    ys: YeastarSMSClient = context.bot_data["ys"]
    r1,r2,r3 = await ys.send_sms(number, text, sim)
    msg = "📤 SMS → {} (SIM {})\n1) {}\n2) {}\n3) {}".format(
        number, sim, render_resp(r1), render_resp(r2), render_resp(r3)
    )
    await update.message.reply_text(msg)

# ---------- Git update ----------
@only_admin
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⬇️ Обновляюсь из Git и перезапускаю сервис…")
    log = git_pull(CONFIG.GIT_REPO_DIR, CONFIG.GIT_BRANCH)
    fname = f"update_{time.strftime('%Y%m%d_%H%M%S')}.log"
    p = _write_tmp(fname, log)
    with open(p,"rb") as f:
        await update.message.reply_document(document=f, filename=fname, caption="Git pull log")

    out = run_argv_loose(["sudo","-n","systemctl","restart",CONFIG.BOT_SERVICE_NAME])
    await update.message.reply_text(f"🔁 systemctl restart {CONFIG.BOT_SERVICE_NAME}\n{out}")

# ======== Post-init: запуск reader'а и уведомление о старте ========
async def on_post_init(app: Application):
    # запустить TG200 reader
    await start_ys_reader(app)

    # уведомление администратору
    try:
        admin_chat = get_admin_chat_id()
        if admin_chat:
            ver = get_app_version_text()
            from time import strftime
            text = (
                f"✅ Бот запущен ({strftime('%Y-%m-%d %H:%M:%S')})\n\n"
                f"Версия (Git):\n```\n{ver}\n```"
            )
            await app.bot.send_message(chat_id=admin_chat, text=text, parse_mode="Markdown")
    except Exception:
        # молча игнорируем, чтобы не мешать запуску
        pass


# ======== Incoming SMS -> Telegram ========
async def start_ys_reader(app: Application):
    ys: YeastarSMSClient = app.bot_data["ys"]

    async def sms_cb(sender, sim, when, text):
        admin_chat = get_admin_chat_id()
        if not admin_chat: return
        sim_i = norm_sim(sim)
        msg = f"📩 *SMS*\nОт: `{sender}`\nSIM: `{sim_i}`\nВремя: `{when}`\n\n{text}"
        await app.bot.send_message(chat_id=admin_chat, text=msg, parse_mode="Markdown")

    ys.on_sms = lambda s,p,w,t: app.create_task(sms_cb(s,p,w,t))
    app.create_task(ys.connect_forever())

def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logs_os", cmd_logs_os))
    app.add_handler(CommandHandler("logs_sip", cmd_logs_sip))
    app.add_handler(CommandHandler("vpn_on", cmd_vpn_on))
    app.add_handler(CommandHandler("vpn_off", cmd_vpn_off))
    app.add_handler(CommandHandler("asterisk_restart", cmd_ast_restart))
    app.add_handler(CommandHandler("reboot", cmd_reboot))
    app.add_handler(CommandHandler("update", cmd_update))

    # SMS tools
    app.add_handler(CommandHandler("ys_ping", ys_ping))
    app.add_handler(CommandHandler("ys_cmd", ys_cmd))
    app.add_handler(CallbackQueryHandler(on_reboot_button, pattern=r"^reboot:(yes|no)$"))

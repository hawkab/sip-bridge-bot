import time
from pathlib import Path
from datetime import datetime
import asyncio

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes, Application
from telegram.error import TimedOut, NetworkError, RetryAfter

from config import CONFIG
from auth import only_admin, get_admin_chat_id
from utils import (
    get_status, get_os_logs, get_asterisk_logs, _write_tmp,
    norm_sim, git_pull, run_argv_loose, get_app_version_text
)
from ys_client import YeastarSMSClient

# Если Telegram временно недоступен, складываем сообщения сюда, чтобы ничего не пропало
FAILED_TG_QUEUE = Path("/opt/sms/failed_telegram.queue")


# ---------- Telegram safe send ----------
async def send_tg_safe(
    app: Application,
    chat_id: int,
    text: str,
    parse_mode: str | None = None,
    reply_markup=None,
) -> bool:
    """
    Надёжная отправка в Telegram:
    - несколько ретраев на TimedOut/NetworkError
    - корректная пауза на RetryAfter (rate limit)
    - если всё плохо — складываем текст в /opt/sms/failed_telegram.queue
    """
    delays = [0, 1, 2, 5, 10, 20]
    last_exc = None

    for d in delays:
        if d:
            await asyncio.sleep(d)
        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
            return True
        except RetryAfter as e:
            last_exc = e
            retry = int(getattr(e, "retry_after", 5))
            await asyncio.sleep(max(1, retry))
        except (TimedOut, NetworkError) as e:
            last_exc = e

    # Не смогли отправить вообще: сохраняем в файл-очередь (чтобы не потерять SMS)
    try:
        FAILED_TG_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        with FAILED_TG_QUEUE.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now().isoformat(timespec='seconds')} ---\n")
            f.write(f"chat_id={chat_id}\n")
            if last_exc:
                f.write(f"last_error={type(last_exc).__name__}: {last_exc}\n")
            f.write(text)
            f.write("\n")
    except Exception:
        # даже если запись не удалась — не валим процесс
        pass

    return False


# ======= Commands =======

@only_admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Доступные команды:\n"
        "/status — статус сервера\n"
        "/logs_os [N] — последние строки системного журнала\n"
        "/logs_sip [N] — последние строки журнала Asterisk\n"
        "/asterisk_restart — рестарт Asterisk\n"
        "/reboot — перезагрузка сервера\n"
        "/update — git pull + рестарт бота\n"
        "/ys_ping\n"
        "/ys_cmd <raw>"
    )

@only_admin
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # get_status() у тебя уже возвращает форматированный Markdown
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
        subprocess.Popen(["sudo", "/sbin/reboot"])
    else:
        await q.edit_message_text("Отменено.")


# ---------- Yeastar raw ----------
@only_admin
async def ys_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ys: YeastarSMSClient = context.bot_data["ys"]
    r = await ys.send_command("gsm show spans")
    await update.message.reply_text(f"{r}")

@only_admin
async def ys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Формат: /ys_cmd <raw command>")
    ys: YeastarSMSClient = context.bot_data["ys"]
    cmd = " ".join(context.args)
    r = await ys.send_command(cmd, wait=3.0)
    lines = [f"{k}: {v}" for k, v in r.items()]
    await update.message.reply_text("Ответ TG:\n" + ("\n".join(lines) if lines else "нет данных"))


# ---------- Git update ----------
@only_admin
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⬇️ Обновляюсь из Git и перезапускаю сервис…")

    log = git_pull(CONFIG.GIT_REPO_DIR, CONFIG.GIT_BRANCH)
    fname = f"update_{time.strftime('%Y%m%d_%H%M%S')}.log"
    p = _write_tmp(fname, log)
    with open(p, "rb") as f:
        await update.message.reply_document(document=f, filename=fname, caption="Git pull log")

    out = run_argv_loose(["sudo", "-n", "systemctl", "restart", CONFIG.BOT_SERVICE_NAME])
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
            text = (
                f"✅ Бот запущен ({time.strftime('%Y-%m-%d %H:%M:%S')})\n\n"
                f"Версия (Git):\n```\n{ver}\n```"
            )
            await send_tg_safe(app, admin_chat, text, parse_mode="Markdown")
    except Exception:
        # не мешаем запуску, даже если телега/сеть умерла
        pass


# ======== Incoming SMS -> Telegram ========
async def start_ys_reader(app: Application):
    ys: YeastarSMSClient = app.bot_data["ys"]

    async def sms_cb(sender, sim, when, text):
        admin_chat = get_admin_chat_id()
        if not admin_chat:
            return

        sim_i = norm_sim(sim)

        # Если SMS уже “нормализована” (ты сделал unquote_plus + сборку частей),
        # то тут просто отправляем.
        msg = (
            f"📩 *SMS*\n"
            f"От: `{sender}`\n"
            f"SIM: `{sim_i}`\n"
            f"Время: `{when}`\n\n"
            f"{text}"
        )

        # Важно: через send_tg_safe, иначе таймаут Telegram роняет таску
        await send_tg_safe(app, admin_chat, msg, parse_mode="Markdown")

    ys.on_sms = lambda s, p, w, t: app.create_task(sms_cb(s, p, w, t))
    app.create_task(ys.connect_forever())


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logs_os", cmd_logs_os))
    app.add_handler(CommandHandler("logs_sip", cmd_logs_sip))
    app.add_handler(CommandHandler("asterisk_restart", cmd_ast_restart))
    app.add_handler(CommandHandler("reboot", cmd_reboot))
    app.add_handler(CommandHandler("update", cmd_update))

    # Yeastar tools
    app.add_handler(CommandHandler("ys_ping", ys_ping))
    app.add_handler(CommandHandler("ys_cmd", ys_cmd))

    app.add_handler(CallbackQueryHandler(on_reboot_button, pattern=r"^reboot:(yes|no)$"))

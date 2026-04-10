import time
import os
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
from cdr_monitor import CDRMonitor

# Если Telegram временно недоступен, складываем сообщения сюда
FAILED_TG_QUEUE = Path("/opt/sms/failed_telegram.queue")


# ---------- Telegram safe send ----------
async def send_tg_safe(
        app: Application,
        chat_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup=None,
) -> bool:
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
        pass
    return False


# ---------- Форматирование одного звонка (старый стиль) ----------
def format_single_cdr(row: dict) -> str:
    """Возвращает сообщение для одного звонка в старом, подробном формате."""
    lines = []
    if row.get("src"):
        lines.append(f"От: `{row['src']}`")
    if row.get("dst"):
        lines.append(f"Кому: `{row['dst']}`")
    if row.get("start"):
        lines.append(f"Начало: `{row['start']}`")
    if row.get("answer") and row['answer']:
        lines.append(f"Ответ: `{row['answer']}`")
    if row.get("end"):
        lines.append(f"Конец: `{row['end']}`")
    if row.get("duration"):
        lines.append(f"Длительность (сек): `{row['duration']}`")
    if row.get("billsec"):
        lines.append(f"Разговор (сек): `{row['billsec']}`")
    if row.get("disposition"):
        disp = row['disposition']
        if disp == "ANSWERED":
            disp = "Отвечен"
        elif disp == "NO ANSWER":
            disp = "Нет ответа"
        elif disp == "BUSY":
            disp = "Занято"
        elif disp == "FAILED":
            disp = "Ошибка"
        lines.append(f"Статус: `{disp}`")
    if not lines:
        return ""
    return "📞 *Звонок (CDR)*\n" + "\n".join(lines)


# ---------- Форматирование группы звонков (новый стиль) ----------
def format_cdr_group(rows: list) -> str:
    """
    Если в группе одна запись – используется старый формат.
    Если несколько – выводится групповое сообщение с детализацией попыток,
    где время окончания показывается только как часы:минуты:секунды.
    """
    if not rows:
        return ""

    # Одиночный звонок – старый формат
    if len(rows) == 1:
        return format_single_cdr(rows[0])

    # Группа из нескольких попыток
    first = rows[0]
    src = first.get('src', '?')
    dst = first.get('dst', '?')
    context = first.get('dcontext', '')

    if 'inbound-gsm' in context:
        direction = "Входящий с GSM"
        caller = src
        callee = dst
    else:
        direction = "Исходящий на GSM"
        caller = src
        callee = dst

    lines = [f"📞 *{direction}*", f"От: `{caller}` → `{callee}`", ""]

    for idx, row in enumerate(rows, 1):
        start = row.get('start', '')
        end = row.get('end', '')
        duration = row.get('duration', '0')
        disposition = row.get('disposition', '')

        # Статус по-русски
        if disposition == "ANSWERED":
            disp = "Отвечен"
        elif disposition == "NO ANSWER":
            disp = "Не отвечено"
        elif disposition == "BUSY":
            disp = "Занято"
        elif disposition == "FAILED":
            disp = "Ошибка"
        else:
            disp = disposition

        # Преобразуем время начала (только HH:MM:SS)
        start_time = ""
        if start:
            try:
                dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
                start_time = dt.strftime("%H:%M:%S")
            except:
                start_time = start

        # Преобразуем время окончания (только HH:MM:SS)
        end_time = ""
        if end:
            try:
                dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
                end_time = dt.strftime("%H:%M:%S")
            except:
                end_time = end

        line = f"{start_time} - {end_time} ({duration}с) {disp}"
        lines.append(line)

    # Итоговый статус группы
    final_status = "Отвечен" if any(r.get('disposition') == "ANSWERED" for r in rows) else disp
    lines.append("")
    lines.append(f"Итог: {final_status} (попыток: {len(rows)})")

    return "\n".join(lines)


# ======== Команды ========
@only_admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Доступные команды:\n"
        "/status — статус сервера\n"
        "/logs_os [N] — последние строки системного журнала\n"
        "/logs_sip [N] — последние строки журнала Asterisk\n"
        "/cdr_csv — скачать файл CDR Asterisk Master.csv\n"
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
async def cmd_cdr_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cdr_file = "/var/log/asterisk/cdr-csv/Master.csv"
    if not os.path.exists(cdr_file):
        await update.message.reply_text(f"Файл не найден: {cdr_file}")
        return

    with open(cdr_file, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=f"Master_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        )


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


# ======== Мониторинг CDR Asterisk ========
async def start_cdr_monitor(app: Application):
    admin_chat = get_admin_chat_id()
    if not admin_chat:
        return

    async def cdr_group_callback(group: list):
        msg = format_cdr_group(group)
        if not msg:
            return

        # Поиск отвеченной записи в группе
        answered_record = None
        for record in group:
            if record.get('disposition') == "ANSWERED":
                answered_record = record
                break

        # Если есть отвеченная запись, пытаемся отправить файл
        if answered_record:
            uniqueid = answered_record.get('uniqueid')
            if uniqueid:
                record_path = f"/var/spool/asterisk/monitor/{uniqueid}.wav"
                # Проверяем, что файл существует и имеет размер больше 44 байт (содержит аудио)
                if os.path.exists(record_path) and os.path.getsize(record_path) > 44:
                    with open(record_path, 'rb') as f:
                        await app.bot.send_document(
                            chat_id=admin_chat,
                            document=f,
                            filename=f"{uniqueid}.wav",
                            caption=msg,
                            parse_mode="Markdown"
                        )
                    return

        # Если нет отвеченной записи или файл пустой/отсутствует, отправляем только текст
        await send_tg_safe(app, admin_chat, msg, parse_mode="Markdown")

    cdr_file = "/var/log/asterisk/cdr-csv/Master.csv"
    monitor = CDRMonitor(cdr_file, cdr_group_callback, check_interval=5.0, group_timeout=30.0)
    asyncio.create_task(monitor.start())


# ======== Incoming SMS -> Telegram ========
async def start_ys_reader(app: Application):
    ys: YeastarSMSClient = app.bot_data["ys"]

    async def sms_cb(sender, sim, when, text):
        admin_chat = get_admin_chat_id()
        if not admin_chat:
            return
        sim_i = norm_sim(sim)
        msg = (
            f"📩 *SMS*\n"
            f"От: `{sender}`\n"
            f"SIM: `{sim_i}`\n"
            f"Время: `{when}`\n\n"
            f"{text}"
        )
        await send_tg_safe(app, admin_chat, msg, parse_mode="Markdown")

    ys.on_sms = lambda s, p, w, t: asyncio.create_task(sms_cb(s, p, w, t))
    asyncio.create_task(ys.connect_forever())


# ======== Post-init ========
async def on_post_init(app: Application):
    await start_ys_reader(app)
    await start_cdr_monitor(app)

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
        pass


def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logs_os", cmd_logs_os))
    app.add_handler(CommandHandler("logs_sip", cmd_logs_sip))
    app.add_handler(CommandHandler("cdr_csv", cmd_cdr_csv))
    app.add_handler(CommandHandler("asterisk_restart", cmd_ast_restart))
    app.add_handler(CommandHandler("reboot", cmd_reboot))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("ys_ping", ys_ping))
    app.add_handler(CommandHandler("ys_cmd", ys_cmd))
    app.add_handler(CallbackQueryHandler(on_reboot_button, pattern=r"^reboot:(yes|no)$"))

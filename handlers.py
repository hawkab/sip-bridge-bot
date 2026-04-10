import asyncio
import os
import time
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from auth import get_admin_chat_id, only_admin
from cdr_monitor import CDRMonitor
from command_service import execute_post_action
from delivery import DeliveryHub


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
    if not rows:
        return ""

    if len(rows) == 1:
        return format_single_cdr(rows[0])

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

    for row in rows:
        start = row.get('start', '')
        end = row.get('end', '')
        duration = row.get('duration', '0')
        disposition = row.get('disposition', '')

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

        start_time = ""
        if start:
            try:
                dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
                start_time = dt.strftime("%H:%M:%S")
            except Exception:
                start_time = start

        end_time = ""
        if end:
            try:
                dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
                end_time = dt.strftime("%H:%M:%S")
            except Exception:
                end_time = end

        line = f"{start_time} - {end_time} ({duration}с) {disp}"
        lines.append(line)

    final_status = "Отвечен" if any(r.get('disposition') == "ANSWERED" for r in rows) else disp
    lines.append("")
    lines.append(f"Итог: {final_status} (попыток: {len(rows)})")

    return "\n".join(lines)


async def _run_shared_command(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_command: str):
    result = await context.bot_data["command_service"].execute(raw_command)
    await context.bot_data["delivery"].reply_telegram(update.effective_chat.id, result)
    execute_post_action(result.post_action)


@only_admin
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_shared_command(update, context, "/start")


@only_admin
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_shared_command(update, context, "/status")


@only_admin
async def cmd_logs_os(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = context.args[0] if (context.args and context.args[0]) else ""
    await _run_shared_command(update, context, f"/logs_os {arg}".strip())


@only_admin
async def cmd_logs_sip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = context.args[0] if (context.args and context.args[0]) else ""
    await _run_shared_command(update, context, f"/logs_sip {arg}".strip())


@only_admin
async def cmd_cdr_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_shared_command(update, context, "/cdr_csv")


@only_admin
async def cmd_ast_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_shared_command(update, context, "/asterisk_restart")


@only_admin
async def cmd_reboot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Перезагрузить", callback_data="reboot:yes"),
         InlineKeyboardButton("Отмена", callback_data="reboot:no")]
    ])
    await update.message.reply_text("Подтвердите перезагрузку:", reply_markup=kb)


@only_admin
async def on_reboot_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "reboot:yes":
        result = await context.bot_data["command_service"].execute("/reboot yes")
        await q.edit_message_text("Перезагружаюсь…")
        execute_post_action(result.post_action)
    else:
        await q.edit_message_text("Отменено.")


@only_admin
async def ys_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_shared_command(update, context, "/ys_ping")


@only_admin
async def ys_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tail = " ".join(context.args) if context.args else ""
    await _run_shared_command(update, context, f"/ys_cmd {tail}".strip())


@only_admin
async def cmd_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _run_shared_command(update, context, "/update")


async def start_cdr_monitor(delivery: DeliveryHub):
    async def cdr_group_callback(group: list):
        msg = format_cdr_group(group)
        if not msg:
            return

        answered_record = None
        for record in group:
            if record.get('disposition') == "ANSWERED":
                answered_record = record
                break

        attachment_path = None
        attachment_name = None
        if answered_record:
            uniqueid = answered_record.get('uniqueid')
            if uniqueid:
                record_path = f"/var/spool/asterisk/monitor/{uniqueid}.wav"
                if os.path.exists(record_path) and os.path.getsize(record_path) > 44:
                    attachment_path = record_path
                    attachment_name = f"{uniqueid}.wav"

        await delivery.notify_event(
            subject="SipBridgeBot: CDR событие",
            text=msg,
            attachment_path=attachment_path,
            attachment_name=attachment_name,
            parse_mode="Markdown",
        )

    cdr_file = "/var/log/asterisk/cdr-csv/Master.csv"
    monitor = CDRMonitor(cdr_file, cdr_group_callback, check_interval=5.0, group_timeout=30.0)
    asyncio.create_task(monitor.start())


async def start_ys_reader(ys, delivery: DeliveryHub):
    async def sms_cb(sender, sim, when, text):
        msg = (
            f"📩 *SMS*\n"
            f"От: `{sender}`\n"
            f"SIM: `{sim}`\n"
            f"Время: `{when}`\n\n"
            f"{text}"
        )
        await delivery.notify_event(
            subject=f"SipBridgeBot: SMS от {sender}",
            text=msg,
            parse_mode="Markdown",
        )

    ys.on_sms = lambda s, p, w, t: asyncio.create_task(sms_cb(s, p, w, t))
    asyncio.create_task(ys.connect_forever())


async def send_startup_notification(delivery: DeliveryHub, app_version_text: str):
    text = (
        f"✅ Бот запущен ({time.strftime('%Y-%m-%d %H:%M:%S')})\n\n"
        f"Версия (Git):\n```\n{app_version_text}\n```"
    )
    await delivery.notify_event(
        subject="SipBridgeBot: запуск",
        text=text,
        parse_mode="Markdown",
    )


async def on_post_init(app: Application):
    delivery = app.bot_data.get("delivery")
    if delivery:
        delivery.set_telegram_app(app)
        await delivery.flush_pending_telegram_messages()


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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from integrations.telegram.auth import only_admin
from services.command_service import execute_post_action



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

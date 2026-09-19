"""Admin-only voice call composition, explicit confirmation and scheduling."""
from datetime import datetime, timezone
import re
import secrets
import time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters
from integrations.telegram.auth import only_admin

MOSCOW = ZoneInfo('Europe/Moscow')
HELP = ('Отправьте /voicecall +79991234567 для звонка сейчас или\n'
        '/voicecall +79991234567 2026-12-31 18:30 для запланированного звонка.\n'
        'Время — московское. Затем пришлите голосовое сообщение или аудиофайл '
        '(до 120 секунд и 2 МБ) и подтвердите вызов.\n'
        '/voicecalls — очередь и результаты\n/cancelcall ID — отменить ожидающий вызов\n'
        '/cancelvoice — сбросить черновик')


def client(context):
    result = getattr(context.bot_data['command_service'], 'voice_outbox', None)
    if not result or not result.enabled:
        raise RuntimeError('Голосовые вызовы не настроены.')
    return result


def parse_destination(args):
    if len(args) not in (1, 3) or not re.fullmatch(r'\+[1-9][0-9]{6,14}', args[0]):
        raise ValueError(HELP)
    when = ''
    if len(args) == 3:
        try:
            date = datetime.strptime(' '.join(args[1:]), '%Y-%m-%d %H:%M').replace(tzinfo=MOSCOW)
        except ValueError:
            raise ValueError('Формат времени: ГГГГ-ММ-ДД ЧЧ:ММ (Москва).') from None
        delta = date.timestamp()-time.time()
        if not 0 < delta <= 90*86400:
            raise ValueError('Выберите будущее время в пределах 90 дней.')
        when = date.astimezone(timezone.utc).isoformat(timespec='seconds')
    return args[0], when


def display_time(when):
    return datetime.fromisoformat(when.replace('Z', '+00:00')).astimezone(MOSCOW).strftime('%d.%m.%Y %H:%M МСК') if when else 'сейчас'


@only_admin
async def voicecall(update, context):
    context.user_data.pop('voice_call', None)
    try:
        client(context)
        number, when = parse_destination(context.args)
        context.user_data['voice_call'] = {'number': number, 'scheduled_at': when, 'nonce': secrets.token_hex(12),
                                          'expires': time.time()+900, 'chat_id': update.effective_chat.id}
        await update.effective_message.reply_text(f'Кому: {number}\nКогда: {display_time(when)}\n'
            'Пришлите голосовое сообщение или аудиофайл до 120 секунд / 2 МБ. Перед звонком появится кнопка подтверждения.')
    except (ValueError, RuntimeError) as error:
        await update.effective_message.reply_text(str(error))


@only_admin
async def receive_voice(update, context):
    draft = context.user_data.get('voice_call')
    if not draft or draft['expires'] < time.time() or draft['chat_id'] != update.effective_chat.id:
        await update.effective_message.reply_text(HELP)
        return
    audio = update.effective_message.voice or update.effective_message.audio
    if not audio or (audio.file_size or 0) > 2097152 or audio.duration > 120:
        await update.effective_message.reply_text('Нужна запись до 120 секунд и 2 МБ.')
        return
    draft.update(file_id=audio.file_id, nonce=secrets.token_hex(12), expires=time.time()+900)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton('Позвонить' if not draft['scheduled_at'] else 'Запланировать',
        callback_data='voice:send:'+draft['nonce']), InlineKeyboardButton('Отмена', callback_data='voice:cancel:'+draft['nonce'])]])
    await update.effective_message.reply_text(f"Запись: {audio.duration} сек.\nКому: {draft['number']}\nКогда: {display_time(draft['scheduled_at'])}", reply_markup=keyboard)


@only_admin
async def confirm_voice(update, context):
    query = update.callback_query
    await query.answer()
    _, action, nonce = query.data.split(':')
    draft = context.user_data.get('voice_call')
    if not draft or draft['nonce'] != nonce or draft['expires'] < time.time() or draft['chat_id'] != update.effective_chat.id:
        await query.edit_message_text('Черновик устарел. Начните с /voicecall.')
        return
    if action == 'cancel':
        context.user_data.pop('voice_call', None)
        await query.edit_message_text('Черновик отменён.')
        return
    try:
        api = client(context)
        remote_file = await context.bot.get_file(draft['file_id'])
        if (remote_file.file_size or 0) > 2097152:
            raise RuntimeError('Запись превышает 2 МБ.')
        audio = bytes(await remote_file.download_as_bytearray())
        response = await api.request('enqueue', audio=audio, number=draft['number'],
            scheduled_at=draft['scheduled_at'], request_key='telegram-voice:'+nonce)
        job = response['job']
    except Exception:
        # Keep the same request key: pressing the existing button again is safe
        # even if the server accepted the first request but its response was lost.
        await update.effective_message.reply_text('Не удалось подтвердить вызов. Проверьте /voicecalls; '
            'можно повторно нажать ту же кнопку без создания дубля. Если время прошло, создайте новый черновик.')
        return
    context.user_data.pop('voice_call', None)
    await query.edit_message_text(f"Вызов {job['id']}\nКому: {job['number']}\nКогда: {display_time(job['scheduled_at'])}\n"
        f"{job['message']}\nОтмена: /cancelcall {job['id']}")


@only_admin
async def voicecalls(update, context):
    try:
        response = await client(context).request()
        lines = [f"{j['id']}\n{j['number']} · {display_time(j['scheduled_at'])}\n{j['message']}" for j in response['jobs'][:10]]
        await update.effective_message.reply_text('\n\n'.join(lines) or 'Очередь пуста.\n'+HELP)
    except RuntimeError as error:
        await update.effective_message.reply_text(str(error))


@only_admin
async def cancelcall(update, context):
    try:
        if len(context.args) != 1 or not re.fullmatch('[a-f0-9]{32}', context.args[0]):
            raise RuntimeError('Формат: /cancelcall ID (идентификатор из /voicecalls).')
        result = await client(context).request('cancel', id=context.args[0])
        await update.effective_message.reply_text(result['job']['message'])
    except RuntimeError as error:
        await update.effective_message.reply_text(str(error))


@only_admin
async def cancelvoice(update, context):
    context.user_data.pop('voice_call', None)
    await update.effective_message.reply_text('Черновик сброшен. Запланированные вызовы отменяются через /cancelcall ID.')


def register_voice_handlers(app):
    for name, handler in [('voicecall', voicecall), ('voicecalls', voicecalls), ('cancelcall', cancelcall), ('cancelvoice', cancelvoice)]:
        app.add_handler(CommandHandler(name, handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, receive_voice))
    app.add_handler(CallbackQueryHandler(confirm_voice, pattern=r'^voice:(send|cancel):[a-f0-9]{24}$'))

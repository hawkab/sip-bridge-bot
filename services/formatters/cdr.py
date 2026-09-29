from datetime import datetime


def format_single_cdr(row: dict, *, local_number: str = '') -> str:
    lines = []
    if row.get("src"):
        lines.append(f"От: `{row['src']}`")
    if row.get("dst"):
        lines.append(f"Кому: {_format_recipient(row, local_number)}")
    if row.get("start"):
        lines.append(f"Начало: `{_format_datetime(row['start'])}`")
    if row.get("answer"):
        lines.append(f"Ответ: `{_format_datetime(row['answer'])}`")
    if row.get("end"):
        lines.append(f"Конец: `{_format_datetime(row['end'])}`")
    if row.get("duration"):
        lines.append(f"Длительность (сек): `{row['duration']}`")
    if row.get("billsec"):
        lines.append(f"Разговор (сек): `{row['billsec']}`")
    if row.get("disposition"):
        disp = _translate_disposition(row['disposition'])
        lines.append(f"Статус: `{disp}`")
    if not lines:
        return ""
    return "📞 *Звонок (CDR)*\n" + "\n".join(lines)


def format_cdr_group(rows: list[dict], *, local_number: str = '') -> str:
    if not rows:
        return ""
    if len(rows) == 1:
        return format_single_cdr(rows[0], local_number=local_number)

    first = rows[0]
    src = first.get('src', '?')
    context = first.get('dcontext', '')

    if 'inbound-gsm' in context:
        direction = "Входящий с GSM"
    else:
        direction = "Исходящий на GSM"

    lines = [f"📞 *{direction}*", f"От: `{src}`", f"Кому: {_format_recipient(first, local_number)}", ""]
    last_disp = ""
    for row in rows:
        start_time = _format_datetime(row.get('start', ''))
        end_time = _format_datetime(row.get('end', ''))
        duration = row.get('duration', '0')
        last_disp = _translate_disposition(row.get('disposition', ''))
        lines.append(f"{start_time} - {end_time} ({duration}с) {last_disp}")

    final_status = "Отвечен" if any(r.get('disposition') == "ANSWERED" for r in rows) else last_disp
    lines.append("")
    lines.append(f"Итог: {final_status} (попыток: {len(rows)})")
    return "\n".join(lines)


def _format_recipient(row: dict, local_number: str) -> str:
    destination = row.get('dst') or '?'
    if local_number and 'inbound-gsm' in str(row.get('dcontext', '')):
        return f"`{local_number}` (`{destination}`)"
    return f"`{destination}`"


def _format_datetime(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%H:%M:%S %d.%m.%Y")
    except (ValueError, TypeError):
        return value


def _translate_disposition(value: str) -> str:
    mapping = {
        "ANSWERED": "Отвечен",
        "NO ANSWER": "Не отвечено",
        "BUSY": "Занято",
        "FAILED": "Ошибка",
    }
    return mapping.get(value, value)

from datetime import datetime


def format_single_cdr(row: dict) -> str:
    lines = []
    if row.get("src"):
        lines.append(f"От: `{row['src']}`")
    if row.get("dst"):
        lines.append(f"Кому: `{row['dst']}`")
    if row.get("start"):
        lines.append(f"Начало: `{row['start']}`")
    if row.get("answer"):
        lines.append(f"Ответ: `{row['answer']}`")
    if row.get("end"):
        lines.append(f"Конец: `{row['end']}`")
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


def format_cdr_group(rows: list[dict]) -> str:
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
    last_disp = ""
    for row in rows:
        start_time = _short_time(row.get('start', ''))
        end_time = _short_time(row.get('end', ''))
        duration = row.get('duration', '0')
        last_disp = _translate_disposition(row.get('disposition', ''))
        lines.append(f"{start_time} - {end_time} ({duration}с) {last_disp}")

    final_status = "Отвечен" if any(r.get('disposition') == "ANSWERED" for r in rows) else last_disp
    lines.append("")
    lines.append(f"Итог: {final_status} (попыток: {len(rows)})")
    return "\n".join(lines)


def _short_time(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%H:%M:%S")
    except Exception:
        return value


def _translate_disposition(value: str) -> str:
    mapping = {
        "ANSWERED": "Отвечен",
        "NO ANSWER": "Не отвечено",
        "BUSY": "Занято",
        "FAILED": "Ошибка",
    }
    return mapping.get(value, value)

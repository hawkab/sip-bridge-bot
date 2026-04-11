def format_transcription(conversation: list[dict] | None) -> str:
    if not conversation:
        return ''

    lines: list[str] = []
    for row in conversation:
        start_hms = str(row.get('start_hms') or '')
        end_hms = str(row.get('end_hms') or '')
        speaker = str(row.get('speaker') or 'SPEAKER')
        text = ' '.join(str(row.get('text') or '').split())
        if not start_hms or not end_hms or not text:
            continue
        lines.append(f'[{start_hms} - {end_hms}] {speaker}: {text}')
    return '\n'.join(lines)

from domain.events import SMSReceivedEvent


def format_sms(event: SMSReceivedEvent) -> str:
    recipient = event.local_number or f'номер не определён (SIM {event.sim})'
    return (
        f"📩 *SMS*\n"
        f"От: `{event.sender}`\n"
        f"Кому: `{recipient}`\n"
        f"Время: `{event.received_at}`\n\n"
        f"{event.text}"
    )

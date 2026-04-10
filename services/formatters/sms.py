from domain.events import SMSReceivedEvent


def format_sms(event: SMSReceivedEvent) -> str:
    return (
        f"📩 *SMS*\n"
        f"От: `{event.sender}`\n"
        f"SIM: `{event.sim}`\n"
        f"Время: `{event.received_at}`\n\n"
        f"{event.text}"
    )

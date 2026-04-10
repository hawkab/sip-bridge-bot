from services.delivery_service import DeliveryHub


class NotificationService:
    def __init__(self, delivery: DeliveryHub):
        self.delivery = delivery

    async def notify(self, subject: str, text: str, attachment_path: str | None = None, attachment_name: str | None = None, parse_mode: str | None = None) -> None:
        await self.delivery.notify_event(subject=subject, text=text, attachment_path=attachment_path, attachment_name=attachment_name, parse_mode=parse_mode)

import os


def resolve_recording_path(uniqueid: str) -> tuple[str | None, str | None]:
    if not uniqueid:
        return None, None
    record_path = f"/var/spool/asterisk/monitor/{uniqueid}.wav"
    if os.path.exists(record_path) and os.path.getsize(record_path) > 44:
        return record_path, f"{uniqueid}.wav"
    return None, None

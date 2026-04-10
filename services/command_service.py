import os
import shlex
import subprocess
import time
from dataclasses import dataclass

from bootstrap.config import CONFIG
from domain.models import CommandResult, ResponseItem
from services.system_ops import (
    _write_tmp,
    get_asterisk_logs,
    get_os_logs,
    get_status,
    git_pull,
    run,
)


@dataclass
class CommandService:
    ys: object

    async def execute(self, raw_command: str) -> CommandResult:
        raw = (raw_command or "").strip()
        if not raw:
            return self._help_result()

        try:
            parts = shlex.split(raw)
        except ValueError as exc:
            return CommandResult([ResponseItem(kind="text", text=f"Ошибка разбора команды: {exc}")])

        if not parts:
            return self._help_result()

        cmd = parts[0]
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        args = parts[1:]

        if cmd == "/start":
            return self._help_result()
        if cmd == "/status":
            return CommandResult([ResponseItem(kind="text", text=get_status(), parse_mode="Markdown")])
        if cmd == "/logs_os":
            return self._logs_result("os", get_os_logs, args)
        if cmd == "/logs_sip":
            return self._logs_result("sip", get_asterisk_logs, args)
        if cmd == "/cdr_csv":
            cdr_file = "/var/log/asterisk/cdr-csv/Master.csv"
            if not os.path.exists(cdr_file):
                return CommandResult([ResponseItem(kind="text", text=f"Файл не найден: {cdr_file}")])
            return CommandResult([
                ResponseItem(
                    kind="file",
                    attachment_path=cdr_file,
                    attachment_name=f"Master_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                    caption="Файл CDR Asterisk",
                )
            ])
        if cmd == "/asterisk_restart":
            out = run("sudo systemctl restart asterisk")
            return CommandResult([ResponseItem(kind="text", text=f"Asterisk restart: {out}")])
        if cmd == "/reboot":
            if args and args[0].lower() in {"yes", "confirm", "1"}:
                return CommandResult(
                    [ResponseItem(kind="text", text="Перезагружаюсь…")],
                    post_action="reboot_host",
                )
            return CommandResult([
                ResponseItem(kind="text", text="Для подтверждения отправьте: /reboot yes")
            ])
        if cmd == "/update":
            items = [ResponseItem(kind="text", text="⬇️ Обновляюсь из Git и готовлю перезапуск сервиса…")]
            log = git_pull(CONFIG.GIT_REPO_DIR, CONFIG.GIT_BRANCH)
            fname = f"update_{time.strftime('%Y%m%d_%H%M%S')}.log"
            p = _write_tmp(fname, log)
            items.append(ResponseItem(kind="file", attachment_path=p, attachment_name=fname, caption="Git pull log"))
            items.append(ResponseItem(kind="text", text=f"🔁 Перезапуск {CONFIG.BOT_SERVICE_NAME} будет выполнен через 2 секунды."))
            return CommandResult(items, post_action="restart_bot_service")
        if cmd == "/ys_ping":
            r = await self.ys.send_command("gsm show spans")
            return CommandResult([ResponseItem(kind="text", text=f"{r}")])
        if cmd == "/ys_cmd":
            if not args:
                return CommandResult([ResponseItem(kind="text", text="Формат: /ys_cmd <raw command>")])
            raw_ys_cmd = " ".join(args)
            r = await self.ys.send_command(raw_ys_cmd, wait=3.0)
            lines = [f"{k}: {v}" for k, v in r.items()]
            return CommandResult([ResponseItem(kind="text", text="Ответ TG:\n" + ("\n".join(lines) if lines else "нет данных"))])

        return CommandResult([ResponseItem(kind="text", text=f"Неизвестная команда: {cmd}\n\n{self._help_text()}")])

    def _logs_result(self, prefix: str, producer, args: list[str]) -> CommandResult:
        n = int(args[0]) if (args and args[0].isdigit()) else 200
        txt = producer(n)
        fname = f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.log"
        p = _write_tmp(fname, txt)
        return CommandResult([ResponseItem(kind="file", attachment_path=p, attachment_name=fname)])

    def _help_text(self) -> str:
        return (
            "Доступные команды:\n"
            "/status — статус сервера\n"
            "/logs_os [N] — последние строки системного журнала\n"
            "/logs_sip [N] — последние строки журнала Asterisk\n"
            "/cdr_csv — скачать файл CDR Asterisk Master.csv\n"
            "/asterisk_restart — рестарт Asterisk\n"
            "/reboot yes — перезагрузка сервера\n"
            "/update — git pull + рестарт бота\n"
            "/ys_ping\n"
            "/ys_cmd <raw>"
        )

    def _help_result(self) -> CommandResult:
        return CommandResult([ResponseItem(kind="text", text=self._help_text())])


def execute_post_action(action: str | None) -> None:
    if action == "restart_bot_service":
        subprocess.Popen([
            "/bin/sh",
            "-lc",
            f"sleep 2; sudo -n systemctl restart {shlex.quote(CONFIG.BOT_SERVICE_NAME)} >/tmp/sms-bot-restart.log 2>&1",
        ])
    elif action == "reboot_host":
        subprocess.Popen(["sudo", "/sbin/reboot"])

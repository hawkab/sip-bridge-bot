import asyncio, urllib.parse, traceback, time
import secrets
import re
from dataclasses import dataclass


@dataclass
class SmsSendResult:
    status: str
    message: str
    gateway_id: str = ""

from typing import Optional

class YeastarSMSClient:
    """
    AMI-подобный TCP API TG200 (порт 5038, 'SMS Account').
    - Event: ReceivedSMS
    - Action: smscommand (обычно 'Response: Follows' + текст до '--END COMMAND--')
    """
    def __init__(self, host: str, port: int, user: str, pwd: str, *, span_offset: int = 1):
        self.host, self.port, self.user, self.pwd = host, port, user, pwd
        self.span_offset = span_offset
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.on_sms = None
        self.resp_queue: asyncio.Queue = asyncio.Queue()
        self._sms_parts = {}
        self.ready = asyncio.Event()
        self._command_lock = asyncio.Lock()
        self._send_locks = {}
        self._last_send = {}
        self._pending_sms = {}
    
    async def connect_forever(self):
        while True:
            reader_task = keep_task = None
            try:
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                while not self.resp_queue.empty():
                    self.resp_queue.get_nowait()
                reader_task = asyncio.create_task(self._read_loop())
                await self._login_and_drain()
                self.ready.set()
                keep_task = asyncio.create_task(self._keepalive())
                await reader_task
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            finally:
                self.ready.clear()
                for task in (reader_task, keep_task):
                    if task:
                        task.cancel()
                await asyncio.gather(*(t for t in (reader_task, keep_task) if t), return_exceptions=True)
                if self.writer:
                    self.writer.close()
                    try:
                        await self.writer.wait_closed()
                    except Exception:
                        pass
                self.writer = None
            await asyncio.sleep(3)

    async def _login_and_drain(self):
        await self._send_raw(f"Action: Login\r\nUsername: {self.user}\r\nSecret: {self.pwd}\r\n\r\n")
        reply = await asyncio.wait_for(self.resp_queue.get(), timeout=5)
        if reply.get("Response", "").lower() != "success":
            raise RuntimeError("TG authentication failed")

    async def _keepalive(self):
        while True:
            await asyncio.sleep(60)
            await self.send_command("gsm show spans")

    async def _read_loop(self):
        buf = b""
        while True:
            chunk = await self.reader.read(4096)
            if not chunk:
                raise RuntimeError("Disconnected")
            buf += chunk
            while b"\r\n\r\n" in buf:
                block, buf = buf.split(b"\r\n\r\n", 1)
                self._handle_block(block.decode(errors="ignore"))

    @staticmethod
    def _parse_block(text: str) -> dict:
        kv, outputs, rawlines = {}, [], []
        for line in text.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                k = k.strip(); v = v.strip()
                if k.lower() == "output":
                    outputs.append(v)
                else:
                    kv[k] = v
            else:
                s = line.strip()
                if s:
                    rawlines.append(s)
        outs = outputs + rawlines
        if outs:
            kv["Outputs"] = outs
        kv["_raw"] = text
        return kv

    def _handle_block(self, block: str):
        kv = self._parse_block(block)

        if kv.get("Event") == "UpdateSMSSend":
            future = self._pending_sms.get(str(kv.get("ID", "")))
            if future and not future.done():
                future.set_result(kv)
            return
        if "Response" in kv or ("--END COMMAND--" in block and not kv.get("Event")):
            self.resp_queue.put_nowait(kv)
            return

        if kv.get("Event") == "ReceivedSMS":
            sender = kv.get("Sender", "")
            sim    = kv.get("GsmPort", "") or kv.get("Port", "")
            if not sim and kv.get("GsmSpan", "").isdigit():
                sim = str(int(kv["GsmSpan"]) - self.span_offset)
            when   = kv.get("Recvtime", "") or kv.get("Time", "")

            sms_id = (kv.get("ID", "") or "").strip()
            idx_raw = (kv.get("Index", "") or "1").strip()
            total_raw = (kv.get("Total", "") or "1").strip()

            try:
                idx = int(idx_raw)
            except Exception:
                idx = 1
            try:
                total = int(total_raw)
            except Exception:
                total = 1

            raw = kv.get("Content", "") or ""
            try:
                part = urllib.parse.unquote_plus(raw).lstrip("\ufeff")
            except Exception:
                part = raw

            # Если не длинная (или нет ID) — сразу отдаём
            if total <= 1 or not sms_id:
                if self.on_sms:
                    try:
                        self.on_sms(sender, sim, when, part)
                    except Exception:
                        traceback.print_exc()
                return

            # чистим старые хвосты (5 минут)
            now = time.time()
            for k in list(self._sms_parts.keys()):
                if now - self._sms_parts[k]["ts"] > 300:
                    self._sms_parts.pop(k, None)

            key = f"{sms_id}:{sender}:{sim}"
            buf = self._sms_parts.setdefault(key, {"total": total, "parts": {}, "ts": now, "when": when})
            buf["total"] = total
            buf["ts"] = now
            buf["when"] = buf.get("when") or when
            buf["parts"][idx] = part

            # если собрали всё — склеиваем по порядку
            if len(buf["parts"]) >= total:
                base = 0 if 0 in buf["parts"] else 1
                if not all(i in buf["parts"] for i in range(base, base + total)):
                    return
                full = "".join(buf["parts"][i] for i in range(base, base + total)).lstrip("\ufeff")
                self._sms_parts.pop(key, None)

                if self.on_sms:
                    try:
                        self.on_sms(sender, sim, buf["when"], full)
                    except Exception:
                        traceback.print_exc()
            return

    async def _send_raw(self, s: str):
        if not self.writer:
            raise RuntimeError("not connected")
        self.writer.write(s.encode())
        await self.writer.drain()

    async def send_command(self, command: str, wait: float = 4.0) -> dict:
        if "\r" in command or "\n" in command:
            raise ValueError("Command must be a single line")
        await asyncio.wait_for(self.ready.wait(), timeout=10)
        async with self._command_lock:
            action_id = secrets.token_hex(12)
            while not self.resp_queue.empty():
                self.resp_queue.get_nowait()
            await self._send_raw(f"Action: smscommand\r\nActionID: {action_id}\r\ncommand: {command}\r\n\r\n")
            deadline = asyncio.get_running_loop().time() + wait
            blocks = []
            try:
                while True:
                    reply = await asyncio.wait_for(self.resp_queue.get(), max(0.01, deadline - asyncio.get_running_loop().time()))
                    if reply.get("ActionID") and reply["ActionID"] != action_id:
                        continue
                    blocks.append(reply)
                    if reply.get("Response", "").lower() not in ("", "follows") or "--END COMMAND--" in reply.get("_raw", ""):
                        result = dict(blocks[0])
                        result["Outputs"] = [line for block in blocks for line in block.get("Outputs", [])]
                        result["_raw"] = "\n".join(block.get("_raw", "") for block in blocks)
                        return result
            except asyncio.TimeoutError:
                # A late untagged reply must never acknowledge the next command.
                self.ready.clear()
                if self.writer:
                    self.writer.close()
                return {"Response": "Timeout", "Message": "No reply from TG"}

    async def send_sms(self, number: str, text: str, sim_port: int, *, span_offset: int = 1, status_timeout: float = 60) -> SmsSendResult:
        if not re.fullmatch(r"\+?[1-9][0-9]{6,14}", number):
            raise ValueError("Укажите номер в международном формате, например +79991234567.")
        if not text.strip() or len(text.encode("utf-8")) > 1024 or "\x00" in text:
            raise ValueError("Текст СМС должен содержать от 1 до 1024 байт UTF-8.")
        if type(sim_port) is not int or not 1 <= sim_port <= 32:
            raise ValueError("Некорректный порт SIM.")
        lock = self._send_locks.setdefault(sim_port, asyncio.Lock())
        async with lock:
            delay = 10 - (time.monotonic() - self._last_send.get(sim_port, 0))
            if delay > 0:
                await asyncio.sleep(delay)
            if not self.ready.is_set():
                return SmsSendResult("failed", "Шлюз не подключён. Сообщение не отправлено.")
            sms_id = str(secrets.randbelow(2**31 - 1) + 1)
            future = asyncio.get_running_loop().create_future()
            self._pending_sms[sms_id] = future
            encoded = urllib.parse.quote(text, safe="")
            try:
                self._last_send[sim_port] = time.monotonic()
                # TG physical port 1 is Asterisk span 2. Exactly one command;
                # never fall back to another spelling after an uncertain result.
                reply = await self.send_command(f'gsm send sms {sim_port + span_offset} {number} "{encoded}" {sms_id}', wait=8)
                if reply.get("Response", "").lower() == "error":
                    return SmsSendResult("failed", "Шлюз отклонил отправку СМС.", sms_id)
                if reply.get("Response") == "Timeout" and not future.done():
                    return SmsSendResult("unknown", "Нет подтверждения шлюза. Проверьте получателя перед повторной отправкой.", sms_id)
                status = await asyncio.wait_for(future, timeout=status_timeout)
                if str(status.get("Status")) == "1":
                    return SmsSendResult("sent", "Шлюз подтвердил отправку СМС оператору.", sms_id)
                return SmsSendResult("failed", "Шлюз сообщил об ошибке отправки СМС.", sms_id)
            except (asyncio.TimeoutError, OSError, RuntimeError):
                return SmsSendResult("unknown", "Результат отправки неизвестен. Проверьте получателя перед повтором.", sms_id)
            finally:
                self._pending_sms.pop(sms_id, None)
                if not future.done():
                    future.cancel()

import asyncio, urllib.parse, traceback
from typing import Optional
from config import CONFIG

class YeastarSMSClient:
    """
    AMI-подобный TCP API TG200 (порт 5038, 'SMS Account').
    - Event: ReceivedSMS
    - Action: smscommand (обычно 'Response: Follows' + текст до '--END COMMAND--')
    """
    def __init__(self, host: str, port: int, user: str, pwd: str):
        self.host, self.port, self.user, self.pwd = host, port, user, pwd
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.on_sms = None
        self.resp_queue: asyncio.Queue = asyncio.Queue()

    async def connect_forever(self):
        while True:
            try:
                self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
                # запускаем чтение до логина, чтобы съесть "Authentication accepted"
                reader_task = asyncio.create_task(self._read_loop())
                await self._login_and_drain()
                keep_task = asyncio.create_task(self._keepalive())
                await reader_task
                keep_task.cancel()
            except Exception:
                await asyncio.sleep(3)

    async def _login_and_drain(self):
        self.writer.write(f"Action: Login\r\nUsername: {self.user}\r\nSecret: {self.pwd}\r\n\r\n".encode())
        await self.writer.drain()
        try:
            _ = await asyncio.wait_for(self.resp_queue.get(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

    async def _keepalive(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self._send_raw("Action: smscommand\r\ncommand: gsm show spans\r\n\r\n")
            except Exception:
                break

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
        if kv.get("Event") == "ReceivedSMS":
            sender = kv.get("Sender","")
            sim    = kv.get("GsmPort","") or kv.get("Port","")
            when   = kv.get("Recvtime","") or kv.get("Time","")
            raw    = kv.get("Content","")
            try:
                text = urllib.parse.unquote(raw)
            except Exception:
                text = raw
            if self.on_sms:
                try:
                    self.on_sms(sender, sim, when, text)
                except Exception:
                    traceback.print_exc()
            return

        if any(k in kv for k in ("Response","Message","Outputs")):
            try:
                self.resp_queue.put_nowait(kv)
            except Exception:
                pass

    async def _send_raw(self, s: str):
        if not self.writer:
            raise RuntimeError("not connected")
        self.writer.write(s.encode())
        await self.writer.drain()

    async def send_command(self, command: str, wait: float = 4.0) -> dict:
        await self._send_raw(f"Action: smscommand\r\ncommand: {command}\r\n\r\n")
        try:
            first = await asyncio.wait_for(self.resp_queue.get(), timeout=wait)
        except asyncio.TimeoutError:
            return {"Response":"Timeout","Message":"No reply from TG"}

        if (first.get("Response","").lower() != "follows"):
            return first

        outputs = list(first.get("Outputs", []))
        end_seen = any(s.strip().endswith("--END COMMAND--") for s in outputs)
        deadline = asyncio.get_event_loop().time() + wait
        while not end_seen and asyncio.get_event_loop().time() < deadline:
            try:
                more = await asyncio.wait_for(self.resp_queue.get(), timeout=1.0)
                outputs += more.get("Outputs", [])
                end_seen = any(s.strip().endswith("--END COMMAND--") for s in outputs)
            except asyncio.TimeoutError:
                break
        return {"Response":"Follows","Message":first.get("Message",""),"Outputs":outputs}

    # оставлено для совместимости с вашими /sms и /reply
    async def send_sms(self, number: str, text: str, sim_port: int):
        enc = urllib.parse.quote(text)
        r1 = await self.send_command(f"gsm send sms {sim_port} {number} {enc}")
        r2 = await self.send_command(f"sms send {sim_port} {number} {enc}")
        safe = text.replace('"', "'")
        r3 = await self.send_command(f'gsm send sms {sim_port} {number} "{safe}"')
        return r1, r2, r3

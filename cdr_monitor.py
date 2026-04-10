import csv
import os
import asyncio
from pathlib import Path
from typing import Callable, Optional, List, Dict
from datetime import datetime

class CDRMonitor:
    """
    Мониторинг файла CDR Asterisk в формате CSV с группировкой близких вызовов.
    При появлении новых записей собирает их в группы и вызывает callback с группой.
    """
    def __init__(self, cdr_path: str, callback: Callable[[List[Dict]], None], 
                 check_interval: float = 5.0, group_timeout: float = 30.0):
        self.cdr_path = Path(cdr_path)
        self.callback = callback          # асинхронная функция, принимающая список словарей
        self.interval = check_interval
        self.group_timeout = group_timeout
        self.last_position = 0
        self._task: Optional[asyncio.Task] = None
        self._current_group: List[Dict] = []          # текущая собираемая группа
        self._last_group_time: Optional[float] = None  # время последней записи в группе

    async def start(self):
        """Запускает мониторинг файла."""
        # Ожидаем появления файла, если его ещё нет
        while not self.cdr_path.exists():
            await asyncio.sleep(self.interval)

        # Сразу переходим в конец файла, чтобы не читать старые записи
        with open(self.cdr_path, 'r', encoding='utf-8') as f:
            f.seek(0, os.SEEK_END)
            self.last_position = f.tell()

        self._task = asyncio.create_task(self._run())

    async def _run(self):
        """Основной цикл проверки новых строк и таймаута группы."""
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self._check_new_cdrs()
                await self._check_group_timeout()
            except Exception as e:
                # Логируем ошибку, но не останавливаем цикл
                print(f"CDRMonitor error: {e}")

    async def _check_new_cdrs(self):
        """Проверяет, появились ли новые строки в файле, и добавляет их в группу."""
        if not self.cdr_path.exists():
            return

        with open(self.cdr_path, 'r', encoding='utf-8') as f:
            f.seek(self.last_position)
            new_lines = f.readlines()
            if not new_lines:
                return
            self.last_position = f.tell()

        # Парсим CSV. Предполагается, что в файле нет заголовка.
        fieldnames = [
            "accountcode", "src", "dst", "dcontext", "clid", "channel", "dstchannel",
            "lastapp", "lastdata", "start", "answer", "end", "duration", "billsec",
            "disposition", "amaflags", "uniqueid", "userfield"
        ]
        reader = csv.DictReader(new_lines, fieldnames=fieldnames)

        for row in reader:
            # Пропускаем технические вызовы без src/dst
            if not row.get('src') or not row.get('dst'):
                continue

            # Если группа не пуста, проверяем, подходит ли новая запись под группу
            if self._current_group:
                last_row = self._current_group[-1]
                same_src = row['src'] == last_row['src']
                same_dst = row['dst'] == last_row['dst']
                same_context = row.get('dcontext') == last_row.get('dcontext')
                time_diff_ok = self._time_diff(last_row, row) <= self.group_timeout

                if same_src and same_dst and same_context and time_diff_ok:
                    # Добавляем в текущую группу
                    self._current_group.append(row)
                    self._last_group_time = asyncio.get_event_loop().time()
                else:
                    # Запись не подходит – отправляем старую группу и начинаем новую
                    await self._flush_group()
                    self._current_group = [row]
                    self._last_group_time = asyncio.get_event_loop().time()
            else:
                # Группа пуста – начинаем новую
                self._current_group = [row]
                self._last_group_time = asyncio.get_event_loop().time()

    def _time_diff(self, row1: Dict, row2: Dict) -> float:
        """
        Вычисляет разницу во времени между концом первого вызова и началом второго.
        Возвращает разницу в секундах.
        """
        try:
            end1 = datetime.strptime(row1['end'], "%Y-%m-%d %H:%M:%S") if row1.get('end') else None
            start2 = datetime.strptime(row2['start'], "%Y-%m-%d %H:%M:%S") if row2.get('start') else None
            if end1 and start2:
                return (start2 - end1).total_seconds()
        except:
            pass
        # Если не удалось вычислить, считаем, что разница большая
        return float('inf')

    async def _check_group_timeout(self):
        """Проверяет, не истёк ли таймаут для текущей группы."""
        if self._current_group and self._last_group_time:
            now = asyncio.get_event_loop().time()
            if now - self._last_group_time > self.group_timeout:
                await self._flush_group()

    async def _flush_group(self):
        """Отправляет текущую группу через callback и очищает её."""
        if self._current_group:
            await self.callback(self._current_group)
            self._current_group = []
            self._last_group_time = None

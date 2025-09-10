# SipBridgeBot

Телеграм-бот для домашнего/офисного VoIP-комплекса: присылает **входящие SMS** со шлюза Yeastar TG (режим *SMS Account* по TCP) в личный Telegram-чат и предоставляет набор **админ-команд** для сервера: статус, логи OS/Asterisk, управление WireGuard, перезапуск Asterisk, перезагрузка хоста и **дистанционное обновление** бота (`git pull` + `systemctl restart`).  
Отправка SMS из Telegram намеренно **удалена**: бот работает только на приём SMS + админ-команды.

---

## Минимальные системные требования

- Linux x86_64/ARM (подходит Raspberry Pi 4/5).
- Python **3.10+** (рекомендовано 3.11).
- Сетевой доступ:
  - исходящий к Telegram (HTTPS/443);
  - доступ к шлюзу Yeastar TG по TCP **5038** (локальная сеть).
- (Опционально) WireGuard установлен, если хотите команды `/vpn_on` и `/vpn_off`.
- Для температуры на Raspberry Pi: пакет `libraspberrypi-bin` (команда `vcgencmd`).

---

## Установка

### 1) Подготовка системы

    sudo apt update
    sudo apt install -y python3-venv git curl
    # (опционально, для Raspberry)
    sudo apt install -y libraspberrypi-bin
    # (опционально, если используете WireGuard-управление)
    sudo apt install -y wireguard

### 2) Развёртывание кода

    sudo mkdir -p /opt/sms
    sudo chown -R asterisk:asterisk /opt/sms          # замените пользователя, если у вас другой
    sudo -u asterisk bash -lc '
      cd /opt/sms
      python3 -m venv venv
      source venv/bin/activate
      # Если у вас уже есть репозиторий:
      git clone <ВАШ_GIT_REMOTE_URL> .
      pip install -r requirements.txt
    '

> Если репозиторий под `root`/другим пользователем, Git может ругаться на “detected dubious ownership”.  
> Рекомендуется сменить владельца:
>
>     sudo chown -R asterisk:asterisk /opt/sms
>
> Либо пометить каталог как безопасный **от имени пользователя бота**:
>
>     sudo -u asterisk git config --global --add safe.directory /opt/sms

### 3) Настройка `.env`

Создайте `/opt/sms/.env` (каждый параметр на своей строке, **без** комментариев в конце строки):

    # Telegram
    BOT_TOKEN=123456:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    ADMIN_LOGIN=your_telegram_username   # без @ (комментарий перенесите на отдельную строку!)

    # Yeastar TG (SMS Account / TCP API)
    TG_HOST=192.168.1.150
    TG_PORT=5038
    TG_USER=smsuser
    TG_PASS=smspass
    TG_DEFAULT_SIM=1

    # Пути/сервисы
    ASTERISK_CLI=/usr/sbin/asterisk
    ASTERISK_LOG=/var/log/asterisk/messages
    OS_LOG=/var/log/syslog
    WG_IFACE=wg0

    # Git-обновление
    GIT_REPO_DIR=/opt/sms
    GIT_BRANCH=main
    BOT_SERVICE_NAME=bot.service
    # Если хотите автоклон при /update, когда репозитория нет:
    # GIT_REMOTE_URL=https://github.com/you/your-repo.git

### 4) Права и группы

- Разрешить боту читать `journald` (для `/logs_os` и `/logs_sip`, если файловых логов нет):

      sudo usermod -aG systemd-journal asterisk

- Разрешить перезапуск сервисов без пароля (**NOPASSWD**). Откройте файл sudoers:

      sudo visudo -f /etc/sudoers.d/sms-bot

  Вставьте строки (уточните путь к `systemctl` и имя юнита бота):

      asterisk ALL=(root) NOPASSWD:/usr/bin/systemctl restart bot.service
      asterisk ALL=(root) NOPASSWD:/usr/bin/systemctl start wg-quick@wg0,/usr/bin/systemctl stop wg-quick@wg0
      asterisk ALL=(root) NOPASSWD:/usr/bin/systemctl restart asterisk
      asterisk ALL=(root) NOPASSWD:/sbin/reboot

  Узнать путь к `systemctl`:

      command -v systemctl

### 5) Systemd-юнит

Файл `/etc/systemd/system/bot.service`:

    [Unit]
    Description=SipBridgeBot
    After=network-online.target

    [Service]
    User=asterisk
    Group=asterisk
    WorkingDirectory=/opt/sms
    ExecStart=/opt/sms/venv/bin/python /opt/sms/bot.py
    Restart=always

    [Install]
    WantedBy=multi-user.target

Применить и запустить:

    sudo systemctl daemon-reload
    sudo systemctl enable --now bot.service

### 6) Настройка Yeastar TG

- В веб-интерфейсе TG200 откройте **SMS Account Setting**:
  - создайте пользователя/пароль (используйте их в `TG_USER`/`TG_PASS`);
  - убедитесь, что API слушает порт **5038** (или укажите свой в `TG_PORT`).
- Проверьте сетевую доступность с сервера бота:

      nc -vz 192.168.1.150 5038

---

## Переменные окружения (`.env`)

| Ключ               | Обяз. | По умолчанию                 | Описание |
|--------------------|:----:|------------------------------|---------|
| `BOT_TOKEN`        |  да  | —                            | Токен Telegram-бота |
| `ADMIN_LOGIN`      |  да  | —                            | Ваш username в TG (без `@`); только вы можете пользоваться ботом |
| `TG_HOST`          |  да  | —                            | IP/домен шлюза Yeastar TG |
| `TG_PORT`          |  нет | `5038`                       | Порт TCP API (*SMS Account*) |
| `TG_USER`          |  да  | —                            | Логин *SMS Account* |
| `TG_PASS`          |  да  | —                            | Пароль *SMS Account* |
| `TG_DEFAULT_SIM`   |  нет | `1`                          | SIM-порт по умолчанию (информативно) |
| `ASTERISK_CLI`     |  нет | `/usr/sbin/asterisk`         | Путь к бинарнику Asterisk CLI |
| `ASTERISK_LOG`     |  нет | `/var/log/asterisk/messages` | Файл журнала Asterisk; если нет — используется `journalctl -u asterisk` |
| `OS_LOG`           |  нет | `/var/log/syslog`            | Системный лог; если нет — используется общий `journalctl` |
| `WG_IFACE`         |  нет | `wg0`                        | Имя интерфейса WireGuard |
| `GIT_REPO_DIR`     |  нет | `/opt/sms`                   | Каталог репозитория приложения |
| `GIT_BRANCH`       |  нет | `main`                       | Целевая ветка для `/update` |
| `BOT_SERVICE_NAME` |  нет | `bot.service`                | Имя systemd-юнита бота (для рестартов из чата) |
| `GIT_REMOTE_URL`   |  нет | —                            | URL origin; нужен, если хотите автоклон при `/update` |

> Комментарии в `.env` помещайте на **отдельные строки**, не после значения.

---

## Структура проекта

    /opt/sms/
    ├─ bot.py           # точка входа
    ├─ config.py        # загрузка .env, объект CONFIG
    ├─ auth.py          # only_admin, кэш admin chat id
    ├─ utils.py         # run/journal/tail, статус, git-пулл, версия из git
    ├─ ys_client.py     # TCP-клиент Yeastar TG (приём SMS, события)
    ├─ handlers.py      # Telegram-хэндлеры и их регистрация
    ├─ requirements.txt
    └─ README.md

---

## Использование

1. В Telegram отправьте боту `/start` **со своего аккаунта** (`ADMIN_LOGIN`).  
   При первом обращении бот запомнит ваш `chat_id`.  
   При каждом запуске сервиса бот присылает уведомление “✅ Бот запущен …” и **текущую версию из Git**.

2. Доступные команды:
   - `/status` — сводка: аптайм, температура, диск, RAM, состояние WireGuard, состояние и аптайм Asterisk, **версия приложения (Git)**.
   - `/logs_os [N]` — последние N строк системного журнала (файл или `journalctl`); присылается файлом.
   - `/logs_sip [N]` — последние N строк журнала Asterisk; присылается файлом.
   - `/vpn_on` / `/vpn_off` — запустить/остановить `wg-quick@${WG_IFACE}` (нужны правила NOPASSWD).
   - `/asterisk_restart` — перезапуск службы Asterisk.
   - `/reboot` — запрос на перезагрузку хоста (кнопка подтверждения).
   - `/update` — `git pull` в `${GIT_REPO_DIR}` из `${GIT_BRANCH}` и `systemctl restart ${BOT_SERVICE_NAME}`. Лог `git pull` приходит файлом.

3. Входящие SMS со шлюза TG:
   - Бот **автоматически** присылает входящие SMS в ваш чат: отправитель, порт SIM, время, текст.  
   - Ответ из Telegram **не поддерживается** (функционал отключён).

---

## Советы по эксплуатации

- Если `/status` в разделе “Asterisk uptime” показывает «Unable to connect…», проверьте:
  - под кем запущены демон Asterisk и бот (рекомендуется один и тот же пользователь — `asterisk`);
  - права на сокет `/var/run/asterisk/asterisk.ctl`.
- Если `/update` пишет про “dubious ownership”:
  - предпочтительно: `sudo chown -R asterisk:asterisk /opt/sms`;
  - либо: `sudo -u asterisk git config --global --add safe.directory /opt/sms`.
- Для файловых логов Asterisk можно настроить `/etc/asterisk/logger.conf` и выполнить `asterisk -rx 'logger reload'` — бот автоматически начнёт читать файл вместо `journalctl`.

---

## Безопасность

- Не публикуйте `BOT_TOKEN` и `TG_*`.
- Доступ к боту — только по `ADMIN_LOGIN`.
- В sudoers добавляйте минимально необходимый набор команд и точные пути (`/usr/bin/systemctl` и т. п.).

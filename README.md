# Ranepa Schedule Bot

Telegram-бот, который переносит расписание из личного кабинета РАНХиГС (my.ranepa.ru) в Google Calendar. Бот создаёт, обновляет и удаляет события, когда расписание меняется, и присылает уведомления об изменениях.

## Как это работает

1. Пользователь пишет боту, даёт согласие на обработку данных и вводит логин и пароль от ЛК.
2. Бот присылает ссылку для доступа к Google Calendar. Пользователь входит в свой аккаунт Google и копирует в бота адрес `localhost` из браузера.
3. Раз в несколько часов `sync_all.py` забирает расписание и сверяет его с календарём. Пара определяется по ключу `uid_disc + start_date`, а аудитория, тип, преподаватель и статус хранятся в `extendedProperties` события.
4. Бот читает результаты синхронизации из SQLite и отправляет пользователю уведомления.

Логины, пароли и токены Google хранятся в `data/bot.db` в зашифрованном виде (Fernet, ключ в `.env`).

## Ограничения

- **Капча не обходится.** Если ЛК требует капчу, синхронизация не пройдёт.
- **Нет постоянного сервера.** Бот и автообновление работают, только пока включён компьютер. Пропущенные запуски догоняются при следующем включении.
- **Сеть.** В России Telegram доступен через VPN, а ЛК отдаёт данные только без VPN. Поэтому `sync_all.py` не обращается к Telegram, а уведомления отправляет бот.
- **Приложение Google не проходило проверку.** При входе Google показывает предупреждение «Google не проверил это приложение». Нужно нажать «Дополнительно» и продолжить.
- **Лимит около 100 пользователей** на непроверенное приложение Google.
- **Отдельный календарь.** Синхронизация управляет событиями в календаре, который выбран для расписания. События, созданные вручную в этом календаре, код не защищает. Для личных событий используй другой календарь.
- Удаление данных пользователя пока выполняется вручную администратором.

## Установка

Нужен Python 3.14 (проверено на нём) и Windows.

```bash
git clone https://github.com/d1montkg/Project-Schedule-Ranepa.git
cd Project-Schedule-Ranepa
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

### Google OAuth

1. В Google Cloud Console создай проект и включи Google Calendar API.
2. Настрой экран согласия (External) и создай OAuth-клиент типа **Desktop app**.
3. Скачай JSON и положи его в `secrets/client_secret.json`. Папка `secrets/` в `.gitignore`, файл не попадёт в репозиторий.

### Настройка `.env`

Скопируй шаблон и заполни значения:

```bash
cp .env.example .env
```

- `BOT_TOKEN`: токен от @BotFather.
- `ENCRYPTION_KEY`: ключ Fernet. Сгенерировать:
  ```bash
  .venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
  Сохрани ключ отдельно: без него зашифрованные данные в БД не прочитать.
- `ADMIN_ID`: твой числовой Telegram ID (узнать у @userinfobot), туда приходит сводка для администратора.

## Запуск

### Бот

```bash
.venv/Scripts/python.exe bot.py
```

Запускай именно через `.venv/Scripts/python.exe`: системный Python не содержит нужных пакетов.

### Автообновление

Синхронизацию запускает Планировщик заданий Windows без окна консоли: каждые 2 часа и при входе в систему. Если компьютер был выключен, пропущенный запуск выполнится сразу после включения. Выполни в PowerShell, подставив путь к проекту:

```powershell
$dir = "C:\path\to\Project-Schedule-Ranepa"
$action = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\pythonw.exe" -Argument "sync_all.py" -WorkingDirectory $dir
$every2h = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 2) -RepetitionDuration (New-TimeSpan -Days 3650)
$logon = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "RanepaSync" -Action $action -Trigger $every2h,$logon -Settings $settings -Force
```

Лог синхронизации: `data/sync_all.log`. Запуск вручную: `.venv/Scripts/python.exe sync_all.py`.

## Структура

| Файл | Назначение |
|---|---|
| `bot.py` | Telegram-бот: регистрация, кнопки, `/status`, `/reminders`, уведомления |
| `first_try.py` | Получение расписания и синхронизация с Google Calendar |
| `sync_all.py` | Синхронизация для всех пользователей |
| `google_auth.py` | OAuth для каждого пользователя |
| `reminders.py` | Настройки напоминаний |
| `db.py` | SQLite |
| `crypto.py` | Шифрование данных |
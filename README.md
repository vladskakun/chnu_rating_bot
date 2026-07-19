# CHNU Rating Telegram Bot

Telegram-бот для аналізу рейтингів вступників ЧНУ.

## Можливості

- перевірка обраних освітніх програм;
- перевірка всіх ОП на вказаних сторінках;
- прогнозоване місце за конкурсним балом;
- кількість місць державного замовлення;
- виключення заяв із позначкою `ПЛ` із бюджетного рейтингу;
- два найближчі результати вище та нижче;
- персональна історія останніх 5 унікальних балів;
- кнопки для повторного вибору попереднього бала.

## Файли проєкту

```text
bot.py               Telegram-інтерфейс
rating_parser.py     парсинг і розрахунок рейтингу
score_history.py     SQLite-історія останніх балів
config.py            читання змінних середовища
requirements.txt     Python-залежності
railway.json         команда запуску Railway
.env.example         приклад локальних змінних
.gitignore           файли, які не потрапляють у GitHub
```

## Локальний запуск

Встановіть залежності:

```bash
py -m pip install -r requirements.txt
```

Створіть `.env` на основі `.env.example`:

```powershell
Copy-Item .env.example .env
```

Заповніть:

```env
BOT_TOKEN=ваш_токен
```

Запустіть:

```bash
py bot.py
```

Локально історія створиться у файлі `user_scores.db`.
Він навмисно виключений із GitHub.

## Завантаження в GitHub

Створіть на GitHub новий порожній репозиторій без автоматичного
README, `.gitignore` або ліцензії.

У терміналі відкрийте папку проєкту та виконайте:

```bash
git init
git branch -M main
git add .
git status
git commit -m "Initial Telegram bot"
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

Перед `git commit` обов'язково перевірте `git status`.
У списку не повинно бути:

- `.env`;
- `user_scores.db`;
- файлів `*.db`, `*.sqlite`, `*.sqlite3`;
- `.venv` або `venv`;
- `__pycache__`.

## Railway

1. Створіть новий Railway Project.
2. Оберіть **Deploy from GitHub repo**.
3. Підключіть цей репозиторій.
4. У вкладці **Variables** додайте:

```text
BOT_TOKEN=ваш_токен
SCORE_DB_PATH=/data/user_scores.db
```

5. Додайте до сервісу Railway Volume.
6. Mount path для Volume:

```text
/data
```

7. Переконайтеся, що запущена тільки одна replica сервісу.
8. Публічний домен цьому polling-боту не потрібний.

`railway.json` уже задає:

```text
python bot.py
```

і політику автоматичного перезапуску.

## Чому потрібен Volume

Railway-контейнер має тимчасову файлову систему.
Без Volume файл SQLite може зникнути після redeploy або restart.
Volume на `/data` зберігає `user_scores.db` між деплоями.

## Оновлення

Після змін:

```bash
git add .
git status
git commit -m "Describe changes"
git push
```

Railway автоматично запустить новий deployment із підключеної
гілки GitHub.

import os

from dotenv import load_dotenv


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "Не знайдено BOT_TOKEN. "
        "Створіть файл .env на основі .env.example."
    )

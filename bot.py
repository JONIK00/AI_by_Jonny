import os
import asyncio
import aiohttp
import time
import logging
import re
import html
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

# === Конфигурация ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL = "deepseek/deepseek-r1-0528:free"

DELAY_TIMER = 15  # Сколько секунд идёт таймер для пользователя
DELAY_AI = 10     # Через сколько секунд реально начинать запрос к ИИ

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

chat_history = {}
user_busy = {}
last_request_time = {}

# === Очистка HTML для Telegram ===
def sanitize_for_telegram_html(text: str) -> str:
    allowed_tags = ['b', 'i', 'u', 'code', 'pre', 'a']

    # Убираем все теги, кроме разрешённых
    for tag in re.findall(r'<(/?)(\w+)[^>]*>', text):
        if tag[1].lower() not in allowed_tags:
            text = re.sub(rf'</?{tag[1]}[^>]*>', '', text, flags=re.IGNORECASE)

    # Разрешаем только href в <a>
    text = re.sub(r'<a\s+[^>]*href=["\'](.*?)["\'][^>]*>', r'<a href="\1">', text, flags=re.IGNORECASE)

    # <br> → перенос строки
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)

    # Списки → буллеты
    text = re.sub(r'(?i)<li\s*>', '• ', text)
    text = re.sub(r'(?i)</li\s*>', '\n', text)
    text = re.sub(r'(?i)</?(ul|ol)\s*>', '', text)

    # Экранируем, восстанавливаем разрешённые теги
    text = html.escape(text)
    for tag in allowed_tags:
        text = text.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    text = re.sub(r'&lt;a href="(.*?)"&gt;', r'<a href="\1">', text)
    text = text.replace("&lt;/a&gt;", "</a>")

    # Лишние пустые строки
    text = re.sub(r'\n\s*\n', '\n\n', text).strip()

    return text

# === Генерация ответа через OpenRouter ===
async def generate_response_openrouter(user_id: int, user_message: str) -> str:
    if user_id not in chat_history:
        chat_history[user_id] = []

    if not any(m.get("role") == "system" for m in chat_history[user_id]):
        chat_history[user_id].insert(0, {
            "role": "system",
            "content": (
                "Форматируй ответ в HTML для Telegram. Разрешённые теги: "
                "<b>, <i>, <u>, <code>, <pre>, <a href=\"...\">. "
                "Не используй <ul>/<li>/Markdown. Для списков используй буллеты '• ' или нумерацию '1.'."
            )
        })

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": chat_history[user_id]
    }

    await asyncio.sleep(DELAY_AI)

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                data = await resp.json()

                if resp.status != 200:
                    err_msg = data.get("error", {}).get("message", "Неизвестная ошибка")
                    return f"⚠️ Ошибка: {err_msg} (код {resp.status})"

                bot_reply = data["choices"][0]["message"]["content"]
                chat_history[user_id].append({"role": "assistant", "content": bot_reply})
                return bot_reply
    except Exception as e:
        return f"⚠️ Ошибка: {e}"

# === Обработка сообщений ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()

    if not text:
        return

    if user_busy.get(user_id):
        await update.message.reply_text("⏳ Подождите, идёт генерация ответа.")
        return

    last = last_request_time.get(user_id)
    now = time.time()
    if last and now - last < DELAY_TIMER:
        await update.message.reply_text(f"⏳ Подождите ещё {int(DELAY_TIMER - (now - last))} сек.")
        return

    user_busy[user_id] = True
    last_request_time[user_id] = now

    chat_history.setdefault(user_id, []).append({"role": "user", "content": text})

    timer_msg = await update.message.reply_text(f"⌛ Генерация ответа... ({DELAY_TIMER} сек)")

    async def timer():
        for rem in range(DELAY_TIMER, 0, -1):
            try:
                await timer_msg.edit_text(f"⌛ Генерация ответа... ({rem} сек)")
            except:
                pass
            await asyncio.sleep(1)

    async def gen():
        return await generate_response_openrouter(user_id, text)

    timer_task = asyncio.create_task(timer())
    gen_task = asyncio.create_task(gen())

    await asyncio.wait({timer_task, gen_task})

    reply_raw = gen_task.result()

    try:
        await timer_msg.delete()
    except:
        pass

    reply_html = sanitize_for_telegram_html(reply_raw)
    await update.message.reply_text(reply_html, parse_mode="HTML", disable_web_page_preview=True)

    user_busy[user_id] = False

# === Команды ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("<b>Привет! 😊</b>\nЯ — DeepSeek by Jonny. Напиши что-нибудь.", parse_mode="HTML")

def main():
    print("✅ Бот запущен")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    try:
        app.run_polling()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        print("🛑 Бот остановлен")

if __name__ == "__main__":
    main()

import os
import io
import sys
import time
import subprocess

# --- Авто-установка зависимостей ---
def install_dependencies():
    print("Установка необходимых библиотек...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyTelegramBotAPI", "pillow", "google-generativeai"])

try:
    import telebot
    import google.generativeai as genai
    from PIL import Image
except ImportError:
    install_dependencies()
    import telebot
    import google.generativeai as genai
    from PIL import Image
# -----------------------------------

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Критическая ошибка: Не найдены ключи в переменных окружения.")

genai.configure(api_key=GEMINI_API_KEY)

# Жестко задаем требуемую сервером модель
model = genai.GenerativeModel('gemini-3.8-flash')

bot = telebot.TeleBot(TELEGRAM_TOKEN)

SYSTEM_PROMPT = """
Проанализируй фото визитной карточки. Извлеки все контактные данные и расположи их в структурированном виде:
- 👤 **ФИО:** 
- 🏢 **Компания и должность:** 
- 📞 **Телефон:** 
- ✉️ **Email:** 
- 🌐 **Сайт:** 
- 📍 **Адрес:** 

Если какой-то параметр отсутствует на визитке, просто пропусти его. Ответ давай кратко, без вводных фраз.
"""

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Привет! Отправь мне фотографию визитки, и я распознаю все контактные данные.")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing')
        
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        image = Image.open(io.BytesIO(downloaded_file))
        
        response = model.generate_content([SYSTEM_PROMPT, image])
        
        if response.text:
            bot.reply_to(message, response.text)
        else:
            bot.reply_to(message, "Не удалось прочитать данные с визитки.")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка обработки: {e}")

if __name__ == '__main__':
    print("Бот запускается...")
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            print(f"Сбой соединения: {e}. Перезапуск через 5 секунд...")
            time.sleep(5)

import os
import sys
import time
import subprocess

# --- БРОНЕБОЙНЫЙ БЛОК: Принудительная установка библиотек ---
# Если Amvera закэшировала старую сборку, скрипт установит всё сам
def install_dependencies():
    print("Внимание: Библиотеки не найдены. Принудительная установка...")
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
# -------------------------------------------------------------

# 1. Получение ключей
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Критическая ошибка: Не найдены ключи в переменных окружения.")

# 2. Настройка Gemini API (стабильная модель)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

# 3. Инициализация Telegram бота
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
        
        # Запись во временный файл для надежности
        temp_path = "temp_card.jpg"
        with open(temp_path, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        image = Image.open(temp_path)
        response = model.generate_content([SYSTEM_PROMPT, image])
        
        if response.text:
            bot.reply_to(message, response.text)
        else:
            bot.reply_to(message, "Не удалось прочитать данные с визитки.")
            
        os.remove(temp_path) # Убираем за собой
            
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

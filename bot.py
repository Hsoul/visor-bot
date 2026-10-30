import os
import io
import telebot
from PIL import Image
import google.generativeai as genai

# Получение ключей из переменных окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("Не найдены TELEGRAM_TOKEN или GEMINI_API_KEY в переменных окружения.")

# Настройка Gemini API
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-3.8-flash')

# Инициализация Telegram бота
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
        
        # Получаем файл с наивысшим разрешением
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Открываем изображение для Gemini
        image = Image.open(io.BytesIO(downloaded_file))
        
        # Генерация ответа
        response = model.generate_content([SYSTEM_PROMPT, image])
        
        if response.text:
            bot.reply_to(message, response.text)
        else:
            bot.reply_to(message, "Не удалось прочитать данные с визитки.")
            
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка обработки: {e}")

if __name__ == '__main__':
    print("Бот успешно запущен и ожидает сообщений...")
    bot.polling(none_stop=True)

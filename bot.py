import os
import io
import json
import base64
from PIL import Image
import telebot
from google import genai

# Получаем токены из переменных окружения облачного сервиса
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Инструкция для нейросети по разбору визитки и поиску логотипа
SYSTEM_PROMPT = """
Ты — эксперт по распознаванию визитных карточек.
Проанализируй изображение визитки и верни JSON со следующими полями:
- fn: Полное имя человека (ФИО)
- org: Название компании
- title: Должность
- tel: Номер телефона в международном формате (например, +79001234567)
- logo_box: Массив из 4 чисел [ymin, xmin, ymax, xmax] в нормализованных координатах 0..1000 для области с логотипом компании. Если логотипа нет, верни null.

Верни ТОЛЬКО чистый JSON без дополнительных символов и markdown-разметки.
"""

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "Привет, Владимир! Отправь мне фото визитки (можно с комментарием в подписи), и я сформирую vCard контакт для iPhone.")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    status_msg = bot.reply_to(message, "⏳ Распознаю визитку и выделяю логотип...")
    
    try:
        # Загружаем фото из Telegram в память
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        img_bytes = io.BytesIO(downloaded_file)
        pil_img = Image.open(img_bytes)

        # Отправляем запрос в Gemini Flash
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[SYSTEM_PROMPT, pil_img]
        )
        
        # Парсим JSON-ответ от модели
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)

        # Если найден логотип — кадрируем его через Pillow и кодируем в Base64
        logo_base64 = ""
        logo_box = data.get("logo_box")
        if logo_box and len(logo_box) == 4:
            width, height = pil_img.size
            ymin, xmin, ymax, xmax = logo_box
            
            crop_box = (
                int((xmin / 1000) * width),
                int((ymin / 1000) * height),
                int((xmax / 1000) * width),
                int((ymax / 1000) * height)
            )
            logo_img = pil_img.crop(crop_box)
            
            buffered = io.BytesIO()
            logo_img.save(buffered, format="JPEG")
            logo_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

        # Забираем текст из подписи к фото (если есть) для поля «Заметки»
        user_notes = message.caption if message.caption else ""

        # Собираем файл vCard 3.0 под стандарты iOS
        vcard_lines = [
            "BEGIN:VCARD",
            "VERSION:3.0",
            f"FN:{data.get('fn', 'Без имени')}",
            f"ORG:{data.get('org', '')}",
            f"TITLE:{data.get('title', '')}",
            f"TEL;TYPE=CELL:{data.get('tel', '')}"
        ]
        
        if user_notes:
            vcard_lines.append(f"NOTE:{user_notes}")
            
        if logo_base64:
            vcard_lines.append(f"PHOTO;ENCODING=b;TYPE=JPEG:{logo_base64}")
            
        vcard_lines.append("END:VCARD")
        
        vcard_content = "\n".join(vcard_lines)
        
        # Отправляем готовый .vcf файл пользователю
        contact_name = data.get('fn', 'Contact').replace(' ', '_')
        vcf_file = io.BytesIO(vcard_content.encode('utf-8'))
        vcf_file.name = f"{contact_name}.vcf"
        
        bot.delete_message(message.chat.id, status_msg.message_id)
        bot.send_document(message.chat.id, vcf_file, caption=f"✅ Контакт **{data.get('fn')}** готов к сохранению!")

    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка обработки: {str(e)}", message.chat.id, status_msg.message_id)

if __name__ == "__main__":
    bot.infinity_polling()

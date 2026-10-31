import base64
import io
import logging
import os
from openai import OpenAI
from PIL import Image
from pillow_heif import register_heif_opener
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Регистрируем поддержку HEIC файлов с iPhone
register_heif_opener()

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# Переменные окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Инициализация клиента OpenRouter (совместим с OpenAI)
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


def convert_image_to_jpeg_base64(image_bytes: bytes) -> str:
  """Конвертирует любое фото (включая HEIC) в JPEG и переводит в Base64."""
  image = Image.open(io.BytesIO(image_bytes))
  if image.mode != "RGB":
    image = image.convert("RGB")

  output_buffer = io.BytesIO()
  image.save(output_buffer, format="JPEG", quality=95)
  jpeg_bytes = output_buffer.getvalue()

  return base64.b64encode(jpeg_bytes).decode("utf-8")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      "Привет, Владимир! Отправь мне фотографию визитки (как фото или файл), "
      "и я зафиксирую все контактные данные и логотип."
  )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
  status_msg = await update.message.reply_text("Обрабатываю визитку через Qwen...")

  try:
    # Получаем файл из фото или документа
    if update.message.photo:
      file_id = update.message.photo[-1].file_id
    elif update.message.document:
      file_id = update.message.document.file_id
    else:
      await status_msg.edit_text("Пожалуйста, отправьте изображение.")
      return

    telegram_file = await context.bot.get_file(file_id)
    image_bytes = await telegram_file.download_as_bytearray()

    # Конвертация в JPEG Base64
    base64_image = convert_image_to_jpeg_base64(bytes(image_bytes))

    prompt = (
        "Считай все данные с этой визитки и выведи их в четком структурированном"
        " виде:\n"
        "- 👤 **ФИО:**\n"
        "- 🏢 **Компания и должность:**\n"
        "- 🎨 **Логотип / Эмблема:** (Опиши графический символ, знак или текст на"
        " логотипе)\n"
        "- 📞 **Телефон:**\n"
        "- ✉️ **Email:**\n"
        "- 🌐 **Сайт:**\n"
        "- 📍 **Адрес:**"
    )

    # Запрос к Qwen2.5-VL через OpenRouter
    response = client.chat.completions.create(
        model="qwen/qwen-2.5-vl-7b-instruct",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{base64_image}"
                    },
                },
            ],
        }],
    )

    result_text = response.choices[0].message.content
    await status_msg.edit_text(result_text, parse_mode="Markdown")

  except Exception as e:
    logging.error(f"Ошибка: {e}")
    await status_msg.edit_text(f"Произошла ошибка при обработке: {e}")


def main():
  app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

  app.add_handler(CommandHandler("start", start))
  app.add_handler(
      MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo)
  )

  logging.info("Бот запущен...")
  app.run_polling()


if __name__ == "__main__":
  main()

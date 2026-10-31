import base64
import io
import json
import logging
import os
import re
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

# Поддержка HEIC с iPhone
register_heif_opener()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


def convert_image_to_jpeg_base64(image_bytes: bytes) -> str:
  """Конвертирует входной файл (включая HEIC) в JPEG и переводит в Base64."""
  image = Image.open(io.BytesIO(image_bytes))
  if image.mode != "RGB":
    image = image.convert("RGB")
  output_buffer = io.BytesIO()
  image.save(output_buffer, format="JPEG", quality=95)
  return base64.b64encode(output_buffer.getvalue()).decode("utf-8")


def generate_vcard(data: dict, user_note: str = "") -> str:
  """Формирует чистый vCard (версия 3.0) без фото/аватарок."""
  fn = data.get("full_name", "Неизвестный контакт")
  last_name = data.get("last_name", "")
  first_name = data.get("first_name", "")
  middle_name = data.get("middle_name", "")

  org = data.get("company", "")
  title = data.get("position", "")
  phones = data.get("phones", [])
  email = data.get("email", "")
  site = data.get("website", "")
  address = data.get("address", "")

  vcard_lines = [
      "BEGIN:VCARD",
      "VERSION:3.0",
      f"N:{last_name};{first_name};{middle_name};;",
      f"FN:{fn}",
  ]

  if org:
    vcard_lines.append(f"ORG:{org}")
  if title:
    vcard_lines.append(f"TITLE:{title}")

  for phone in phones:
    vcard_lines.append(f"TEL;TYPE=CELL:{phone}")

  if email:
    vcard_lines.append(f"EMAIL:{email}")
  if site:
    vcard_lines.append(f"URL:{site}")
  if address:
    vcard_lines.append(f"ADR:;;{address};;;;")

  # В заметку попадает ТОЛЬКО пользовательский комментарий
  if user_note:
    vcard_lines.append(f"NOTE:{user_note}")

  vcard_lines.append("END:VCARD")
  return "\n".join(vcard_lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      "Привет, Владимир! Отправь мне фотографию визитки. Вы можете добавить"
      " подпись к фото с комментарием — я сожму его в заметку и сформирую"
      " файл контакта .vcf для iPhone!"
  )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
  status_msg = await update.message.reply_text(
      "Распознаю визитку и создаю файл контакта..."
  )

  try:
    if update.message.photo:
      file_id = update.message.photo[-1].file_id
    elif update.message.document:
      file_id = update.message.document.file_id
    else:
      await status_msg.edit_text("Пожалуйста, отправьте изображение.")
      return

    user_caption = update.message.caption or ""

    telegram_file = await context.bot.get_file(file_id)
    image_bytes = await telegram_file.download_as_bytearray()
    base64_image = convert_image_to_jpeg_base64(bytes(image_bytes))

    prompt = f"""Считай данные с визитки и верни СТРОГО чистый JSON без разметки markdown (без ```json).

Структура JSON:
{{
  "full_name": "ФИО целиком",
  "first_name": "Имя",
  "last_name": "Фамилия",
  "middle_name": "Отчество (если есть)",
  "company": "Название компании или ведомства",
  "position": "Должность",
  "phones": ["телефон 1", "телефон 2"],
  "email": "email",
  "website": "сайт",
  "address": "адрес",
  "logo_description": "Краткое описание логотипа/символа (если есть)",
  "summary_note": "Если передан комментарий пользователя, сожми его до 1 короткого предложения. Если комментария нет, оставь пустым"
}}

Комментарий пользователя: "{user_caption}"
"""

    response = client.chat.completions.create(
        model="qwen/qwen2.5-vl-72b-instruct",
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
        max_tokens=1500,
    )

    raw_json = response.choices[0].message.content.strip()
    raw_json = re.sub(r"^```json\s*", "", raw_json)
    raw_json = re.sub(r"\s*```$", "", raw_json)

    data = json.loads(raw_json)

    phones_str = "\n".join(
        [f"  - {p}" for p in data.get("phones", [])]
    ) or "Не указан"
    summary_note = data.get("summary_note", "")

    msg_text = (
        f"👤 **ФИО:** {data.get('full_name', '-')}\n"
        f"🏢 **Компания:** {data.get('company', '-')}\n"
        f"💼 **Должность:** {data.get('position', '-')}\n"
        f"🎨 **Логотип:** {data.get('logo_description', '-')}\n"
        f"📞 **Телефоны:**\n{phones_str}\n"
        f"✉️ **Email:** {data.get('email', '-')}\n"
        f"🌐 **Сайт:** {data.get('website', '-')}\n"
        f"📍 **Адрес:** {data.get('address', '-')}\n"
    )
    if summary_note:
      msg_text += f"📝 **Заметка:** {summary_note}\n"

    await status_msg.edit_text(msg_text, parse_mode="Markdown")

    vcard_str = generate_vcard(data, user_note=summary_note)
    vcard_bytes = vcard_str.encode("utf-8")

    filename = (
        f"{data.get('last_name', 'contact')}_{data.get('first_name', 'card')}.vcf"
    )
    vcard_file = io.BytesIO(vcard_bytes)
    vcard_file.name = filename

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=vcard_file,
        caption="🎴 Карточка контакта сформирована! Нажмите и сохраните в «Контакты».",
    )

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

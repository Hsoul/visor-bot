import base64
import io
import json
import logging
import os
import re
from openai import OpenAI
from PIL import Image, ImageOps
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


def load_and_fix_orientation(image_bytes: bytes) -> Image.Image:
  """Открывает изображение и правильно поворачивает его согласно EXIF метаданным iPhone."""
  img = Image.open(io.BytesIO(image_bytes))
  img = ImageOps.exif_transpose(img)
  if img.mode != "RGB":
    img = img.convert("RGB")
  return img


def convert_image_to_jpeg_bytes(image_bytes: bytes) -> bytes:
  """Конвертирует входное фото в стандартные байты JPEG с учетом поворота."""
  img = load_and_fix_orientation(image_bytes)
  output_buffer = io.BytesIO()
  img.save(output_buffer, format="JPEG", quality=95)
  return output_buffer.getvalue()


def crop_logo_base64(jpeg_bytes: bytes, bbox: list) -> str:
  """Вырезает логотип по координатам bbox [ymin, xmin, ymax, xmax] (0..1000)

  с учетом EXIF-ориентации и небольшим запасом.
  """
  if not bbox or len(bbox) != 4:
    return ""
  try:
    img = Image.open(io.BytesIO(jpeg_bytes))
    w, h = img.size
    ymin, xmin, ymax, xmax = bbox

    # Добавляем 5% запаса (padding) вокруг логотипа, чтобы не резать впритык
    pad_h = int((ymax - ymin) * 0.05)
    pad_w = int((xmax - xmin) * 0.05)

    ymin = max(0, ymin - pad_h)
    xmin = max(0, xmin - pad_w)
    ymax = min(1000, ymax + pad_h)
    xmax = min(1000, xmax + pad_w)

    # Перевод координат 0..1000 в реальные пиксели
    left = max(0, int(xmin * w / 1000))
    top = max(0, int(ymin * h / 1000))
    right = min(w, int(xmax * w / 1000))
    bottom = min(h, int(ymax * h / 1000))

    if right <= left or bottom <= top:
      return ""

    cropped = img.crop((left, top, right, bottom))
    # Оптимизируем размер аватарки
    cropped.thumbnail((400, 400))

    buf = io.BytesIO()
    cropped.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")
  except Exception as e:
    logging.error(f"Ошибка при вырезании логотипа: {e}")
    return ""


def generate_vcard(
    data: dict, user_note: str = "", logo_base64: str = ""
) -> str:
  """Формирует vCard (версия 3.0) с аватаркой логотипа для iOS."""
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

  if user_note:
    vcard_lines.append(f"NOTE:{user_note}")

  if logo_base64:
    vcard_lines.append(f"PHOTO;TYPE=JPEG;ENCODING=b:{logo_base64}")

  vcard_lines.append("END:VCARD")
  return "\n".join(vcard_lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
  await update.message.reply_text(
      "Привет, Владимир! Отправь мне фотографию визитки. Я создам контакт .vcf"
      " с верным логотипом и заметками!"
  )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
  status_msg = await update.message.reply_text(
      "Распознаю визитку, определяю профильный логотип и создаю файл"
      " контакта..."
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
    raw_bytes = await telegram_file.download_as_bytearray()

    # Поворачиваем изображение по EXIF и переводим в JPEG
    jpeg_bytes = convert_image_to_jpeg_bytes(bytes(raw_bytes))
    base64_image = base64.b64encode(jpeg_bytes).decode("utf-8")

    prompt = f"""Считай данные с визитки и верни СТРОГО чистый JSON без разметки markdown (без ```json).

ПРАВИЛА ОПРЕДЕЛЕНИЯ ЛОГОТИПА (logo_bbox):
1. ИГНОРИРУЙ QR-КОДЫ И ШТРИХ-КОДЫ.
2. Если на визитке есть общая символика (например, Герб города/субъекта слева) и профильная эмблема службы/ведомства/компании (например, Эмблема Гостехнадзора справа), выбери ПРОФИЛЬНУЮ ЭМБЛЕМУ ВЕДОМСТВА/КОМПАНИИ.
3. Верни рамку выбранной эмблемы [ymin, xmin, ymax, xmax] по шкале 0..1000 строго вокруг самой эмблемы.

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
  "logo_description": "Краткое описание выбранной эмблемы",
  "logo_bbox": [ymin, xmin, ymax, xmax],
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
    logo_bbox = data.get("logo_bbox")

    # Вырезаем выверенную эмблему с автоповоротом по EXIF
    logo_base64 = crop_logo_base64(jpeg_bytes, logo_bbox)

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

    vcard_str = generate_vcard(
        data, user_note=summary_note, logo_base64=logo_base64
    )
    vcard_bytes = vcard_str.encode("utf-8")

    filename = (
        f"{data.get('last_name', 'contact')}_{data.get('first_name', 'card')}.vcf"
    )
    vcard_file = io.BytesIO(vcard_bytes)
    vcard_file.name = filename

    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=vcard_file,
        caption="🎴 Карточка контакта сформирована!",
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

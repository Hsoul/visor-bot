import asyncio
import base64
import html
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

# Поддержка HEIC формата с iPhone
register_heif_opener()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Инициализация клиента OpenAI с увеличенным сетевым таймаутом (60 секунд)
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
    timeout=60.0,
    default_headers={
        "HTTP-Referer": "https://amvera.ru",
        "X-Title": "VisorJamBot",
    },
)


def normalize_phone(phone: str) -> str:
    """Приводит российский номер телефона к стандарту +7 (XXX) XXX-XX-XX.

    - Если номер 7-значный (городской СПб) -> автоматически добавляет 7812.
    - Исправляет дубли префиксов.
    """
    if not phone:
        return ""

    digits = re.sub(r"\D", "", phone)

    if len(digits) == 7:
        digits = "7812" + digits

    if len(digits) > 11 and (
        digits.startswith("7981931") or digits.startswith("8981931")
    ):
        digits = "7931" + digits[7:]

    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) > 11:
        digits = "7" + digits[-10:]

    if len(digits) == 11 and digits.startswith("7"):
        return f"+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"

    return f"+{digits}" if digits else phone


def convert_image_to_jpeg_base64(image_bytes: bytes, max_size: int = 1024) -> str:
    """Оптимизирует фото: поворачивает по EXIF, ужимает до 1024px и сжимает JPEG."""
    image = Image.open(io.BytesIO(image_bytes))

    # Корректный поворот ориентации кадра с iPhone
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass

    if image.mode != "RGB":
        image = image.convert("RGB")

    # Уменьшение разрешения для защиты от сетевых обрывов
    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

    output_buffer = io.BytesIO()
    image.save(output_buffer, format="JPEG", quality=85, optimize=True)
    return base64.b64encode(output_buffer.getvalue()).decode("utf-8")


def generate_vcard(data: dict, user_note: str = "") -> str:
    """Формирует vCard контакта (версия 3.0) для сохранения в iPhone."""
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
        norm_p = normalize_phone(phone)
        if norm_p:
            vcard_lines.append(f"TEL;TYPE=CELL:{norm_p}")

    if email:
        vcard_lines.append(f"EMAIL:{email}")
    if site:
        vcard_lines.append(f"URL:{site}")
    if address:
        vcard_lines.append(f"ADR:;;{address};;;;")

    if user_note:
        vcard_lines.append(f"NOTE:{user_note}")

    vcard_lines.append("END:VCARD")
    return "\n".join(vcard_lines)


def safe_html(text: str) -> str:
    """Экранирует спецсимволы HTML для Telegram."""
    return html.escape(str(text or "-"))


def call_openrouter_api(
    prompt: str, base64_image: str = None, text_content: str = None
):
    """Каскадный вызов надёжных Vision-моделей в OpenRouter."""
    content_payload = [{"type": "text", "text": prompt}]

    if base64_image:
        content_payload.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
        })
    elif text_content:
        content_payload.append(
            {"type": "text", "text": f"\n\nТЕКСТ ДЛЯ РАЗБОРА:\n{text_content}"}
        )

    # Список актуальных и проверенных моделей с поддержкой изображений
    candidate_models = [
        "google/gemini-2.0-flash-001",
        "qwen/qwen-2.5-vl-7b-instruct",
        "qwen/qwen-2.5-vl-72b-instruct",
    ]

    last_exception = None
    for model_id in candidate_models:
        try:
            logging.info(f"Отправка запроса в OpenRouter (модель: {model_id})...")
            response = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": content_payload}],
            )
            logging.info(f"Успешный ответ от модели: {model_id}")
            return response
        except Exception as e:
            logging.warning(
                f"Ошибка вызова модели {model_id}: {e}. Переходим к резервной..."
            )
            last_exception = e

    raise last_exception


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет, Владимир! Отправь мне фотографию визитки ИЛИ просто скопированный"
        " текст с контактами. Я сформирую готовый файл контакта .vcf для iPhone!"
    )


async def process_contact_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    base64_image: str = None,
    raw_text_input: str = None,
    user_caption: str = "",
):
    """Единый обработчик распознавания визитки."""
    status_msg = await update.message.reply_text(
        "Распознаю контактные данные и создаю файл контакта..."
    )

    try:
        prompt = f"""Считай контактные данные и верни СТРОГО чистый JSON без разметки markdown (без ```json).

ПРАВИЛА ДЛЯ ТЕЛЕФОНОВ:
1. Считывай телефонные номера СТРОГО по факту данных, не придумывай и не добавляй лишние цифры!
2. В массив "phones" включай все найденные номера.

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
  "summary_note": "Если передан комментарий пользователя, сожми его до 1 короткого предложения. Если комментария нет, оставь пустым"
}}

Комментарий пользователя: "{user_caption}"
"""

        response = await asyncio.to_thread(
            call_openrouter_api,
            prompt,
            base64_image=base64_image,
            text_content=raw_text_input,
        )

        raw_json = response.choices[0].message.content.strip()
        raw_json = re.sub(r"^```json\s*", "", raw_json)
        raw_json = re.sub(r"\s*```$", "", raw_json)

        data = json.loads(raw_json)

        raw_phones = data.get("phones", [])
        normalized_phones = [
            normalize_phone(p) for p in raw_phones if normalize_phone(p)
        ]
        data["phones"] = normalized_phones

        phones_str = (
            "\n".join([f"  - {safe_html(p)}" for p in normalized_phones])
            if normalized_phones
            else "Не указан"
        )
        summary_note = data.get("summary_note", "")

        msg_text = (
            f"👤 <b>ФИО:</b> {safe_html(data.get('full_name'))}\n"
            f"🏢 <b>Компания:</b> {safe_html(data.get('company'))}\n"
            f"💼 <b>Должность:</b> {safe_html(data.get('position'))}\n"
            f"📞 <b>Телефоны:</b>\n{phones_str}\n"
            f"✉️ <b>Email:</b> {safe_html(data.get('email'))}\n"
            f"🌐 <b>Сайт:</b> {safe_html(data.get('website'))}\n"
            f"📍 <b>Адрес:</b> {safe_html(data.get('address'))}\n"
        )
        if summary_note:
            msg_text += f"📝 <b>Заметка:</b> {safe_html(summary_note)}\n"

        await status_msg.edit_text(msg_text, parse_mode="HTML")

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
            caption=(
                "🎴 Карточка контакта сформирована! Нажмите и сохраните в"
                " «Контакты»."
            ),
        )

    except Exception as e:
        err_str = str(e)
        logging.error(f"Ошибка при обработке: {err_str}")
        if "402" in err_str:
            await status_msg.edit_text(
                "💳 Закончился лимит на балансе OpenRouter. Пополните счет на openrouter.ai."
            )
        elif "429" in err_str:
            await status_msg.edit_text(
                "⏳ Сервер перегружен. Подождите 10 секунд и отправьте визитку еще раз."
            )
        else:
            await status_msg.edit_text(
                f"Произошла ошибка при обработке: {safe_html(err_str)}"
            )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        return

    user_caption = update.message.caption or ""
    telegram_file = await context.bot.get_file(file_id)
    image_bytes = await telegram_file.download_as_bytearray()
    
    # Сжатие и ориентация снимка в памяти перед отправкой
    base64_image = convert_image_to_jpeg_base64(bytes(image_bytes))

    await process_contact_request(
        update, context, base64_image=base64_image, user_caption=user_caption
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_input = update.message.text or ""
    await process_contact_request(update, context, raw_text_input=text_input)


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo)
    )
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    logging.info("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()

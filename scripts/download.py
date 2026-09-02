"""
دانلود فایل از یک پیام تلگرام با استفاده از Telethon.

دو حالت پشتیبانی می‌شود:

۱) حالت خودکار (از طریق بات، وقتی TELEGRAM_BOT_TOKEN و TELEGRAM_USER_CHAT_ID
   ست شده باشند): با توکن خود بات لاگین می‌کند و مستقیم پیامی که کاربر به بات
   فرستاده را می‌خواند. (شماره پیام و چت دقیقاً همانی است که بات دریافت کرده،
   پس هیچ عدم‌تطابقی وجود ندارد و محدودیت ۲۰ مگابایتی Bot API HTTP هم دور زده
   می‌شود چون Telethon مستقیم روی MTProto کار می‌کند.)

۲) حالت دستی (از طریق اجرای مستقیم workflow با TELEGRAM_SESSION اکانت شخصی):
   ورودی MESSAGE_LINK می‌تواند یکی از این دو حالت باشد:
     - فقط آیدی عددی پیام (مثل 123) → از TELEGRAM_CHANNEL_ID به‌عنوان چت استفاده می‌شود
     - لینک کامل پیام (مثل https://t.me/channel_username/123
       یا https://t.me/c/1234567890/123) → چت از خود لینک استخراج می‌شود
"""

import os
import re
import sys
import asyncio
from telethon import TelegramClient, utils
from telethon.sessions import StringSession
from telethon.tl import types, functions

API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
USER_CHAT_ID = os.environ.get("TELEGRAM_USER_CHAT_ID", "").strip()
SESSION = os.environ.get("TELEGRAM_SESSION", "").strip()
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
MESSAGE_LINK = os.environ["MESSAGE_LINK"].strip()
PARALLEL_CONNECTIONS = int(os.environ.get("PARALLEL_CONNECTIONS", "8"))

DOWNLOAD_DIR = "downloads"
BOT_MODE = bool(BOT_TOKEN and USER_CHAT_ID)
CHUNK_SIZE = 512 * 1024  # باید مضرب ۴۰۹۶ باشه؛ این محدودیت خود تلگرامه

if not BOT_MODE and (not SESSION or len(SESSION) < 50):
    print("::error::مقدار Secret به نام TELEGRAM_SESSION خالی یا نامعتبر است. "
          "دوباره scripts/generate_session.py را اجرا کنید و کل رشته خروجی را "
          "بدون فاصله یا کوتیشن اضافه در Secret جایگذاری کنید.")
    sys.exit(1)


def resolve_chat_and_msg(value: str):
    # فقط عدد یعنی آیدی پیام؛ چت از TELEGRAM_CHANNEL_ID گرفته می‌شود
    if value.isdigit():
        if not CHANNEL_ID:
            raise ValueError("TELEGRAM_CHANNEL_ID تنظیم نشده و ورودی فقط آیدی پیام است.")
        chat = int(CHANNEL_ID) if re.fullmatch(r"-?\d+", CHANNEL_ID) else CHANNEL_ID
        return chat, int(value)

    # فرمت خصوصی: https://t.me/c/1234567890/123
    m = re.search(r"t\.me/c/(\d+)/(\d+)", value)
    if m:
        chat_id = int("-100" + m.group(1))
        msg_id = int(m.group(2))
        return chat_id, msg_id

    # فرمت عمومی: https://t.me/username/123
    m = re.search(r"t\.me/([^/]+)/(\d+)", value)
    if m:
        username = m.group(1)
        msg_id = int(m.group(2))
        return username, msg_id

    raise ValueError(f"مقدار MESSAGE_LINK قابل تشخیص نیست: {value}")


async def download_parallel(client, document, dest_path, progress_cb=None):
    """
    دانلود موازی با چند اتصال هم‌زمان به همون Data Center تلگرام.
    تلگرام سرعت هر اتصال تکی رو محدود می‌کنه (همون ~۱ مگ/ثانیه)، ولی این
    محدودیت روی هر اتصال جداست؛ با باز کردن چند اتصال هم‌زمان (پیش‌فرض ۸ تا)
    می‌شه به مجموع سرعت بسیار بالاتری رسید.
    نکته: از متدهای نیمه‌داخلی (private) کتابخونه Telethon استفاده می‌کنه که
    ممکنه در آپدیت‌های بعدی این کتابخونه تغییر کنه؛ برای همین نسخه Telethon
    توی requirements.txt پین شده.
    """
    total_size = document.size
    dc_id = document.dc_id
    location = types.InputDocumentFileLocation(
        id=document.id,
        access_hash=document.access_hash,
        file_reference=document.file_reference,
        thumb_size="",
    )

    num_parts = (total_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    downloaded = [0]
    lock = asyncio.Lock()

    with open(dest_path, "wb") as f:
        f.truncate(total_size)

    same_dc = dc_id == client.session.dc_id

    async def get_sender():
        if same_dc:
            # نمی‌شه برای همون DC ای که کلاینت اصلی الان بهش وصله، یه اتصال
            # export جدا باز کرد؛ در این حالت از همون کانکشن اصلی استفاده می‌کنیم.
            return client._sender, False
        sender = await client._borrow_exported_sender(dc_id)
        return sender, True

    async def worker(worker_id):
        sender, exported = await get_sender()
        try:
            part = worker_id
            with open(dest_path, "r+b") as f:
                while part < num_parts:
                    offset = part * CHUNK_SIZE
                    remaining = total_size - offset
                    write_len = min(CHUNK_SIZE, remaining)

                    result = await sender.send(
                        functions.upload.GetFileRequest(
                            location=location, offset=offset, limit=CHUNK_SIZE
                        )
                    )
                    f.seek(offset)
                    f.write(result.bytes[:write_len])

                    async with lock:
                        downloaded[0] += write_len
                        if progress_cb:
                            progress_cb(downloaded[0], total_size)

                    part += PARALLEL_CONNECTIONS
        finally:
            if exported:
                await client._return_exported_sender(sender)

    await asyncio.gather(*(worker(i) for i in range(PARALLEL_CONNECTIONS)))


async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    if BOT_MODE:
        chat = int(USER_CHAT_ID)
        msg_id = int(MESSAGE_LINK)
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.start(bot_token=BOT_TOKEN)
    else:
        chat, msg_id = resolve_chat_and_msg(MESSAGE_LINK)
        client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
        await client.start()

    async with client:
        message = await client.get_messages(chat, ids=msg_id)
        if message is None or not message.file:
            print("::error::پیامی با فایل ضمیمه در این آدرس پیدا نشد.")
            sys.exit(1)

        total_mb = message.file.size / (1024 * 1024)
        print(f"در حال دانلود: {message.file.name or 'بدون‌نام'} ({total_mb:.2f} MB)")

        last_percent = [-10]

        def progress(current, total):
            percent = int(current * 100 / total)
            if percent >= last_percent[0] + 10:
                last_percent[0] = percent
                print(f"پیشرفت دانلود: {percent}% "
                      f"({current / (1024*1024):.1f} / {total_mb:.1f} MB)")

        file_name = message.file.name or f"file_{msg_id}"
        dest_path = os.path.join(DOWNLOAD_DIR, file_name)

        document = getattr(message, "document", None)

        if document is not None:
            print(f"دانلود موازی با {PARALLEL_CONNECTIONS} اتصال هم‌زمان...")
            try:
                await download_parallel(client, document, dest_path, progress_cb=progress)
                path = dest_path
            except Exception as e:
                print(f"::warning::دانلود موازی با خطا مواجه شد ({e})؛ "
                      f"بازگشت به دانلود عادی...")
                path = await message.download_media(
                    file=DOWNLOAD_DIR + "/", progress_callback=progress
                )
        else:
            # برای عکس و انواعی که Document نیستن، دانلود موازی معنی نداره
            path = await message.download_media(
                file=DOWNLOAD_DIR + "/", progress_callback=progress
            )

        if not path:
            print("::error::دانلود انجام نشد؛ مسیر فایل خروجی نداریم.")
            sys.exit(1)

        abs_path = os.path.abspath(path)
        size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        print(f"دانلود کامل شد: {abs_path} ({size_mb:.2f} MB)")

        # مسیر فایل رو برای مرحله بعدی workflow خروجی می‌دیم
        github_output = os.environ.get("GITHUB_OUTPUT")
        if not github_output:
            print("::error::متغیر GITHUB_OUTPUT موجود نیست؛ خروجی file_path ست نمی‌شود.")
            sys.exit(1)

        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"file_path={abs_path}\n")
        print(f"خروجی file_path با موفقیت روی GITHUB_OUTPUT نوشته شد: {abs_path}")


if __name__ == "__main__":
    asyncio.run(main())

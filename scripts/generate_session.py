"""
این اسکریپت رو فقط یک بار، روی سیستم خودتون (نه توی گیت‌هاب) اجرا کنید.
هدفش اینه که با اکانت تلگرام‌تون لاگین کنه و یک "Session String" بسازه.
این رشته رو بعداً به‌عنوان یک GitHub Secret به اسم TG_SESSION ذخیره می‌کنید
تا اکشن بدون نیاز به لاگین دوباره، به اکانت شما وصل بشه.

نصب پیش‌نیاز:
    pip install telethon

اجرا:
    python generate_session.py
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = int(input("API_ID خودتون رو از my.telegram.org وارد کنید: "))
API_HASH = input("API_HASH خودتون رو وارد کنید: ").strip()

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    session_string = client.session.save()
    print("\n" + "=" * 60)
    print("این Session String رو کپی کنید و توی GitHub Secret به اسم")
    print("TG_SESSION ذخیره کنید (این رو جایی به‌صورت عمومی منتشر نکنید):")
    print("=" * 60)
    print(session_string)
    print("=" * 60)

# SORO JARVIS

Telegram orqali AI yordamchi, ovozli xabar, xodimlar davomati, qarz va dastavka hisobini yuritadigan bot.

## O'rnatish

1. Python 3.11 yoki undan yangisini o'rnating.
2. Virtual muhit yarating: `python -m venv venv`.
3. Windows: `venv\Scripts\pip install -r requirements.txt`.
4. `.env.example` nusxasi asosida mavjud `.env` qiymatlarini to'ldiring.
5. Google Sheets ishlatilsa, service account JSON faylini `GOOGLE_CREDENTIALS_FILE` orqali ko'rsating.
6. `python -m jarvis_bot` bilan ishga tushiring.

Konfiguratsiyani maxfiy qiymatlarni ekranga chiqarmasdan tekshirish:
`python scripts/check_config.py`.

Mavjud `jarvis.db` birinchi ishga tushishda ma'lumotlarni o'chirmasdan migratsiya qilinadi.

## Tuzilma

- `jarvis_bot/` — Telegram ilovasi va barcha amaliy handlerlar.
- `jarvis_bot/database.py` — SQLite sxemasi, migratsiya va ma'lumot funksiyalari.
- `jarvis_database.py` — eski importlar uchun moslik fayli.
- `telegram_jarvis.py` — eski buyruqlar bilan mos kirish nuqtasi.
- `projects/` — mustaqil veb loyihalar.
- `archive/legacy/` — ishlatilmaydigan eski nusxalar.
- `deploy/` — server uchun xizmat konfiguratsiyasi.

## Serverda ishga tushirish

`deploy/soro-jarvis.service` ichidagi `User` va `/opt/soro-jarvis` yo'llarini serverga moslang, so'ng xizmatni systemd orqali yoqing. Botning faqat bitta nusxasi ishlashi kerak.

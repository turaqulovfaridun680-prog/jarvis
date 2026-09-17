"""Eski ishga tushirish usuli bilan mos kirish nuqtasi."""

from pathlib import Path
import sys

from jarvis_bot import application


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("JARVIS YANGI VERSIYA ISHGA TUSHDI — list savoli yuklash tugagandan keyin.", flush=True)
    print(f"Kirish fayli: {Path(__file__).resolve()}")
    print(f"Bot handlerlari: {Path(application.__file__).resolve()}")
    print("/yuklash: mahsulotlar -> Yuklash tugadi yoki /tayyor -> katta listlar -> hisobot")
    application.main()


if __name__ == "__main__":
    main()

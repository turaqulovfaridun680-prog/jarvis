import os
import json
import re
import webbrowser
from pathlib import Path

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI()

BASE_DIR = Path(__file__).parent / "projects"
BASE_DIR.mkdir(exist_ok=True)

SYSTEM_PROMPT = """
Sen JARVIS Developer ismli web dasturchisan.

Foydalanuvchi qanday sayt kerakligini aytadi.
Sen unga zamonaviy va ishlaydigan sayt yarat.

Faqat JSON qaytar:

{
  "project_name": "sayt_nomi",
  "files": {
    "index.html": "HTML kodi",
    "style.css": "CSS kodi",
    "script.js": "JavaScript kodi"
  }
}

Hech qanday markdown yoki tushuntirish yozma.
Faqat JSON qaytar.
"""

def safe_name(name):
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return name[:50] or "website"

def sayt_yarat(topshiriq):
    print("\nJARVIS: Sayt yaratyapman...")

    response = client.responses.create(
        model="gpt-5.6",
        instructions=SYSTEM_PROMPT,
        input=topshiriq
    )

    raw = response.output_text.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    data = json.loads(raw)

    project_name = safe_name(data.get("project_name", "website"))
    project_dir = BASE_DIR / project_name
    suffix = 2
    while project_dir.exists() and any(project_dir.iterdir()):
        project_dir = BASE_DIR / f"{project_name}_{suffix}"
        suffix += 1
    project_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in data.get("files", {}).items():
        filename = os.path.basename(filename)
        filepath = project_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        print("Yaratildi:", filename)

    index_file = project_dir / "index.html"

    print("\nJARVIS: Sayt tayyor!")
    print("Papka:", project_dir)

    if index_file.exists():
        webbrowser.open(index_file.resolve().as_uri())
        print("JARVIS: Sayt brauzerda ochildi.")

print("JARVIS DEVELOPER ishga tushdi!")

while True:
    topshiriq = input("\nFarid: ")

    if topshiriq.lower() in ["chiq", "exit", "stop"]:
        print("JARVIS: Xayr!")
        break

    try:
        sayt_yarat(topshiriq)
    except Exception as e:
        print("XATOLIK:", e)

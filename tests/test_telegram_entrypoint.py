import runpy
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from telegram import Update, User

import test_delivery_flow
from jarvis_bot import application as app


class TelegramEntrypointTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_delivery_flow.DeliveryFlowTests.asyncSetUp

    async def test_script_registered_conversation_flow(self):
        entrypoint = Path(app.__file__).resolve().parent.parent / "telegram_jarvis.py"
        applications = []
        with (
            patch.object(app, "init_db"),
            patch.object(app.Application, "run_polling", autospec=True,
                         side_effect=lambda instance: applications.append(instance)),
            patch("builtins.print") as printed,
        ):
            runpy.run_path(str(entrypoint), run_name="__main__")
        self.assertEqual(len(applications), 1)
        printed.assert_any_call(
            "JARVIS YANGI VERSIYA ISHGA TUSHDI — list savoli yuklash tugagandan keyin.",
            flush=True,
        )
        application = applications[0]
        application.bot._bot_user = User(id=999, first_name="Test", is_bot=True, username="test_bot")
        loading = next(
            handler for group in application.handlers.values() for handler in group
            if isinstance(handler, app.ConversationHandler)
            and any(isinstance(entry, app.CommandHandler) and "yuklash" in entry.commands
                    for entry in handler.entry_points)
        )
        self.assertIs(loading.entry_points[0].callback, app.yuklash_start)
        self.assertEqual(Path(loading.entry_points[0].callback.__code__.co_filename).resolve(),
                         Path(app.__file__).resolve())
        sequence = 0

        async def dispatch(text=None, callback=None):
            nonlocal sequence
            sequence += 1
            message = {
                "message_id": sequence,
                "date": int(datetime.now(timezone.utc).timestamp()),
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "first_name": "Driver", "is_bot": False},
                "text": text or "Products",
            }
            if text and text.startswith("/"):
                message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
            payload = {"update_id": sequence, "message": message}
            if callback:
                payload = {"update_id": sequence, "callback_query": {
                    "id": str(sequence), "chat_instance": "test", "data": callback,
                    "from": message["from"], "message": message,
                }}
            update = Update.de_json(payload, application.bot)
            check = loading.check_update(update)
            self.assertIsNotNone(check, f"No handler for {text or callback}")
            await loading.handle_update(update, application, check, self.context)

        with (
            patch.object(app, "is_admin_user", return_value=False),
            patch.object(type(application.bot), "send_message", new_callable=AsyncMock) as send,
            patch.object(type(application.bot), "edit_message_text", new_callable=AsyncMock) as edit,
            patch.object(type(application.bot), "answer_callback_query", new_callable=AsyncMock),
        ):
            for finish in ("button", "command"):
                with self.subTest(finish=finish):
                    self.channel.reset_mock()
                    await dispatch("/yuklash")
                    self.assertTrue(send.call_args.kwargs["text"].startswith("📦 Mahsulot yuklash boshlandi."))
                    self.assertIn("Mahsulotni tanlang", send.call_args.kwargs["text"])
                    self.assertNotIn("nechta katta list", send.call_args.kwargs["text"])
                    await dispatch(callback="yp:1")
                    await dispatch(callback="yu:dona")
                    await dispatch("12")
                    for call in send.call_args_list + edit.call_args_list:
                        self.assertNotIn("nechta katta list", call.kwargs["text"])
                    if finish == "button":
                        await dispatch(callback="yfinish")
                        prompt = edit.call_args.kwargs["text"]
                    else:
                        await dispatch("/tayyor")
                        prompt = send.call_args.kwargs["text"]
                    self.assertIn("nechta katta list", prompt)
                    self.channel.assert_not_awaited()
                    await dispatch("5")
                    report = send.call_args.kwargs["text"]
                    self.assertIn("YUKLASH HISOBOTI", report)
                    self.assertIn("12 dona", report)
                    self.assertIn("katta list: 5 dona", report)
                    self.channel.assert_awaited_once()
                    self.assertNotIn("delivery", self.context.user_data)
                    send.reset_mock()
                    edit.reset_mock()


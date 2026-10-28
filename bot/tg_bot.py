#!/usr/bin/env python3
"""
Telegram control panel.
Commands: /start /stop /flatten /direction /status /balance
Needs:  pip install python-telegram-bot   +   TG_BOT_TOKEN, TG_CHAT_ID in env.
"""
from telegram import Update, BotCommand, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
import config as C

# Reply-keyboard labels → handler method names (same logic as slash commands)
_BTN = {
    "🟢 Старт": "_start",
    "🔴 Стоп": "_stop",
    "📊 Статус": "_status",
    "💰 Баланс": "_balance",
    "↔️ Both": "_dir_both",
    "📈 Long": "_dir_long",
    "📉 Short": "_dir_short",
    "🧹 Flatten": "_flatten",
    "❓ Help": "_help",
}

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🟢 Старт"), KeyboardButton("🔴 Стоп")],
        [KeyboardButton("📊 Статус"), KeyboardButton("💰 Баланс")],
        [KeyboardButton("↔️ Both"), KeyboardButton("📈 Long"), KeyboardButton("📉 Short")],
        [KeyboardButton("🧹 Flatten"), KeyboardButton("❓ Help")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


class TelegramControl:
    def __init__(self, bot):
        self.bot = bot                          # Bot instance (.risk, .exec, .sig)
        self.app = (
            Application.builder()
            .token(C.TG_BOT_TOKEN)
            .concurrent_updates(True)
            .build()
        )
        for name, cb in [("start", self._start), ("stop", self._stop),
                         ("status", self._status), ("direction", self._direction),
                         ("flatten", self._flatten), ("balance", self._balance),
                         ("ping", self._ping), ("help", self._help)]:
            self.app.add_handler(CommandHandler(name, cb))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_button))
        self.app.add_error_handler(self._on_error)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        err = context.error
        msg = str(err)
        if "Conflict" in msg or "terminated by other getUpdates" in msg:
            print("TG Conflict: два poller'а на одном токене (останови локальный main.py / 1 replica на Railway)", flush=True)
            return
        print(f"TG handler error: {err}", flush=True)

    def _auth(self, u: Update) -> bool:
        return not C.TG_CHAT_ID or str(u.effective_chat.id) == str(C.TG_CHAT_ID)

    async def _deny(self, u: Update):
        cid = u.effective_chat.id
        if C.TG_CHAT_ID and str(cid) != str(C.TG_CHAT_ID):
            await u.message.reply_text(
                f"⛔ chat_id {cid} не авторизован.\n"
                f"В Railway env: TG_CHAT_ID={C.TG_CHAT_ID}\n"
                f"Твой id: /ping"
            )

    async def _on_button(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self._auth(u):
            await self._deny(u)
            return
        method = _BTN.get(u.message.text)
        if method:
            await getattr(self, method)(u, c)

    async def _set_direction(self, u: Update, arg: str):
        self.bot.risk.direction = arg
        self.bot.sig.direction = arg
        await u.message.reply_text(f"↔️ направление: {arg}", reply_markup=MAIN_KEYBOARD)

    async def _dir_both(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        await self._set_direction(u, "both")

    async def _dir_long(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        await self._set_direction(u, "long")

    async def _dir_short(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        await self._set_direction(u, "short")

    async def _start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not self._auth(u):
            await self._deny(u)
            return
        self.bot.risk.active = True
        await u.message.reply_text(
            "🟢 бот запущен — ловлю сигналы\n\n"
            "Кнопки ниже или команды через / (меню слева от поля ввода).",
            reply_markup=MAIN_KEYBOARD,
        )

    async def _stop(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        self.bot.risk.active = False
        await u.message.reply_text(
            "🔴 бот остановлен — новых входов не будет.\n"
            "(открытая позиция, если есть, остаётся под серверными стоп/тейк ордерами)",
            reply_markup=MAIN_KEYBOARD,
        )

    async def _status(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        await u.message.reply_text(self.bot.risk.status_text(), reply_markup=MAIN_KEYBOARD)

    async def _direction(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        arg = (c.args[0].lower() if c.args else "")
        if arg in ("both", "long", "short"):
            await self._set_direction(u, arg)
        else:
            await u.message.reply_text(
                "используй: /direction both | long | short\nили кнопки Both / Long / Short",
                reply_markup=MAIN_KEYBOARD,
            )

    async def _flatten(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        if self.bot.exec:
            await self.bot.exec.flatten()
            await u.message.reply_text("🧹 всё закрыто (market reduce-only)", reply_markup=MAIN_KEYBOARD)
        else:
            await u.message.reply_text("dry-run: исполнения нет", reply_markup=MAIN_KEYBOARD)

    async def _balance(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        if self.bot.exec:
            bal = await self.bot.exec.balance()
            acc = self.bot.exec.account_index
            await u.message.reply_text(
                f"💰 Lighter mainnet (acc {acc}): ${bal:,.2f}",
                reply_markup=MAIN_KEYBOARD,
            )
        else:
            await u.message.reply_text("dry-run: баланс недоступен", reply_markup=MAIN_KEYBOARD)

    async def _ping(self, u, c):
        await u.message.reply_text(f"chat_id={u.effective_chat.id}\n\nДобавь в Railway env:\nTG_CHAT_ID={u.effective_chat.id}")

    async def _help(self, u, c):
        if not self._auth(u):
            await self._deny(u)
            return
        await u.message.reply_text(
            "📋 Команды бота:\n"
            "/start — запустить (ловить сигналы)\n"
            "/stop — остановить (без новых входов)\n"
            "/status — статус + аналитика за месяц\n"
            "/direction both|long|short — сторона торговли\n"
            "/flatten — закрыть всё (аварийно)\n"
            "/balance — баланс на бирже\n"
            "/ping — показать chat_id для setup\n"
            "/help — этот список\n\n"
            "Или используй кнопки ниже 👇",
            reply_markup=MAIN_KEYBOARD,
        )

    async def send(self, text: str):
        if C.TG_CHAT_ID:
            try:
                await self.app.bot.send_message(chat_id=C.TG_CHAT_ID, text=text)
            except Exception as e:
                print(f"TG send failed chat_id={C.TG_CHAT_ID}: {e}", flush=True)

    async def start(self):
        await self.app.initialize(); await self.app.start()
        # clear webhook so only this poller owns getUpdates
        wh = await self.app.bot.get_webhook_info()
        if wh.url:
            print(f"TG: clearing webhook {wh.url}", flush=True)
        await self.app.bot.delete_webhook(drop_pending_updates=True)
        await self.app.bot.set_my_commands([
            BotCommand("start", "запустить — ловить сигналы"),
            BotCommand("stop", "остановить — без новых входов"),
            BotCommand("status", "статус + аналитика за месяц"),
            BotCommand("direction", "both | long | short"),
            BotCommand("flatten", "закрыть всё (аварийно)"),
            BotCommand("balance", "баланс на бирже"),
            BotCommand("ping", "chat_id для setup"),
            BotCommand("help", "список команд"),
        ])
        await self.app.updater.start_polling(drop_pending_updates=True)

    async def stop(self):
        try:
            await self.app.updater.stop(); await self.app.stop(); await self.app.shutdown()
        except Exception:
            pass

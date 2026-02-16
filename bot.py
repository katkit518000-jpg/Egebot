import os
import json
import logging
from typing import Dict, List, Tuple
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ContentType
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ==================== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(','))) if os.getenv("ADMIN_IDS") else []
BASE_URL = os.getenv("RENDER_EXTERNAL_URL", "https://ваш-сервис.onrender.com")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = BASE_URL + WEBHOOK_PATH
DATA_FILE = "materials.json"

# ==================== ХРАНЕНИЕ ДАННЫХ ====================
materials: Dict[int, List[Tuple[str, str]]] = {}

def load_materials():
    global materials
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            # преобразуем ключи в int, значения оставляем как есть
            materials = {int(k): v for k, v in raw.items()}
    except FileNotFoundError:
        materials = {}

def save_materials():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(materials, f, ensure_ascii=False, indent=2)

load_materials()  # загружаем при старте

logging.basicConfig(level=logging.INFO)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ==================== ПРОВЕРКА АДМИНА ====================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ==================== СОСТОЯНИЯ FSM ====================
class AddMaterial(StatesGroup):
    waiting_for_file = State()

# ==================== ХЭНДЛЕРЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    for i in range(1, 20):
        builder.button(text=str(i), callback_data=f"task_{i}")
    builder.adjust(5)
    await message.answer(
        "📚 Выберите номер задания (от 1 до 19):",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("task_"))
async def process_task_selection(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    await callback.answer()

    if task_id not in materials or not materials[task_id]:
        await callback.message.answer(f"❌ Материал для задания {task_id} ещё не добавлен.")
        return

    for file_type, file_id in materials[task_id]:
        try:
            if file_type == "document":
                await callback.message.answer_document(file_id)
            elif file_type == "video":
                await callback.message.answer_video(file_id)
            elif file_type == "audio":
                await callback.message.answer_audio(file_id)
        except Exception as e:
            logging.error(f"Ошибка отправки файла {file_id}: {e}")
            await callback.message.answer(
                f"⚠️ Не удалось отправить один из файлов для задания {task_id}."
            )

@dp.message(Command("add"))
async def cmd_add(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ У вас нет прав администратора.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("❗ Используйте: /add <номер_задания> (например: /add 5)")
        return

    task_id = int(args[1])
    if task_id < 1 or task_id > 19:
        await message.reply("❗ Номер задания должен быть от 1 до 19.")
        return

    await state.set_state(AddMaterial.waiting_for_file)
    await state.update_data(task_id=task_id)

    await message.reply(
        f"📎 Отправьте файл (PDF, видео или аудио) для задания {task_id}.\n"
        "Вы можете отправить несколько файлов для одного задания.\n"
        "Для завершения введите /done."
    )

@dp.message(AddMaterial.waiting_for_file, F.content_type.in_({ContentType.DOCUMENT, ContentType.VIDEO, ContentType.AUDIO}))
async def handle_file_upload(message: types.Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("task_id")

    file_type = None
    file_id = None

    if message.document:
        file_type = "document"
        file_id = message.document.file_id
    elif message.video:
        file_type = "video"
        file_id = message.video.file_id
    elif message.audio:
        file_type = "audio"
        file_id = message.audio.file_id

    if not file_id:
        await message.reply("❌ Не удалось определить тип файла. Попробуйте снова.")
        return

    if task_id not in materials:
        materials[task_id] = []
    materials[task_id].append((file_type, file_id))
    save_materials()  # сохраняем после каждого добавления

    await message.reply(f"✅ Файл добавлен к заданию {task_id}. Можете отправить ещё файл или /done для завершения.")

@dp.message(Command("done"), AddMaterial.waiting_for_file)
async def cmd_done(message: types.Message, state: FSMContext):
    await state.clear()
    await message.reply("✅ Режим добавления материалов завершён.")

@dp.message(Command("checkme"))
async def cmd_checkme(message: types.Message):
    if is_admin(message.from_user.id):
        await message.reply("✅ Вы администратор.")
    else:
        await message.reply("❌ Вы не администратор.")

@dp.message(Command("list"))
async def cmd_list(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("⛔ У вас нет прав администратора.")
        return
    if not materials:
        await message.reply("📭 Нет загруженных материалов.")
        return
    lines = ["📋 Список заданий с материалами:"]
    for task_id in sorted(materials.keys()):
        count = len(materials[task_id])
        lines.append(f"Задание {task_id}: {count} файл(ов)")
    await message.reply("\n".join(lines))

@dp.message()
async def handle_unknown(message: types.Message):
    await message.reply("Используйте /start для выбора задания.")

# ==================== НАСТРОЙКА ВЕБХУКА ====================
async def on_startup():
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Webhook установлен на {WEBHOOK_URL}")

async def on_shutdown():
    await bot.delete_webhook()
    logging.info("Webhook удалён")

# ==================== ЗАПУСК ВЕБ-СЕРВЕРА ====================
def main():
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(lambda _: on_startup())
    app.on_shutdown.append(lambda _: on_shutdown())
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", 8080)))

if __name__ == "__main__":
    main()

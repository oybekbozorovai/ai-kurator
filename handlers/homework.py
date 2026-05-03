import logging
from pathlib import Path
from typing import Optional
from uuid import uuid4

from aiogram import Router, F
from aiogram.enums import ChatType, ChatAction
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from config import ADMIN_USER_IDS, HOMEWORK_DIR
from services.auth import is_user_approved
from services.gemini import grade_homework
from services.rag import format_context, retrieve

logger = logging.getLogger(__name__)
router = Router(name="homework")
router.message.filter(F.chat.type == ChatType.PRIVATE)


class HomeworkStates(StatesGroup):
    waiting_assignment = State()
    waiting_submission = State()


@router.message(Command("uy_vazifa"))
async def cmd_homework(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    if user_id not in ADMIN_USER_IDS and not is_user_approved(user_id):
        await message.answer("⛔ Avval /start bosib ro'yxatdan o'ting.")
        return
    await state.clear()
    await state.set_state(HomeworkStates.waiting_assignment)
    await message.answer(
        "📝 Uy vazifani tekshirish.\n\n"
        "1-qadam: Topshiriq matnini yuboring (qaysi vazifa ekanligi).\n"
        "Bekor qilish uchun /bekor."
    )


@router.message(Command("bekor"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        await state.clear()
        await message.answer("❌ Bekor qilindi.")


@router.message(HomeworkStates.waiting_assignment, F.text)
async def receive_assignment(message: Message, state: FSMContext) -> None:
    await state.update_data(assignment=message.text)
    await state.set_state(HomeworkStates.waiting_submission)
    await message.answer(
        "✅ Topshiriq qabul qilindi.\n\n"
        "2-qadam: Endi javobingizni yuboring — matn, rasm yoki rasm + izoh."
    )


@router.message(HomeworkStates.waiting_submission, F.photo | F.text)
async def receive_submission(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    assignment = data.get("assignment", "")

    submission_text = message.text or message.caption or ""
    image_path: Optional[Path] = None

    if message.photo:
        HOMEWORK_DIR.mkdir(parents=True, exist_ok=True)
        photo = message.photo[-1]
        image_path = HOMEWORK_DIR / f"{message.from_user.id}_{uuid4().hex}.jpg"
        await message.bot.download(photo, destination=image_path)

    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    await message.answer("🔍 Javobingiz tekshirilmoqda...")

    # Topshiriq matnidan tegishli kurs materialini topamiz
    query = (assignment + " " + submission_text).strip()
    hits = await retrieve(query, k=5)
    context = format_context(hits)

    feedback = await grade_homework(
        assignment=assignment,
        submission=submission_text,
        context=context,
        image_path=image_path,
    )
    await message.answer(feedback)
    await state.clear()

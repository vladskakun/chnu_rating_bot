import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import BOT_TOKEN
from rating_parser import (
    SPECIALTIES,
    analyse_all_specialities,
    analyse_selected_specialities,
    format_all_budget_specialities,
    format_speciality_result,
    parse_user_score,
)
from score_history import (
    ALL_SPECIALITIES_CONTEXT,
    get_recent_scores,
    init_database,
    save_score,
    speciality_context,
)


router = Router()


class RatingStates(StatesGroup):
    choosing_score = State()
    waiting_for_score = State()


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1. Обрані спеціальності",
                    callback_data="mode:selected",
                )
            ],
            [
                InlineKeyboardButton(
                    text="2. Усі спеціальності",
                    callback_data="mode:all",
                )
            ],
        ]
    )


def score_history_keyboard(
    scores: list[float],
) -> InlineKeyboardMarkup:
    """Створює кнопки останніх п'яти балів."""

    rows: list[list[InlineKeyboardButton]] = []
    current_row: list[InlineKeyboardButton] = []

    for score in scores:
        score_milli = round(score * 1000)

        current_row.append(
            InlineKeyboardButton(
                text=f"{score:.3f}",
                callback_data=f"score:{score_milli}",
            )
        )

        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []

    if current_row:
        rows.append(current_row)

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✍️ Ввести новий бал",
                    callback_data="score:new",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ До головного меню",
                    callback_data="menu:main",
                )
            ],
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Повернутися до меню",
                    callback_data="menu:main",
                )
            ]
        ]
    )


async def show_main_menu(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    await message.answer(
        "Вітаю! Я допоможу оцінити ваше місце "
        "у рейтингах вступників ЧНУ.\n\n"
        "Оберіть режим:",
        reply_markup=main_menu_keyboard(),
    )


async def ask_score_for_current_context(
    message: Message,
    state: FSMContext,
    user_id: int,
) -> None:
    """
    Показує останні 5 балів або просить ввести новий.

    Для обраних спеціальностей кожна ОП має власну історію.
    """

    data = await state.get_data()
    mode = data.get("mode")

    if mode == "selected":
        index = int(data.get("speciality_index", 0))

        if not 0 <= index < len(SPECIALTIES):
            await state.clear()
            await message.answer(
                "Не вдалося визначити спеціальність.",
                reply_markup=main_menu_keyboard(),
            )
            return

        speciality = SPECIALTIES[index]
        context_key = speciality_context(
            speciality["key"]
        )

        prompt = (
            f"<b>{index + 1} із {len(SPECIALTIES)}</b>\n"
            f"🎓 <b>{speciality['name']}</b>\n\n"
            "Оберіть попередній бал або введіть "
            "конкурсний бал саме для цієї спеціальності."
        )

    elif mode == "all":
        context_key = ALL_SPECIALITIES_CONTEXT

        prompt = (
            "Введіть один конкурсний бал для перевірки "
            "всіх спеціальностей."
        )

    else:
        await state.clear()
        await message.answer(
            "Не вдалося визначити режим.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.update_data(
        score_context=context_key
    )

    recent_scores = await asyncio.to_thread(
        get_recent_scores,
        user_id,
        context_key,
    )

    if recent_scores:
        await state.set_state(
            RatingStates.choosing_score
        )

        await message.answer(
            prompt,
            reply_markup=score_history_keyboard(
                recent_scores
            ),
        )
        return

    await state.set_state(
        RatingStates.waiting_for_score
    )

    await message.answer(
        f"{prompt}\n\n"
        "Введіть число від 100 до 200.\n"
        "Наприклад: <b>145.630</b>"
    )


async def run_selected_analysis(
    message: Message,
    scores_by_speciality: dict[str, float],
) -> None:
    progress_message = await message.answer(
        "⏳ Аналізую обрані спеціальності "
        "з окремими балами..."
    )

    try:
        results = await asyncio.to_thread(
            analyse_selected_specialities,
            scores_by_speciality,
        )

        await progress_message.edit_text(
            "Готово. Для кожної спеціальності "
            "використано окремий бал:"
        )

        for result in results:
            await message.answer(
                format_speciality_result(result)
            )

    except Exception:
        logging.exception(
            "Помилка аналізу обраних спеціальностей"
        )

        await progress_message.edit_text(
            "❌ Під час аналізу сталася помилка. "
            "Спробуйте ще раз трохи пізніше."
        )

    await message.answer(
        "Оберіть наступну дію:",
        reply_markup=back_to_menu_keyboard(),
    )


async def run_all_analysis(
    message: Message,
    score: float,
) -> None:
    progress_message = await message.answer(
        "⏳ Аналізую всі спеціальності..."
    )

    try:
        analysis = await asyncio.to_thread(
            analyse_all_specialities,
            score,
        )

        messages = format_all_budget_specialities(
            analysis,
            score,
        )

        await progress_message.edit_text(
            "Готово. Результати перевірки "
            "всіх спеціальностей:"
        )

        for text in messages:
            await message.answer(text)

    except Exception:
        logging.exception(
            "Помилка аналізу всіх спеціальностей"
        )

        await progress_message.edit_text(
            "❌ Під час аналізу сталася помилка. "
            "Спробуйте ще раз трохи пізніше."
        )

    await message.answer(
        "Оберіть наступну дію:",
        reply_markup=back_to_menu_keyboard(),
    )


async def accept_score(
    message: Message,
    state: FSMContext,
    user_id: int,
    score: float,
) -> None:
    """
    Зберігає бал поточного кроку.

    Для обраних спеціальностей переходить до наступної ОП.
    Для режиму «Усі» одразу запускає аналіз.
    """

    data = await state.get_data()
    mode = data.get("mode")
    context_key = data.get("score_context")

    if not isinstance(context_key, str):
        await state.clear()
        await message.answer(
            "Не вдалося визначити, для чого зберегти бал.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await asyncio.to_thread(
        save_score,
        user_id,
        score,
        context_key,
    )

    if mode == "all":
        await state.clear()
        await run_all_analysis(
            message,
            score,
        )
        return

    if mode != "selected":
        await state.clear()
        await message.answer(
            "Не вдалося визначити режим.",
            reply_markup=main_menu_keyboard(),
        )
        return

    index = int(data.get("speciality_index", 0))

    if not 0 <= index < len(SPECIALTIES):
        await state.clear()
        await message.answer(
            "Не вдалося визначити спеціальність.",
            reply_markup=main_menu_keyboard(),
        )
        return

    speciality = SPECIALTIES[index]
    selected_scores = dict(
        data.get("selected_scores", {})
    )

    selected_scores[
        speciality["key"]
    ] = score

    next_index = index + 1

    if next_index < len(SPECIALTIES):
        await state.update_data(
            speciality_index=next_index,
            selected_scores=selected_scores,
        )

        await ask_score_for_current_context(
            message,
            state,
            user_id,
        )
        return

    await state.clear()

    await run_selected_analysis(
        message,
        selected_scores,
    )


@router.message(CommandStart())
@router.message(Command("menu"))
async def start_handler(
    message: Message,
    state: FSMContext,
) -> None:
    await show_main_menu(
        message,
        state,
    )


@router.message(Command("cancel"))
async def cancel_handler(
    message: Message,
    state: FSMContext,
) -> None:
    await state.clear()

    await message.answer(
        "Поточну дію скасовано.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "menu:main")
async def menu_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.clear()

    if callback.message is not None:
        await callback.message.answer(
            "Оберіть режим:",
            reply_markup=main_menu_keyboard(),
        )


@router.callback_query(
    F.data.in_({"mode:selected", "mode:all"})
)
async def mode_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()

    if callback.message is None:
        return

    if callback.data == "mode:selected":
        await state.set_state(
            RatingStates.choosing_score
        )
        await state.update_data(
            mode="selected",
            speciality_index=0,
            selected_scores={},
        )

    else:
        await state.set_state(
            RatingStates.choosing_score
        )
        await state.update_data(
            mode="all",
        )

    await ask_score_for_current_context(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.callback_query(
    RatingStates.choosing_score,
    F.data == "score:new",
)
async def new_score_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()

    await state.set_state(
        RatingStates.waiting_for_score
    )

    if callback.message is not None:
        await callback.message.answer(
            "Введіть число від 100 до 200.\n"
            "Наприклад: <b>145.630</b>"
        )


@router.callback_query(
    RatingStates.choosing_score,
    F.data.startswith("score:"),
)
async def saved_score_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()

    if callback.data is None:
        return

    raw_score = callback.data.removeprefix(
        "score:"
    )

    if raw_score == "new" or not raw_score.isdigit():
        return

    if callback.message is None:
        return

    await accept_score(
        message=callback.message,
        state=state,
        user_id=callback.from_user.id,
        score=int(raw_score) / 1000,
    )


@router.message(RatingStates.waiting_for_score)
async def score_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if message.text is None:
        await message.answer(
            "Надішліть бал текстом. Наприклад: 145.630"
        )
        return

    score = parse_user_score(
        message.text
    )

    if score is None:
        await message.answer(
            "Некоректний бал.\n"
            "Введіть одне число від 100 до 200, "
            "наприклад: <b>145.630</b>"
        )
        return

    if message.from_user is None:
        await message.answer(
            "Не вдалося визначити користувача."
        )
        return

    await accept_score(
        message=message,
        state=state,
        user_id=message.from_user.id,
        score=score,
    )


@router.message()
async def unknown_message_handler(
    message: Message,
) -> None:
    await message.answer(
        "Скористайтеся командою /start або /menu, "
        "щоб відкрити меню."
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    await asyncio.to_thread(
        init_database
    )

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

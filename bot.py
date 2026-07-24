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
    discover_all_specialities,
    format_all_budget_specialities,
    format_person_search_results,
    format_speciality_result,
    parse_person_query,
    parse_user_score,
    search_applicant_in_all_ratings,
)
from score_history import (
    all_speciality_context,
    get_recent_name_queries,
    get_recent_scores,
    init_database,
    save_name_query,
    save_score,
    speciality_context,
)


router = Router()


class RatingStates(StatesGroup):
    choosing_score = State()
    waiting_for_score = State()
    choosing_name = State()
    waiting_for_name = State()


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
            [
                InlineKeyboardButton(
                    text="3. Знайти себе у всіх рейтингах",
                    callback_data="mode:search",
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



def name_history_keyboard(
    queries: list[str],
) -> InlineKeyboardMarkup:
    """Створює кнопки останніх 5 запитів за ПІБ."""

    rows = []

    for index, query in enumerate(
        queries
    ):
        display_text = (
            query
            if len(query) <= 48
            else f"{query[:45]}..."
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text=f"👤 {display_text}",
                    callback_data=(
                        f"name_history:{index}"
                    ),
                )
            ]
        )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✍️ Ввести нове ім’я",
                    callback_data="name:new",
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


def get_current_speciality(
    data: dict,
) -> tuple[dict, int, int] | None:
    """
    Повертає поточну ОП, її індекс і загальну кількість
    відповідно до активного режиму.
    """

    mode = data.get("mode")
    index = int(data.get("speciality_index", 0))

    if mode == "selected":
        specialities = SPECIALTIES

    elif mode == "all":
        specialities = data.get(
            "all_specialities",
            [],
        )

    else:
        return None

    if not 0 <= index < len(specialities):
        return None

    return (
        specialities[index],
        index,
        len(specialities),
    )


async def ask_score_for_current_context(
    message: Message,
    state: FSMContext,
    user_id: int,
) -> None:
    """Запитує окремий бал для поточної ОП."""

    data = await state.get_data()
    mode = data.get("mode")
    current = get_current_speciality(data)

    if current is None:
        await state.clear()
        await message.answer(
            "Не вдалося визначити поточну спеціальність.",
            reply_markup=main_menu_keyboard(),
        )
        return

    speciality, index, total = current

    if mode == "selected":
        context_key = speciality_context(
            speciality["key"]
        )

    else:
        context_key = all_speciality_context(
            speciality["key"]
        )

    await state.update_data(
        score_context=context_key
    )

    recent_scores = await asyncio.to_thread(
        get_recent_scores,
        user_id,
        context_key,
    )

    prompt = (
        f"<b>{index + 1} із {total}</b>\n"
        f"🎓 <b>{speciality['name']}</b>\n\n"
        "Оберіть один із попередніх балів або введіть "
        "конкурсний бал саме для цієї спеціальності."
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
    specialities: list[dict],
    scores_by_speciality: dict[str, float],
) -> None:
    progress_message = await message.answer(
        "⏳ Аналізую всі спеціальності "
        "з окремими балами..."
    )

    try:
        analysis = await asyncio.to_thread(
            analyse_all_specialities,
            scores_by_speciality,
            specialities,
        )

        messages = format_all_budget_specialities(
            analysis
        )

        await progress_message.edit_text(
            "Готово. Перевірено всі спеціальності "
            "з указаними для них балами:"
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
    """Зберігає бал і переходить до наступної ОП."""

    data = await state.get_data()
    mode = data.get("mode")
    context_key = data.get("score_context")
    current = get_current_speciality(data)

    if (
        not isinstance(context_key, str)
        or current is None
    ):
        await state.clear()
        await message.answer(
            "Не вдалося визначити, для чого зберегти бал.",
            reply_markup=main_menu_keyboard(),
        )
        return

    speciality, index, total = current

    await asyncio.to_thread(
        save_score,
        user_id,
        score,
        context_key,
    )

    scores_key = (
        "selected_scores"
        if mode == "selected"
        else "all_scores"
    )

    scores = dict(
        data.get(scores_key, {})
    )
    scores[speciality["key"]] = score

    next_index = index + 1

    if next_index < total:
        await state.update_data(
            speciality_index=next_index,
            **{
                scores_key: scores,
            },
        )

        await ask_score_for_current_context(
            message,
            state,
            user_id,
        )
        return

    if mode == "selected":
        await state.clear()
        await run_selected_analysis(
            message,
            scores,
        )
        return

    if mode == "all":
        all_specialities = data.get(
            "all_specialities",
            [],
        )

        await state.clear()
        await run_all_analysis(
            message,
            all_specialities,
            scores,
        )
        return

    await state.clear()
    await message.answer(
        "Не вдалося визначити режим.",
        reply_markup=main_menu_keyboard(),
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



async def run_person_search(
    message: Message,
    state: FSMContext,
    user_id: int,
    query_text: str,
) -> None:
    """Зберігає ПІБ в історію та запускає пошук."""

    if parse_person_query(query_text) is None:
        await message.answer(
            "Введіть щонайменше два слова: "
            "прізвище та ім’я.\n"
            "Наприклад: <b>Скакун Ерік</b>"
        )
        return

    cleaned_query = " ".join(
        query_text.split()
    )

    await asyncio.to_thread(
        save_name_query,
        user_id,
        cleaned_query,
    )

    await state.clear()

    progress_message = await message.answer(
        "⏳ Шукаю у всіх підключених рейтингах..."
    )

    try:
        analysis = await asyncio.to_thread(
            search_applicant_in_all_ratings,
            cleaned_query,
        )

        result_messages = format_person_search_results(
            analysis
        )

        await progress_message.edit_text(
            "Готово. Пошук завершено."
        )

        for text in result_messages:
            await message.answer(text)

    except Exception:
        logging.exception(
            "Помилка пошуку вступника"
        )

        await progress_message.edit_text(
            "❌ Під час пошуку сталася помилка. "
            "Спробуйте ще раз трохи пізніше."
        )

    await message.answer(
        "Оберіть наступну дію:",
        reply_markup=back_to_menu_keyboard(),
    )


@router.callback_query(F.data == "mode:search")
async def search_mode_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.clear()

    if callback.message is None:
        return

    recent_queries = await asyncio.to_thread(
        get_recent_name_queries,
        callback.from_user.id,
    )

    if recent_queries:
        await state.set_state(
            RatingStates.choosing_name
        )
        await state.update_data(
            recent_name_queries=recent_queries
        )

        await callback.message.answer(
            "Оберіть один з останніх пошуків "
            "або введіть нове прізвище та ім’я:",
            reply_markup=name_history_keyboard(
                recent_queries
            ),
        )
        return

    await state.set_state(
        RatingStates.waiting_for_name
    )

    await callback.message.answer(
        "Введіть <b>прізвище та ім’я</b>.\n"
        "По батькові можна не вказувати.\n\n"
        "Наприклад: <b>Скакун Ерік</b>\n\n"
        "Пошук відбувається лише у списках "
        "«зарахування за конкурсом»."
    )


@router.callback_query(
    RatingStates.choosing_name,
    F.data == "name:new",
)
async def new_name_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()

    await state.set_state(
        RatingStates.waiting_for_name
    )

    if callback.message is not None:
        await callback.message.answer(
            "Введіть прізвище та ім’я.\n"
            "Наприклад: <b>Скакун Ерік</b>"
        )


@router.callback_query(
    RatingStates.choosing_name,
    F.data.startswith("name_history:"),
)
async def saved_name_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()

    if (
        callback.data is None
        or callback.message is None
    ):
        return

    raw_index = callback.data.removeprefix(
        "name_history:"
    )

    if not raw_index.isdigit():
        return

    data = await state.get_data()
    queries = data.get(
        "recent_name_queries",
        [],
    )
    index = int(raw_index)

    if not 0 <= index < len(queries):
        await callback.message.answer(
            "Не вдалося прочитати збережений запит. "
            "Відкрийте пошук ще раз."
        )
        return

    await run_person_search(
        message=callback.message,
        state=state,
        user_id=callback.from_user.id,
        query_text=queries[index],
    )


@router.message(RatingStates.waiting_for_name)
async def person_name_handler(
    message: Message,
    state: FSMContext,
) -> None:
    if message.text is None:
        await message.answer(
            "Надішліть прізвище та ім’я текстом."
        )
        return

    if message.from_user is None:
        await message.answer(
            "Не вдалося визначити користувача."
        )
        return

    await run_person_search(
        message=message,
        state=state,
        user_id=message.from_user.id,
        query_text=message.text,
    )


@router.callback_query(F.data == "mode:selected")
async def selected_mode_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()

    if callback.message is None:
        return

    await state.set_state(
        RatingStates.choosing_score
    )
    await state.update_data(
        mode="selected",
        speciality_index=0,
        selected_scores={},
    )

    await ask_score_for_current_context(
        callback.message,
        state,
        callback.from_user.id,
    )


@router.callback_query(F.data == "mode:all")
async def all_mode_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await callback.answer()
    await state.clear()

    if callback.message is None:
        return

    progress_message = await callback.message.answer(
        "⏳ Завантажую список усіх спеціальностей..."
    )

    try:
        discovery = await asyncio.to_thread(
            discover_all_specialities
        )

    except Exception:
        logging.exception(
            "Помилка завантаження списку спеціальностей"
        )

        await progress_message.edit_text(
            "❌ Не вдалося завантажити список спеціальностей."
        )
        return

    specialities = discovery["specialities"]

    if not specialities:
        await progress_message.edit_text(
            "❌ На підключених сторінках не знайдено "
            "жодної конкурсної спеціальності."
        )
        return

    await progress_message.edit_text(
        "Знайдено спеціальностей: "
        f"<b>{len(specialities)}</b>.\n"
        "Тепер введіть окремий конкурсний бал "
        "для кожної з них.\n\n"
        "Скасувати процес можна командою /cancel."
    )

    await state.set_state(
        RatingStates.choosing_score
    )
    await state.update_data(
        mode="all",
        speciality_index=0,
        all_specialities=specialities,
        all_scores={},
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

    if (
        raw_score == "new"
        or not raw_score.isdigit()
    ):
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

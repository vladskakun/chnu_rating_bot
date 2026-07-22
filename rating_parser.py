from __future__ import annotations

import html
import re
import threading
import time
from typing import Any

import requests
from bs4 import BeautifulSoup


# ============================================================
# НАЛАШТУВАННЯ РЕЙТИНГІВ
# ============================================================

URL = (
    "https://www.chnu.edu.ua/public/abitlist/2026/"
    "bakalavr/denna/ftkn/rating_atestat.html?2394839257"
)

SPECIALTIES = [
    {
        "key": "computer_engineering",
        "name": (
            "Комп’ютерна інженерія — програмування мобільних "
            "і вбудованих систем та інтернет речей"
        ),
        "url": URL,
        "search_text": 'ОП "Комп’ютерна інженерія"',
    },
    {
        "key": "automation",
        "name": (
            "Автоматизація та комп'ютерно-інтегровані технології"
        ),
        "url": URL,
        "search_text": (
            'ОП "Автоматизація та '
            "комп'ютерно-інтегровані технології\""
        ),
    },
    {
        "key": "electrical_engineering",
        "name": (
            "Електроенергетика, електротехніка "
            "та електромеханіка"
        ),
        "url": URL,
        "search_text": (
            'ОП "Електроенергетика, електротехніка '
            'та електромеханіка"'
        ),
    },
]

# Сторінки, усі ОП яких перевіряються в режимі
# «Переглянути рейтинг на всіх спеціальностях».
ALL_SPECIALTIES_URLS = [
    URL,
]

# Скільки найближчих результатів показувати вище та нижче.
NEIGHBORS_COUNT = 2

# Рейтинг змінюється протягом дня, тому кеш не повинен бути вічним.
CACHE_TTL_SECONDS = 60


# ============================================================
# КЕШ І ЗАВАНТАЖЕННЯ СТОРІНОК
# ============================================================

_PAGE_CACHE: dict[str, tuple[float, BeautifulSoup]] = {}
_CACHE_LOCK = threading.Lock()


def download_page(url: str) -> BeautifulSoup:
    """Завантажує HTML-сторінку рейтингу."""

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
        )
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"

    return BeautifulSoup(response.text, "html.parser")


def get_page(url: str, force_refresh: bool = False) -> BeautifulSoup:
    """
    Повертає сторінку з короткочасного кешу або завантажує її.

    Кеш спільний для користувачів бота, але автоматично
    оновлюється після CACHE_TTL_SECONDS.
    """

    now = time.monotonic()

    if not force_refresh:
        with _CACHE_LOCK:
            cached = _PAGE_CACHE.get(url)

        if cached is not None:
            saved_at, soup = cached

            if now - saved_at < CACHE_TTL_SECONDS:
                return soup

    soup = download_page(url)

    with _CACHE_LOCK:
        _PAGE_CACHE[url] = (time.monotonic(), soup)

    return soup


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================================

def normalize_text(text: str) -> str:
    """Нормалізує апострофи, регістр і пробіли."""

    for symbol in ("’", "‘", "ʼ", "`"):
        text = text.replace(symbol, "'")

    return " ".join(text.split()).lower().strip()


def parse_score(text: str) -> float | None:
    """Намагається витягнути рейтинговий бал із комірки."""

    normalized = text.strip().replace(",", ".")

    match = re.search(
        r"\b\d{2,3}(?:\.\d+)?\b",
        normalized,
    )

    if match is None:
        return None

    score = float(match.group())

    if 100 <= score <= 200:
        return score

    return None


def parse_user_score(text: str) -> float | None:
    """
    Перевіряє бал, введений користувачем.

    На відміну від parse_score(), ця функція вимагає,
    щоб повідомлення повністю складалося з одного числа.
    """

    normalized = text.strip().replace(",", ".")

    if not re.fullmatch(r"\d{2,3}(?:\.\d{1,6})?", normalized):
        return None

    score = float(normalized)

    if 100 <= score <= 200:
        return score

    return None


def is_contract_only(features: str) -> bool:
    """Перевіряє наявність окремої позначки ПЛ."""

    tokens = re.split(r"[\s,;]+", features.upper().strip())
    return "ПЛ" in tokens


# ============================================================
# ПОШУК СПЕЦІАЛЬНОСТЕЙ І КІЛЬКОСТІ МІСЦЬ
# ============================================================

def get_rating_table_type(table: Any) -> str | None:
    """
    Визначає тип рейтингової таблиці.

    На реальній сторінці ЧНУ напис «зарахування за конкурсом»
    часто розміщений окремим елементом ПЕРЕД таблицею, а не
    всередині самої таблиці. Тому перевіряємо:

    1. текст усередині таблиці;
    2. найближчий рейтинговий заголовок перед таблицею;
    3. зупиняємося біля попередньої таблиці або наступної межі ОП.
    """

    if table is None:
        return None

    table_text = normalize_text(
        table.get_text(" ", strip=True)
    )

    if "зарахування за конкурсом" in table_text:
        return "competition"

    if "зарахування за квотою" in table_text:
        return "quota"

    # Заголовок рейтингу може бути у div, p, b, h5 тощо
    # безпосередньо перед самою таблицею.
    for element in table.previous_elements:
        element_name = getattr(
            element,
            "name",
            None,
        )

        # Не дозволяємо випадково взяти заголовок
        # від попередньої спеціальності або попередньої таблиці.
        if element_name in {"h4", "table"}:
            break

        if not isinstance(element, str):
            continue

        previous_text = normalize_text(
            str(element)
        )

        if "зарахування за конкурсом" in previous_text:
            return "competition"

        if "зарахування за квотою" in previous_text:
            return "quota"

    return None


def is_competition_rating_table(table: Any) -> bool:
    """Перевіряє належність таблиці до загального конкурсу."""

    return get_rating_table_type(table) == "competition"


def find_speciality_section(
    soup: BeautifulSoup,
    search_text: str,
) -> dict[str, Any] | None:
    """
    Знаходить заголовок, держмісця та таблицю загального конкурсу.

    Якщо перед загальним конкурсом розміщені таблиці квоти 1
    або квоти 2, вони пропускаються.
    """

    normalized_search = normalize_text(search_text)

    for heading in soup.find_all("h4"):
        heading_text = heading.get_text(" ", strip=True)

        if normalized_search not in normalize_text(heading_text):
            continue

        seats_element = None
        rating_table = None

        for element in heading.find_all_next(["h4", "h5", "table"]):
            if element.name == "h4":
                break

            if element.name == "h5":
                element_text = normalize_text(
                    element.get_text(" ", strip=True)
                )

                if "держзамовлення" in element_text:
                    seats_element = element

                continue

            if (
                element.name == "table"
                and is_competition_rating_table(element)
            ):
                rating_table = element
                break

        return {
            "full_name": heading_text,
            "seats_element": seats_element,
            "table": rating_table,
        }

    return None


def find_all_specialities(
    soup: BeautifulSoup,
    url: str,
    score: float,
) -> list[dict[str, Any]]:
    """Формує список усіх освітніх програм на сторінці."""

    specialities: list[dict[str, Any]] = []

    for heading in soup.find_all("h4"):
        full_name = heading.get_text(" ", strip=True)
        normalized_name = normalize_text(full_name)

        if re.search(r"\bоп\b", normalized_name) is None:
            continue

        specialities.append(
            {
                "name": full_name,
                "url": url,
                "search_text": full_name,
                "score": score,
            }
        )

    return specialities


def parse_state_seats(seats_element: Any) -> int | None:
    """Зчитує кількість місць державного замовлення."""

    if seats_element is None:
        return None

    text = normalize_text(
        seats_element.get_text(" ", strip=True)
    )

    match = re.search(
        r"(?:максимальна\s+к-сть\s+)?"
        r"місць\s+держзамовлення\s*:\s*(\d+)",
        text,
        flags=re.IGNORECASE,
    )

    if match is None:
        return None

    return int(match.group(1))


# ============================================================
# ЗЧИТУВАННЯ ВСТУПНИКІВ
# ============================================================

def parse_applicants(table: Any) -> list[dict[str, Any]]:
    """Зчитує всіх вступників із таблиці загального конкурсу."""

    applicants: list[dict[str, Any]] = []

    if table is None:
        return applicants

    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])

        values = [
            cell.get_text(" ", strip=True)
            for cell in cells
        ]

        if len(values) < 3:
            continue

        position_text = values[0].strip().rstrip(".")

        if not position_text.isdigit():
            continue

        score = parse_score(values[2])

        if score is None:
            continue

        features = values[3] if len(values) >= 4 else ""
        confirmed_place = values[4] if len(values) >= 5 else ""
        priority = values[5] if len(values) >= 6 else ""

        applicants.append(
            {
                "original_position": int(position_text),
                "name": values[1],
                "score": score,
                "features": features,
                "confirmed_place": confirmed_place,
                "priority": priority,
                "contract_only": is_contract_only(features),
            }
        )

    applicants.sort(
        key=lambda applicant: applicant["score"],
        reverse=True,
    )

    return applicants


# ============================================================
# РОЗРАХУНОК РЕЙТИНГУ
# ============================================================

def calculate_forecast(
    applicants: list[dict[str, Any]],
    my_score: float,
) -> dict[str, int]:
    """Обчислює найкраще та найгірше прогнозоване місце."""

    higher_count = sum(
        applicant["score"] > my_score
        for applicant in applicants
    )

    equal_count = sum(
        abs(applicant["score"] - my_score) < 0.000001
        for applicant in applicants
    )

    return {
        "best_place": higher_count + 1,
        "worst_place": higher_count + equal_count + 1,
        "higher_count": higher_count,
        "equal_count": equal_count,
    }


def get_nearby_applicants(
    applicants: list[dict[str, Any]],
    my_score: float,
    count: int = 2,
) -> dict[str, list[dict[str, Any]]]:
    """Повертає найближчі результати вище та нижче."""

    sorted_applicants = sorted(
        applicants,
        key=lambda applicant: applicant["score"],
        reverse=True,
    )

    higher: list[dict[str, Any]] = []
    lower: list[dict[str, Any]] = []

    for position, applicant in enumerate(
        sorted_applicants,
        start=1,
    ):
        item = {
            **applicant,
            "position_before_adding": position,
        }

        if applicant["score"] > my_score:
            higher.append(item)

        elif applicant["score"] < my_score:
            lower.append(item)

    nearest_higher = higher[-count:]
    nearest_lower = lower[:count]

    for applicant in nearest_higher:
        applicant["forecast_position"] = (
            applicant["position_before_adding"]
        )

    for applicant in nearest_lower:
        applicant["forecast_position"] = (
            applicant["position_before_adding"] + 1
        )

    return {
        "higher": nearest_higher,
        "lower": nearest_lower,
    }


def get_budget_status(
    best_place: int,
    worst_place: int,
    state_seats: int | None,
) -> str:
    """Формує текстовий висновок про держзамовлення."""

    if state_seats is None:
        return "Не вдалося визначити кількість бюджетних місць."

    if best_place > state_seats:
        return "❌ Поза межами держзамовлення за поточним рейтингом."

    if worst_place <= state_seats:
        return "✅ У межах держзамовлення за поточним рейтингом."

    return "⚠️ На межі держзамовлення через однакові бали."


def get_budget_category(result: dict[str, Any]) -> str:
    """Повертає definite, border, outside або unknown."""

    if not result.get("success"):
        return "unknown"

    state_seats = result.get("state_seats")

    if state_seats is None:
        return "unknown"

    if result["worst_place"] <= state_seats:
        return "definite"

    if result["best_place"] <= state_seats < result["worst_place"]:
        return "border"

    return "outside"


def get_current_budget_border(
    applicants: list[dict[str, Any]],
    state_seats: int | None,
) -> float | None:
    """Повертає поточний бал останнього бюджетного місця."""

    if state_seats is None or len(applicants) < state_seats:
        return None

    return applicants[state_seats - 1]["score"]


def analyse_speciality(
    soup: BeautifulSoup,
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Аналізує одну освітню програму."""

    section = find_speciality_section(
        soup,
        settings["search_text"],
    )

    if section is None:
        return {
            "success": False,
            "settings_name": settings["name"],
            "error": (
                "Не вдалося знайти спеціальність за текстом: "
                f'{settings["search_text"]}'
            ),
        }

    if section["table"] is None:
        return {
            "success": False,
            "settings_name": settings["name"],
            "error": (
                "Спеціальність знайдено, але таблицю "
                "«зарахування за конкурсом» не вдалося визначити."
            ),
        }

    state_seats = parse_state_seats(section["seats_element"])
    all_applicants = parse_applicants(section["table"])

    budget_applicants = [
        applicant
        for applicant in all_applicants
        if not applicant["contract_only"]
    ]

    contract_only_count = sum(
        applicant["contract_only"]
        for applicant in all_applicants
    )

    forecast = calculate_forecast(
        budget_applicants,
        settings["score"],
    )

    nearby_applicants = get_nearby_applicants(
        budget_applicants,
        settings["score"],
        count=NEIGHBORS_COUNT,
    )

    budget_border = get_current_budget_border(
        budget_applicants,
        state_seats,
    )

    return {
        "success": True,
        "settings_name": settings["name"],
        "full_name": section["full_name"],
        "score": settings["score"],
        "state_seats": state_seats,
        "all_applicants_count": len(all_applicants),
        "budget_applicants_count": len(budget_applicants),
        "contract_only_count": contract_only_count,
        "budget_border": budget_border,
        "nearby_applicants": nearby_applicants,
        "status": get_budget_status(
            forecast["best_place"],
            forecast["worst_place"],
            state_seats,
        ),
        **forecast,
    }


# ============================================================
# ПУБЛІЧНІ ФУНКЦІЇ ДЛЯ TELEGRAM-БОТА
# ============================================================

def analyse_selected_specialities(
    scores: float | dict[str, float],
) -> list[dict[str, Any]]:
    """
    Аналізує програми зі списку SPECIALTIES.

    Якщо передано число, воно застосовується до всіх програм.
    Якщо передано словник, для кожної програми використовується
    окремий бал за її полем key.
    """

    results: list[dict[str, Any]] = []

    for speciality in SPECIALTIES:
        speciality_key = speciality["key"]

        if isinstance(scores, dict):
            score = scores.get(speciality_key)

            if score is None:
                results.append(
                    {
                        "success": False,
                        "settings_name": speciality["name"],
                        "error": (
                            "Для цієї спеціальності не вказано "
                            "конкурсний бал."
                        ),
                    }
                )
                continue
        else:
            score = scores

        settings = {
            **speciality,
            "score": float(score),
        }

        try:
            soup = get_page(settings["url"])
            result = analyse_speciality(soup, settings)

        except requests.RequestException as error:
            result = {
                "success": False,
                "settings_name": settings["name"],
                "error": f"Не вдалося завантажити сторінку: {error}",
            }

        results.append(result)

    return results





def tokenize_person_name(text: str) -> list[str]:
    """
    Нормалізує ПІБ для пошуку.

    Регістр, зайві пробіли, дефіси та різні апострофи
    не впливають на результат.
    """

    normalized = normalize_text(text)

    # Дефіс вважаємо роздільником між словами.
    normalized = normalized.replace("-", " ")

    return re.findall(
        r"[0-9a-zа-яіїєґ']+",
        normalized,
        flags=re.IGNORECASE,
    )


def parse_person_query(text: str) -> list[str] | None:
    """
    Очікує щонайменше два слова: прізвище та ім'я.
    """

    tokens = tokenize_person_name(text)

    if len(tokens) < 2:
        return None

    return tokens


def person_name_matches(
    applicant_name: str,
    query_tokens: list[str],
) -> bool:
    """
    Пошук за прізвищем та ім'ям незалежно від їх порядку.

    Використовується точний збіг нормалізованих слів,
    щоб «Іван» не збігався з «Іванна».
    """

    applicant_tokens = tokenize_person_name(
        applicant_name
    )

    return all(
        token in applicant_tokens
        for token in query_tokens
    )


def iter_speciality_competition_tables(
    soup: BeautifulSoup,
):
    """
    Послідовно повертає всі спеціальності та їхні таблиці
    «зарахування за конкурсом».

    Таблиці квоти 1 і квоти 2 повністю пропускаються.
    """

    headings = soup.find_all("h4")

    for heading in headings:
        full_name = heading.get_text(
            " ",
            strip=True,
        )

        # Заголовок має бути схожим на освітню програму.
        normalized_heading = normalize_text(full_name)

        if "оп " not in normalized_heading and "оп\"" not in normalized_heading:
            # Додаткова перевірка без залежності від конкретних лапок.
            if re.search(r"(^|\s)оп(\s|$)", normalized_heading) is None:
                continue

        competition_table = None

        for element in heading.find_all_next(
            ["h4", "table"]
        ):
            if element.name == "h4":
                break

            if (
                element.name == "table"
                and is_competition_rating_table(element)
            ):
                competition_table = element
                break

        if competition_table is not None:
            yield {
                "full_name": full_name,
                "table": competition_table,
            }


def search_applicant_in_all_ratings(
    person_query: str,
) -> dict[str, Any]:
    """
    Шукає вступника у всіх підключених рейтингах.

    Пошук відбувається напряму по кожній таблиці загального
    конкурсу, без повторного пошуку секції за повним заголовком.
    """

    query_tokens = parse_person_query(
        person_query
    )

    if query_tokens is None:
        return {
            "query": person_query,
            "matches": [],
            "errors": [],
            "invalid_query": True,
        }

    matches: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[tuple[str, int, str]] = set()

    for url in ALL_SPECIALTIES_URLS:
        try:
            soup = get_page(
                url,
                force_refresh=True,
            )

        except requests.RequestException as error:
            errors.append(
                f"{url}: {error}"
            )
            continue

        for section in iter_speciality_competition_tables(
            soup
        ):
            applicants = parse_applicants(
                section["table"]
            )

            for applicant in applicants:
                if not person_name_matches(
                    applicant["name"],
                    query_tokens,
                ):
                    continue

                unique_key = (
                    section["full_name"],
                    applicant["original_position"],
                    applicant["name"],
                )

                if unique_key in seen:
                    continue

                seen.add(unique_key)

                matches.append(
                    {
                        "full_name": section["full_name"],
                        "position": applicant[
                            "original_position"
                        ],
                        "name": applicant["name"],
                    }
                )

    matches.sort(
        key=lambda item: (
            normalize_text(item["full_name"]),
            item["position"],
        )
    )

    return {
        "query": person_query.strip(),
        "matches": matches,
        "errors": errors,
        "invalid_query": False,
    }


def analyse_all_specialities(
    score: float,
) -> dict[str, Any]:
    """Аналізує всі ОП на сторінках ALL_SPECIALTIES_URLS."""

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for url in ALL_SPECIALTIES_URLS:
        try:
            soup = get_page(url)

        except requests.RequestException as error:
            errors.append(f"{url}: {error}")
            continue

        settings_list = find_all_specialities(
            soup=soup,
            url=url,
            score=score,
        )

        for settings in settings_list:
            result = analyse_speciality(soup, settings)

            if result["success"]:
                results.append(result)

    return {
        "results": results,
        "errors": errors,
    }


# ============================================================
# ФОРМАТУВАННЯ TELEGRAM-ПОВІДОМЛЕНЬ
# ============================================================

def _place_text(result: dict[str, Any]) -> str:
    if result["best_place"] == result["worst_place"]:
        return str(result["best_place"])

    return f'{result["best_place"]}–{result["worst_place"]}'


def _safe(value: Any) -> str:
    return html.escape(str(value))


def format_speciality_result(result: dict[str, Any]) -> str:
    """Формує одне HTML-повідомлення для обраної ОП."""

    name = _safe(result.get("settings_name", "Спеціальність"))

    if not result["success"]:
        return (
            f"❌ <b>{name}</b>\n\n"
            f"{_safe(result['error'])}"
        )

    state_seats = result["state_seats"]
    seats_text = (
        str(state_seats)
        if state_seats is not None
        else "невідомо"
    )

    lines = [
        f"🎓 <b>{name}</b>",
        "",
        f"Ваш бал: <b>{result['score']:.3f}</b>",
        (
            "Прогнозоване місце: "
            f"<b>{_place_text(result)} із {seats_text}</b> бюджетних"
        ),
        "",
        f"Усього заяв: {result['all_applicants_count']}",
        (
            "Беруть участь у конкурсі на бюджет: "
            f"{result['budget_applicants_count']}"
        ),
        f"Лише контракт, ПЛ: {result['contract_only_count']}",
        f"Учасників із вищим балом: {result['higher_count']}",
        f"Учасників із таким самим балом: {result['equal_count']}",
        "",
        "<b>Найближчі результати</b>",
    ]

    nearby = result["nearby_applicants"]
    higher = nearby["higher"]
    lower = nearby["lower"]

    if higher:
        lines.append("🔼 Вище вашого результату:")

        for applicant in higher:
            lines.append(
                f"• {applicant['forecast_position']} місце — "
                f"{applicant['score']:.3f} — "
                f"{_safe(applicant['name'])}"
            )
    else:
        lines.append("🔼 Вище немає вступників.")

    lines.extend(
        [
            "",
            (
                f"➡️ <b>{_place_text(result)} місце — "
                f"{result['score']:.3f} — ваш результат</b>"
            ),
            "",
        ]
    )

    if lower:
        lines.append("🔽 Нижче вашого результату:")

        for applicant in lower:
            lines.append(
                f"• {applicant['forecast_position']} місце — "
                f"{applicant['score']:.3f} — "
                f"{_safe(applicant['name'])}"
            )
    else:
        lines.append("🔽 Нижче немає вступників.")

    lines.append("")

    if result["budget_border"] is None:
        lines.append(
            "Поточна межа бюджету ще не визначена: "
            "заяв на бюджет менше, ніж бюджетних місць."
        )
    else:
        lines.append(
            "Поточний бал на межі бюджету: "
            f"<b>{result['budget_border']:.3f}</b>"
        )

    lines.extend(
        [
            "",
            f"<b>Висновок:</b> {result['status']}",
        ]
    )

    return "\n".join(lines)


def get_short_speciality_name(full_name: str) -> str:
    """Витягує назви з лапок або повертає повний заголовок."""

    quoted_parts = re.findall(
        r'["«](.*?)["»]',
        full_name,
    )

    if quoted_parts:
        return " / ".join(quoted_parts)

    return full_name


def _pack_html_blocks(
    header: str,
    blocks: list[str],
    limit: int = 3800,
) -> list[str]:
    """Пакує цілі HTML-блоки в повідомлення без розриву тегів."""

    if not blocks:
        return [header]

    messages: list[str] = []
    current = header

    for block in blocks:
        candidate = f"{current}\n\n{block}"

        if len(candidate) <= limit:
            current = candidate
            continue

        messages.append(current)
        current = block

    if current:
        messages.append(current)

    return messages





def format_person_search_results(
    analysis: dict[str, Any],
) -> list[str]:
    """
    Формує короткий результат:
    назва спеціальності та місце у загальному конкурсі.
    """

    query = _safe(
        analysis.get("query", "")
    )

    if analysis.get("invalid_query"):
        return [
            "❌ Введіть щонайменше прізвище та ім’я.\n"
            "Наприклад: <b>Савчук Роман</b>"
        ]

    matches = analysis["matches"]

    header = (
        "🔎 <b>Результати пошуку</b>\n"
        f"Пошук: <b>{query}</b>\n\n"
        "Враховано лише рейтинги "
        "<b>«зарахування за конкурсом»</b>."
    )

    if not matches:
        messages = [
            f"{header}\n\n"
            "У підключених рейтингах збігів не знайдено."
        ]
    else:
        blocks: list[str] = []

        for number, match in enumerate(
            matches,
            start=1,
        ):
            speciality_name = _safe(
                get_short_speciality_name(
                    match["full_name"]
                )
            )

            blocks.append(
                f"<b>{number}. {speciality_name}</b>\n"
                f"Місце в рейтингу: "
                f"<b>{match['position']}</b>"
            )

        messages = _pack_html_blocks(
            (
                f"{header}\n"
                f"Знайдено рейтингів: "
                f"<b>{len(matches)}</b>"
            ),
            blocks,
        )

    if analysis["errors"]:
        messages.append(
            "⚠️ Не вдалося перевірити деякі сторінки."
        )

    return messages


def format_all_budget_specialities(
    analysis: dict[str, Any],
    score: float,
) -> list[str]:
    """Формує повідомлення зі всіма ОП, де є шанс на бюджет."""

    results = analysis["results"]

    definite = [
        result
        for result in results
        if get_budget_category(result) == "definite"
    ]

    border = [
        result
        for result in results
        if get_budget_category(result) == "border"
    ]

    definite.sort(key=lambda result: result["best_place"])
    border.sort(key=lambda result: result["best_place"])

    messages: list[str] = []

    header = (
        "✅ <b>Спеціальності, де ви проходите "
        "на державне замовлення</b>\n"
        f"Перевірений бал: <b>{score:.3f}</b>"
    )

    if definite:
        blocks = []

        for number, result in enumerate(definite, start=1):
            short_name = _safe(
                get_short_speciality_name(result["full_name"])
            )

            block_lines = [
                f"<b>{number}. {short_name}</b>",
                (
                    f"Місце: <b>{_place_text(result)} із "
                    f"{result['state_seats']}</b> бюджетних"
                ),
                (
                    "Учасників конкурсу на бюджет: "
                    f"{result['budget_applicants_count']}"
                ),
            ]

            if result["budget_border"] is not None:
                block_lines.append(
                    "Поточна межа бюджету: "
                    f"{result['budget_border']:.3f}"
                )

            blocks.append("\n".join(block_lines))

        messages.extend(_pack_html_blocks(header, blocks))

    else:
        messages.append(
            f"{header}\n\n"
            "Не знайдено програм із гарантованим "
            "потраплянням у межі держзамовлення."
        )

    if border:
        border_header = (
            "⚠️ <b>На межі державного замовлення "
            "через однакові бали</b>"
        )

        border_blocks = []

        for result in border:
            short_name = _safe(
                get_short_speciality_name(result["full_name"])
            )

            border_blocks.append(
                f"<b>{short_name}</b>\n"
                f"Можливі місця: "
                f"<b>{result['best_place']}–{result['worst_place']} "
                f"із {result['state_seats']}</b>"
            )

        messages.extend(
            _pack_html_blocks(border_header, border_blocks)
        )

    if analysis["errors"]:
        error_lines = [
            "⚠️ <b>Не вдалося перевірити деякі сторінки</b>",
            *[
                f"• {_safe(error)}"
                for error in analysis["errors"]
            ],
        ]
        messages.append("\n".join(error_lines))

    return messages

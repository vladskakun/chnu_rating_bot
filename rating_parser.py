from __future__ import annotations

import hashlib
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


def parse_priority_values(priority_text: str) -> set[int]:
    """
    Витягує окремі номери пріоритетів із комірки.

    Приклади:
    - "1" -> {1}
    - "2,7" -> {2, 7}
    - "1,6" -> {1, 6}
    - "10" -> {10}, а не {1, 0}
    """

    return {
        int(value)
        for value in re.findall(
            r"\d+",
            priority_text or "",
        )
    }


def has_priority_1_or_2(priority_text: str) -> bool:
    """Перевіряє, чи є у заяви пріоритет 1 або 2."""

    priorities = parse_priority_values(
        priority_text
    )

    return bool(
        priorities.intersection({1, 2})
    )


def filter_priority_1_2_applicants(
    applicants: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Залишає лише бюджетні заявки з пріоритетом 1 або 2.

    Заяви з позначкою ПЛ не враховуються.
    """

    return [
        applicant
        for applicant in applicants
        if (
            not applicant["contract_only"]
            and has_priority_1_or_2(
                applicant.get("priority", "")
            )
        )
    ]


def calculate_priority_1_2_forecast(
    applicants: list[dict[str, Any]],
    my_score: float,
) -> dict[str, int]:
    """
    Обчислює прогнозоване місце за балом,
    враховуючи лише заявки з пріоритетами 1–2.
    """

    filtered = filter_priority_1_2_applicants(
        applicants
    )

    forecast = calculate_forecast(
        filtered,
        my_score,
    )

    return {
        **forecast,
        "applicants_count": len(filtered),
    }


def calculate_person_priority_1_2_position(
    applicants: list[dict[str, Any]],
    person: dict[str, Any],
) -> dict[str, Any]:
    """
    Обчислює позицію людини серед заяв із пріоритетами 1–2.

    Якщо її власна заява має пріоритет 1/2 і не містить ПЛ,
    вона вже є частиною відфільтрованого рейтингу.

    Якщо пріоритет інший, повертається умовна позиція за балом,
    ніби цю людину додали до рейтингу пріоритетів 1–2.
    """

    filtered = filter_priority_1_2_applicants(
        applicants
    )

    person_is_included = (
        not person["contract_only"]
        and has_priority_1_or_2(
            person.get("priority", "")
        )
    )

    higher_count = sum(
        applicant["score"] > person["score"]
        for applicant in filtered
    )

    equal_count = sum(
        abs(
            applicant["score"] - person["score"]
        ) < 0.000001
        for applicant in filtered
        if applicant is not person
    )

    best_place = higher_count + 1
    worst_place = (
        higher_count
        + equal_count
        + 1
    )

    total_count = (
        len(filtered)
        if person_is_included
        else len(filtered) + 1
    )

    return {
        "best_place": best_place,
        "worst_place": worst_place,
        "total_count": total_count,
        "person_is_included": person_is_included,
    }


# ============================================================
# ПОШУК СПЕЦІАЛЬНОСТЕЙ І КІЛЬКОСТІ МІСЦЬ
# ============================================================


def get_tables_in_speciality_section(
    heading: Any,
) -> list[Any]:
    """
    Повертає всі таблиці між поточним h4 та наступним h4.

    На сторінці ЧНУ таблиці розташовані у такому порядку:
    квота 1, квота 2, загальний конкурс. Не кожна квота
    обов'язково присутня, але загальний конкурс є останнім.
    """

    tables: list[Any] = []
    seen_ids: set[int] = set()

    for element in heading.find_all_next(
        ["h4", "table"]
    ):
        if element.name == "h4":
            break

        if element.name != "table":
            continue

        # Не додаємо ту саму таблицю повторно.
        element_id = id(element)

        if element_id in seen_ids:
            continue

        seen_ids.add(element_id)
        tables.append(element)

    return tables


def get_competition_table(
    heading: Any,
) -> Any | None:
    """
    Повертає таблицю «зарахування за конкурсом».

    У кожній секції ЧНУ загальний конкурс іде після квот,
    тому беремо останню таблицю перед наступним h4.
    """

    tables = get_tables_in_speciality_section(
        heading
    )

    if not tables:
        return None

    return tables[-1]


def find_speciality_section(
    soup: BeautifulSoup,
    search_text: str,
) -> dict[str, Any] | None:
    """Знаходить спеціальність, держмісця та конкурсну таблицю."""

    normalized_search = normalize_text(
        search_text
    )

    for heading in soup.find_all("h4"):
        heading_text = heading.get_text(
            " ",
            strip=True,
        )

        if (
            normalized_search
            not in normalize_text(heading_text)
        ):
            continue

        seats_element = None

        for element in heading.find_all_next(
            ["h4", "h5"]
        ):
            if element.name == "h4":
                break

            element_text = normalize_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if "держзамовлення" in element_text:
                seats_element = element
                break

        return {
            "full_name": heading_text,
            "seats_element": seats_element,
            "table": get_competition_table(
                heading
            ),
        }

    return None


def make_all_speciality_key(
    url: str,
    full_name: str,
) -> str:
    """
    Створює стабільний ключ для ОП зі сторінки.

    Ключ не залежить від порядку програм і підходить
    для окремої історії останніх балів у SQLite.
    """

    source = (
        f"{url}|{normalize_text(full_name)}"
    ).encode("utf-8")

    digest = hashlib.sha1(
        source
    ).hexdigest()[:16]

    return f"all_{digest}"


def get_speciality_display_name(
    full_name: str,
) -> str:
    """Повертає коротку назву ОП для діалогу Telegram."""

    quoted_parts = re.findall(
        r'["«](.*?)["»]',
        full_name,
    )

    if quoted_parts:
        return " / ".join(quoted_parts)

    return full_name


def find_all_specialities(
    soup: BeautifulSoup,
    url: str,
    score: float | None = None,
) -> list[dict[str, Any]]:
    """
    Формує список усіх реальних ОП на сторінці.

    Додаються тільки секції, у яких знайдено таблицю
    загального конкурсу.
    """

    specialities: list[dict[str, Any]] = []

    for heading in soup.find_all("h4"):
        full_name = heading.get_text(
            " ",
            strip=True,
        )
        normalized_name = normalize_text(
            full_name
        )

        if re.search(
            r"(^|\s)оп(\s|[\"«])",
            normalized_name,
        ) is None:
            continue

        if get_competition_table(heading) is None:
            continue

        speciality = {
            "key": make_all_speciality_key(
                url,
                full_name,
            ),
            "name": get_speciality_display_name(
                full_name
            ),
            "full_name": full_name,
            "url": url,
            "search_text": full_name,
        }

        if score is not None:
            speciality["score"] = float(score)

        specialities.append(speciality)

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
    """Зчитує вступників із таблиці загального конкурсу."""

    applicants: list[dict[str, Any]] = []

    if table is None:
        return applicants

    for row in table.find_all("tr"):
        # Спочатку беремо прямі комірки рядка.
        cells = row.find_all(
            ["td", "th"],
            recursive=False,
        )

        # Fallback для нестандартної вкладеності HTML.
        if len(cells) < 3:
            cells = row.find_all(
                ["td", "th"]
            )

        values = [
            cell.get_text(
                " ",
                strip=True,
            )
            for cell in cells
        ]

        if len(values) < 3:
            continue

        position_match = re.fullmatch(
            r"\s*(\d+)\.?\s*",
            values[0].replace("\xa0", " "),
        )

        if position_match is None:
            continue

        score = parse_score(values[2])

        if score is None:
            continue

        name = values[1].strip()

        if not name:
            continue

        features = (
            values[3]
            if len(values) >= 4
            else ""
        )
        confirmed_place = (
            values[4]
            if len(values) >= 5
            else ""
        )
        priority = (
            values[5]
            if len(values) >= 6
            else ""
        )

        applicants.append(
            {
                "original_position": int(
                    position_match.group(1)
                ),
                "name": name,
                "score": score,
                "features": features,
                "confirmed_place": confirmed_place,
                "priority": priority,
                "contract_only": is_contract_only(
                    features
                ),
            }
        )

    # Зберігаємо рейтинговий порядок сторінки.
    applicants.sort(
        key=lambda applicant: (
            applicant["original_position"]
        )
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

    if not all_applicants:
        return {
            "success": False,
            "settings_name": settings["name"],
            "error": (
                "Таблицю загального конкурсу знайдено, "
                "але не вдалося прочитати жодної заявки. "
                "Перевірте актуальну структуру сторінки ЧНУ."
            ),
        }

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

    priority_1_2_forecast = (
        calculate_priority_1_2_forecast(
            all_applicants,
            settings["score"],
        )
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
        "priority_1_2_forecast": priority_1_2_forecast,
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

def discover_all_specialities() -> dict[str, Any]:
    """
    Завантажує всі ОП зі сторінок ALL_SPECIALTIES_URLS.

    Повертає серіалізований список налаштувань, який можна
    тимчасово зберігати у FSM aiogram.
    """

    specialities: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_keys: set[str] = set()

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

        for speciality in find_all_specialities(
            soup=soup,
            url=url,
        ):
            key = speciality["key"]

            if key in seen_keys:
                continue

            seen_keys.add(key)
            specialities.append(speciality)

    return {
        "specialities": specialities,
        "errors": errors,
    }


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
    Повертає кожну ОП, її конкурсну таблицю
    та кількість місць державного замовлення.

    Остання таблиця секції — загальний конкурс;
    квотні таблиці розміщені перед нею.
    """

    for heading in soup.find_all("h4"):
        full_name = heading.get_text(
            " ",
            strip=True,
        )
        normalized_heading = normalize_text(
            full_name
        )

        if (
            re.search(
                r"(^|\s)оп(\s|[\"«])",
                normalized_heading,
            )
            is None
        ):
            continue

        competition_table = get_competition_table(
            heading
        )

        if competition_table is None:
            continue

        seats_element = None

        for element in heading.find_all_next(
            ["h4", "h5"]
        ):
            if element.name == "h4":
                break

            element_text = normalize_text(
                element.get_text(
                    " ",
                    strip=True,
                )
            )

            if "держзамовлення" in element_text:
                seats_element = element
                break

        yield {
            "full_name": full_name,
            "table": competition_table,
            "state_seats": parse_state_seats(
                seats_element
            ),
        }



def search_applicant_in_all_ratings(
    person_query: str,
) -> dict[str, Any]:
    """
    Шукає вступника у всіх таблицях загального конкурсу.

    Для кожної знайденої заяви готується повний аналіз,
    подібний до аналізу за введеним балом.
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
    scanned_specialities = 0
    scanned_applicants = 0

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
            scanned_specialities += 1

            all_applicants = parse_applicants(
                section["table"]
            )
            scanned_applicants += len(
                all_applicants
            )

            budget_applicants = [
                applicant
                for applicant in all_applicants
                if not applicant["contract_only"]
            ]

            contract_only_count = sum(
                applicant["contract_only"]
                for applicant in all_applicants
            )

            state_seats = section.get(
                "state_seats"
            )

            budget_border = get_current_budget_border(
                budget_applicants,
                state_seats,
            )

            for applicant in all_applicants:
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

                # Якщо це бюджетна заява, тимчасово прибираємо
                # саму людину й додаємо назад через прогноз.
                # Так вона не рахується двічі.
                if applicant["contract_only"]:
                    comparison_applicants = list(
                        budget_applicants
                    )
                    place_label = (
                        "Умовне бюджетне місце за балом"
                    )
                else:
                    comparison_applicants = [
                        item
                        for item in budget_applicants
                        if item is not applicant
                    ]
                    place_label = (
                        "Місце серед бюджетних заяв за балом"
                    )

                forecast = calculate_forecast(
                    comparison_applicants,
                    applicant["score"],
                )

                nearby_applicants = get_nearby_applicants(
                    comparison_applicants,
                    applicant["score"],
                    count=NEIGHBORS_COUNT,
                )

                priority_position = (
                    calculate_person_priority_1_2_position(
                        all_applicants,
                        applicant,
                    )
                )

                matches.append(
                    {
                        "full_name": section["full_name"],
                        "name": applicant["name"],
                        "score": applicant["score"],
                        "position": applicant[
                            "original_position"
                        ],
                        "features": applicant.get(
                            "features",
                            "",
                        ),
                        "confirmed_place": applicant.get(
                            "confirmed_place",
                            "",
                        ),
                        "priority": applicant.get(
                            "priority",
                            "",
                        ),
                        "contract_only": applicant[
                            "contract_only"
                        ],
                        "state_seats": state_seats,
                        "all_applicants_count": len(
                            all_applicants
                        ),
                        "budget_applicants_count": len(
                            budget_applicants
                        ),
                        "contract_only_count": (
                            contract_only_count
                        ),
                        "budget_border": budget_border,
                        "nearby_applicants": (
                            nearby_applicants
                        ),
                        "priority_1_2_position": (
                            priority_position
                        ),
                        "place_label": place_label,
                        "status": get_budget_status(
                            forecast["best_place"],
                            forecast["worst_place"],
                            state_seats,
                        ),
                        **forecast,
                    }
                )

    matches.sort(
        key=lambda item: (
            normalize_text(
                item["full_name"]
            ),
            item["position"],
        )
    )

    return {
        "query": person_query.strip(),
        "matches": matches,
        "errors": errors,
        "invalid_query": False,
        "scanned_specialities": scanned_specialities,
        "scanned_applicants": scanned_applicants,
    }


def analyse_all_specialities(
    scores: float | dict[str, float],
    settings_list: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Аналізує всі ОП.

    Рекомендований режим — передати окремий бал у словнику
    для кожного key зі settings_list. Число підтримується
    лише для сумісності зі старими викликами.
    """

    results: list[dict[str, Any]] = []
    errors: list[str] = []

    if settings_list is None:
        discovery = discover_all_specialities()
        settings_list = discovery["specialities"]
        errors.extend(discovery["errors"])

    pages: dict[str, BeautifulSoup] = {}

    for speciality in settings_list:
        key = speciality["key"]

        if isinstance(scores, dict):
            score = scores.get(key)

            if score is None:
                errors.append(
                    "Не вказано бал для: "
                    f"{speciality['name']}"
                )
                continue
        else:
            score = scores

        url = speciality["url"]

        if url not in pages:
            try:
                pages[url] = get_page(
                    url,
                    force_refresh=True,
                )

            except requests.RequestException as error:
                errors.append(
                    f"{url}: {error}"
                )
                continue

        settings = {
            **speciality,
            "score": float(score),
        }

        result = analyse_speciality(
            pages[url],
            settings,
        )

        if result["success"]:
            results.append(result)
        else:
            errors.append(
                f"{speciality['name']}: "
                f"{result['error']}"
            )

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
        (
            "Місце лише серед заяв із пріоритетами 1–2: "
            f"<b>"
            f"{result['priority_1_2_forecast']['best_place']}"
            f"{'–' + str(result['priority_1_2_forecast']['worst_place']) if result['priority_1_2_forecast']['best_place'] != result['priority_1_2_forecast']['worst_place'] else ''}"
            f" із {result['priority_1_2_forecast']['applicants_count'] + 1}"
            f"</b>"
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
    Формує детальні картки знайдених заяв.

    Виведення наближене до аналізу за балом.
    """

    query = _safe(
        analysis.get(
            "query",
            "",
        )
    )

    if analysis.get("invalid_query"):
        return [
            "❌ Введіть щонайменше прізвище та ім’я.\n"
            "Наприклад: <b>Скакун Ерік</b>"
        ]

    matches = analysis["matches"]

    header = (
        "🔎 <b>Результати пошуку за ПІБ</b>\n"
        f"Пошук: <b>{query}</b>\n"
        "Враховано лише таблиці "
        "<b>«зарахування за конкурсом»</b>."
    )

    if not matches:
        scanned_applicants = analysis.get(
            "scanned_applicants",
            0,
        )

        if scanned_applicants == 0:
            messages = [
                f"{header}\n\n"
                "❌ Бот не зміг прочитати жодної заявки. "
                "Це технічна помилка парсера."
            ]
        else:
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
            applicant_name = _safe(
                match["name"]
            )

            state_seats = match.get(
                "state_seats"
            )
            seats_text = (
                str(state_seats)
                if state_seats is not None
                else "невідомо"
            )

            if (
                match["best_place"]
                == match["worst_place"]
            ):
                budget_place_text = str(
                    match["best_place"]
                )
            else:
                budget_place_text = (
                    f"{match['best_place']}"
                    f"–{match['worst_place']}"
                )

            priority_position = match[
                "priority_1_2_position"
            ]

            if (
                priority_position["best_place"]
                == priority_position["worst_place"]
            ):
                priority_place_text = str(
                    priority_position["best_place"]
                )
            else:
                priority_place_text = (
                    f"{priority_position['best_place']}"
                    f"–{priority_position['worst_place']}"
                )

            priority_label = (
                "Місце серед заяв із пріоритетами 1–2"
                if priority_position[
                    "person_is_included"
                ]
                else (
                    "Умовне місце за балом серед заяв "
                    "із пріоритетами 1–2"
                )
            )

            application_priority = _safe(
                match.get("priority")
                or "не вказано"
            )
            features = _safe(
                match.get("features")
                or "немає"
            )
            confirmed_place = _safe(
                match.get("confirmed_place")
                or "не вказано"
            )

            application_status = (
                "лише контракт — ПЛ"
                if match["contract_only"]
                else "бере участь у бюджетному конкурсі"
            )

            lines = [
                f"🎓 <b>{number}. {speciality_name}</b>",
                "",
                f"ПІБ: <b>{applicant_name}</b>",
                (
                    "Рейтинговий бал: "
                    f"<b>{match['score']:.3f}</b>"
                ),
                (
                    "Місце у таблиці загального конкурсу: "
                    f"<b>{match['position']} "
                    f"з {seats_text} бюджетних місць</b>"
                ),
                (
                    f"{match['place_label']}: "
                    f"<b>{budget_place_text} "
                    f"з {seats_text}</b>"
                ),
                (
                    f"{priority_label}: "
                    f"<b>{priority_place_text} "
                    f"із {priority_position['total_count']}</b>"
                ),
                "",
                (
                    "Статус заяви: "
                    f"<b>{application_status}</b>"
                ),
                (
                    "Пріоритет заяви: "
                    f"<b>{application_priority}</b>"
                ),
                (
                    "Пільги та особливості: "
                    f"<b>{features}</b>"
                ),
                (
                    "ПМН: "
                    f"<b>{confirmed_place}</b>"
                ),
                "",
                (
                    "Усього заяв у таблиці: "
                    f"<b>{match['all_applicants_count']}</b>"
                ),
                (
                    "Беруть участь у конкурсі на бюджет: "
                    f"<b>{match['budget_applicants_count']}</b>"
                ),
                (
                    "Заяв лише на контракт, ПЛ: "
                    f"<b>{match['contract_only_count']}</b>"
                ),
                "",
                "<b>Найближчі результати за балом</b>",
            ]

            nearby = match[
                "nearby_applicants"
            ]
            higher = nearby["higher"]
            lower = nearby["lower"]

            if higher:
                lines.append(
                    "🔼 Вище:"
                )

                for applicant in higher:
                    lines.append(
                        f"• {applicant['forecast_position']} місце — "
                        f"{applicant['score']:.3f} — "
                        f"{_safe(applicant['name'])}"
                    )
            else:
                lines.append(
                    "🔼 Вище немає вступників."
                )

            lines.extend(
                [
                    "",
                    (
                        f"➡️ <b>{budget_place_text} місце — "
                        f"{match['score']:.3f} — "
                        f"{applicant_name}</b>"
                    ),
                    "",
                ]
            )

            if lower:
                lines.append(
                    "🔽 Нижче:"
                )

                for applicant in lower:
                    lines.append(
                        f"• {applicant['forecast_position']} місце — "
                        f"{applicant['score']:.3f} — "
                        f"{_safe(applicant['name'])}"
                    )
            else:
                lines.append(
                    "🔽 Нижче немає вступників."
                )

            lines.append("")

            if match["budget_border"] is None:
                lines.append(
                    "Поточна межа бюджету ще не визначена: "
                    "заяв менше, ніж бюджетних місць."
                )
            else:
                lines.append(
                    "Поточний бал на межі бюджету: "
                    f"<b>{match['budget_border']:.3f}</b>"
                )

            lines.extend(
                [
                    "",
                    f"<b>Висновок:</b> {match['status']}",
                ]
            )

            blocks.append(
                "\n".join(lines)
            )

        messages = [
            (
                f"{header}\n"
                f"Знайдено заяв: "
                f"<b>{len(matches)}</b>\n\n"
                "Кожний результат надіслано "
                "окремим повідомленням."
            ),
            *blocks,
        ]

    if analysis["errors"]:
        messages.append(
            "⚠️ <b>Не вдалося перевірити деякі сторінки</b>\n"
            + "\n".join(
                f"• {_safe(error)}"
                for error in analysis["errors"]
            )
        )

    return messages


def format_all_budget_specialities(
    analysis: dict[str, Any],
) -> list[str]:
    """
    Формує результат для ВСІХ проаналізованих програм.

    Програми поділяються на:
    - проходиш на бюджет;
    - перебуваєш на межі;
    - не проходиш за поточним рейтингом.

    Назву функції залишено старою для сумісності з bot.py.
    """

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

    outside = [
        result
        for result in results
        if get_budget_category(result) == "outside"
    ]

    unknown = [
        result
        for result in results
        if get_budget_category(result) == "unknown"
    ]

    definite.sort(
        key=lambda result: (
            result["best_place"],
            normalize_text(result["full_name"]),
        )
    )

    border.sort(
        key=lambda result: (
            result["best_place"],
            normalize_text(result["full_name"]),
        )
    )

    outside.sort(
        key=lambda result: (
            result["best_place"],
            normalize_text(result["full_name"]),
        )
    )

    unknown.sort(
        key=lambda result: normalize_text(
            result.get(
                "full_name",
                result.get(
                    "settings_name",
                    "",
                ),
            )
        )
    )

    total_count = len(results)

    summary = (
        "📊 <b>Результати перевірки всіх спеціальностей</b>\n"
        "Для кожної програми використано окремий бал.\n\n"
        f"Усього проаналізовано: <b>{total_count}</b>\n"
        f"✅ Проходиш на бюджет: <b>{len(definite)}</b>\n"
        f"⚠️ На межі бюджету: <b>{len(border)}</b>\n"
        f"❌ Поза бюджетом: <b>{len(outside)}</b>"
    )

    messages: list[str] = [summary]

    def make_result_block(
        result: dict[str, Any],
        number: int,
        category: str,
    ) -> str:
        short_name = _safe(
            get_short_speciality_name(
                result["full_name"]
            )
        )

        state_seats = result.get(
            "state_seats"
        )

        seats_text = (
            str(state_seats)
            if state_seats is not None
            else "невідомо"
        )

        place_text = _place_text(
            result
        )

        priority_forecast = result.get(
            "priority_1_2_forecast",
            {},
        )

        priority_best = priority_forecast.get(
            "best_place"
        )
        priority_worst = priority_forecast.get(
            "worst_place"
        )

        if priority_best is None:
            priority_place_text = "невідомо"
        elif priority_best == priority_worst:
            priority_place_text = str(
                priority_best
            )
        else:
            priority_place_text = (
                f"{priority_best}–{priority_worst}"
            )

        category_icon = {
            "definite": "✅",
            "border": "⚠️",
            "outside": "❌",
            "unknown": "❔",
        }.get(
            category,
            "•",
        )

        lines = [
            (
                f"{category_icon} <b>{number}. "
                f"{short_name}</b>"
            ),
            (
                f"Ваш бал: "
                f"<b>{result['score']:.3f}</b>"
            ),
            (
                "Місце у загальному рейтингу: "
                f"<b>{place_text} із {seats_text} "
                "бюджетних</b>"
            ),
            (
                "Місце серед заяв із пріоритетами 1–2: "
                f"<b>{priority_place_text}</b>"
            ),
            (
                "Учасників конкурсу на бюджет: "
                f"<b>{result['budget_applicants_count']}</b>"
            ),
        ]

        if result.get("budget_border") is not None:
            lines.append(
                "Поточна межа бюджету: "
                f"<b>{result['budget_border']:.3f}</b>"
            )

        if category == "definite":
            lines.append(
                "Висновок: "
                "<b>у межах держзамовлення</b>"
            )

        elif category == "border":
            lines.append(
                "Висновок: "
                "<b>на межі через однакові бали</b>"
            )

        elif category == "outside":
            places_below = (
                result["best_place"]
                - state_seats
                if state_seats is not None
                else None
            )

            if places_below is not None:
                lines.append(
                    "До межі бюджету за найкращим місцем: "
                    f"<b>{places_below}</b>"
                )

            lines.append(
                "Висновок: "
                "<b>поза межами держзамовлення</b>"
            )

        return "\n".join(
            lines
        )

    if definite:
        definite_blocks = [
            make_result_block(
                result,
                number,
                "definite",
            )
            for number, result in enumerate(
                definite,
                start=1,
            )
        ]

        messages.extend(
            _pack_html_blocks(
                "✅ <b>ПРОХОДИШ НА ДЕРЖАВНЕ ЗАМОВЛЕННЯ</b>",
                definite_blocks,
            )
        )
    else:
        messages.append(
            "✅ <b>ПРОХОДИШ НА ДЕРЖАВНЕ ЗАМОВЛЕННЯ</b>\n\n"
            "Таких спеціальностей не знайдено."
        )

    if border:
        border_blocks = [
            make_result_block(
                result,
                number,
                "border",
            )
            for number, result in enumerate(
                border,
                start=1,
            )
        ]

        messages.extend(
            _pack_html_blocks(
                "⚠️ <b>НА МЕЖІ ДЕРЖАВНОГО ЗАМОВЛЕННЯ</b>",
                border_blocks,
            )
        )

    if outside:
        outside_blocks = [
            make_result_block(
                result,
                number,
                "outside",
            )
            for number, result in enumerate(
                outside,
                start=1,
            )
        ]

        messages.extend(
            _pack_html_blocks(
                "❌ <b>ПОЗА МЕЖАМИ ДЕРЖАВНОГО ЗАМОВЛЕННЯ</b>",
                outside_blocks,
            )
        )

    if unknown:
        unknown_blocks = []

        for number, result in enumerate(
            unknown,
            start=1,
        ):
            name = _safe(
                result.get(
                    "full_name",
                    result.get(
                        "settings_name",
                        "Невідома спеціальність",
                    ),
                )
            )

            unknown_blocks.append(
                f"❔ <b>{number}. {name}</b>\n"
                "Не вдалося визначити бюджетний статус."
            )

        messages.extend(
            _pack_html_blocks(
                "❔ <b>СТАТУС НЕ ВИЗНАЧЕНО</b>",
                unknown_blocks,
            )
        )

    if analysis["errors"]:
        error_blocks = [
            f"• {_safe(error)}"
            for error in analysis["errors"]
        ]

        messages.extend(
            _pack_html_blocks(
                "⚠️ <b>ПОМИЛКИ ПІД ЧАС АНАЛІЗУ</b>",
                error_blocks,
            )
        )

    return messages

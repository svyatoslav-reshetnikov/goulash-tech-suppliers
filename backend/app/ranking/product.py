"""Does a text really name the searched product? Stricter than the substring check used by the agent.

A phrase ("Рис для суши", synonym "Рис", "Сыр моцарелла") matches when **all** of its content words are present.
Short words (3 letters, like "рис") must match as a whole word with a case ending, so "рисовый уксус" is not rice.
"""

import re

from app.agent.validation import norm_text

_STOP = {"для", "без", "или", "под", "при", "и", "в", "на", "с"}
_ENDINGS = r"(?:а|у|ы|е|ом|ой|ов|ам|ами|ах)?"
# generic claims that say nothing about a concrete product
GENERIC = re.compile(
    r"полный\s+ассортимент|широкий\s+ассортимент|все\s+для|продукт\w*\s+питания|продукт\w*\s+для\s+(?:ресторан|кафе|horeca|общепит)"
    r"|(?:свыше|более|от)\s+[\d\s]+\s+(?:товар|позици)|маркетплейс"
)


def _words(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[a-zа-я0-9]+", norm_text(phrase)) if w not in _STOP and len(w) >= 3]


def _has_word(folded: str, word: str) -> bool:
    if len(word) <= 3:
        return re.search(rf"\b{re.escape(word)}{_ENDINGS}\b", folded) is not None
    return re.search(rf"\b{re.escape(word[: max(4, len(word) - 2)])}", folded) is not None


def matches_phrase(text: str, phrase: str) -> bool:
    """All content words of the phrase are present in the text."""
    words = _words(phrase)
    folded = norm_text(text)
    return bool(words) and all(_has_word(folded, w) for w in words)


def first_matching_phrase(text: str, phrases: tuple[str, ...]) -> str | None:
    return next((p for p in phrases if matches_phrase(text, p)), None)


def partially_matches(text: str, phrases: tuple[str, ...]) -> bool:
    """Some, but not all, words of some phrase are present ("для суши" without "рис")."""
    folded = norm_text(text)
    return any(any(_has_word(folded, w) for w in _words(p)) for p in phrases)


def is_generic(text: str) -> bool:
    return GENERIC.search(norm_text(text)) is not None

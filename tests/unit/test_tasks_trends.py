from __future__ import annotations

from api.tasks.trends import _wikipedia_article_guess


class TestWikipediaArticleGuess:
    def test_single_word_is_capitalized(self) -> None:
        assert _wikipedia_article_guess("expensify") == "Expensify"

    def test_multi_word_keyword_joins_with_underscore(self) -> None:
        assert _wikipedia_article_guess("expense tracker") == "Expense_Tracker"

    def test_extra_whitespace_is_stripped(self) -> None:
        assert _wikipedia_article_guess("  ai   crm  ") == "Ai_Crm"

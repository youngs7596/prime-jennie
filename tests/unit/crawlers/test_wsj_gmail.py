"""WSJ Gmail crawler unit tests."""

from prime_jennie.infra.crawlers.wsj_gmail import (
    WSJBriefing,
    WSJNewsletter,
    _classify_newsletter,
    _clean_body,
)


class TestClassifyNewsletter:
    def test_10_point(self):
        assert _classify_newsletter("The 10-Point: A 'Fight' at the Pentagon") == "10-point"

    def test_markets_am(self):
        assert _classify_newsletter("Markets A.M.: What Hotels Tell Us") == "markets-am"

    def test_markets_pm(self):
        assert _classify_newsletter("Markets P.M.: Stocks Tumble") == "markets-pm"

    def test_whats_news(self):
        assert _classify_newsletter("What's News: Top Stories") == "whats-news"

    def test_unknown(self):
        assert _classify_newsletter("Welcome to WSJ") is None

    def test_emoji_subject(self):
        assert _classify_newsletter("🪖 Markets A.M.: Do You Need War Insurance?") == "markets-am"


class TestCleanBody:
    def test_removes_urls(self):
        text = "Read more [https://www.wsj.com/article/123] about it."
        result = _clean_body(text)
        assert "https://" not in result
        assert "Read more" in result

    def test_removes_footer(self):
        text = "Main content here.\n\nUnsubscribe from this newsletter."
        result = _clean_body(text)
        assert "Unsubscribe" not in result
        assert "Main content" in result

    def test_removes_copyright(self):
        text = "Content.\n\nCopyright 2026 Dow Jones & Company, Inc. All Rights Reserved."
        result = _clean_body(text)
        assert "Copyright" not in result

    def test_cleans_narrow_spaces(self):
        text = "Hello\u200b\u200bworld\u00a0here"
        result = _clean_body(text)
        assert "\u200b" not in result


class TestWSJBriefing:
    def test_to_text_empty(self):
        briefing = WSJBriefing()
        assert briefing.to_text() == ""

    def test_to_text_single(self):
        nl = WSJNewsletter(
            newsletter_type="10-point",
            subject="The 10-Point: Test",
            body="Test content here.",
            email_date="Mon, 3 Mar 2026",
        )
        briefing = WSJBriefing(newsletters=[nl])
        text = briefing.to_text()
        assert "WSJ The 10-Point" in text
        assert "Test content here." in text

    def test_to_text_multiple(self):
        nls = [
            WSJNewsletter("10-point", "The 10-Point: A", "Content A.", "date1"),
            WSJNewsletter("markets-am", "Markets A.M.: B", "Content B.", "date2"),
        ]
        briefing = WSJBriefing(newsletters=nls)
        text = briefing.to_text()
        assert "WSJ The 10-Point" in text
        assert "WSJ Markets A.M." in text
        assert "Content A." in text
        assert "Content B." in text

    def test_no_credentials_file_returns_empty(self):
        """fetch_wsj_briefing with missing credentials.json returns empty."""
        from prime_jennie.infra.crawlers.wsj_gmail import fetch_wsj_briefing

        result = fetch_wsj_briefing(credentials_path="/nonexistent/credentials.json")
        assert result.newsletters == []
        assert result.to_text() == ""

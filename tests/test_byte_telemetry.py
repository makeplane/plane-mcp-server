"""Tests for input_bytes / output_bytes telemetry in PlaneLoggingMiddleware."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from plane_mcp.middleware import PlaneLoggingMiddleware, _byte_size


class TestByteSize:
    """Pure unit tests for _byte_size helper."""

    def test_string(self):
        assert _byte_size("hello") == len('"hello"'.encode("utf-8"))

    def test_dict(self):
        assert _byte_size({"a": 1}) == len('{"a":1}'.encode("utf-8"))

    def test_int(self):
        assert _byte_size(42) == len("42".encode("utf-8"))


class TestByteTelemetry:
    """Unit tests for byte telemetry on log records."""

    @pytest.fixture
    def middleware(self):
        """PlaneLoggingMiddleware with all methods enabled, capturing log records."""
        mw = PlaneLoggingMiddleware(methods=["tools/call"])
        records = []

        def capture(msg, level=None):
            records.append(msg)

        mw._log_message = capture
        return mw, records

    def _mock_context(self, tool_name: str, action: str | None = None) -> MagicMock:
        """A minimal MiddlewareContext for tools/call."""
        ctx = MagicMock()
        ctx.method = "tools/call"
        msg = MagicMock()
        msg.name = tool_name
        args = {"action": action} if action else {}
        msg.arguments = args
        ctx.message = msg
        return ctx

    def test_success_record_has_input_and_output_bytes(self, middleware):
        mw, records = middleware
        ctx = self._mock_context("workitem", "list")

        async def call_next(_):
            return {"content": "result"}

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(mw.on_message(ctx, call_next))

        assert len(records) == 2  # _start, _success
        start_record = records[0]
        success_record = records[1]

        assert "input_bytes" in start_record
        assert "output_bytes" not in start_record
        assert isinstance(start_record["input_bytes"], int)

        assert "input_bytes" in success_record
        assert "output_bytes" in success_record
        assert isinstance(success_record["input_bytes"], int)
        assert isinstance(success_record["output_bytes"], int)
        assert success_record["output_bytes"] == _byte_size(result)

    def test_error_record_has_input_bytes_only(self, middleware):
        mw, records = middleware
        ctx = self._mock_context("workitem", "list")

        class CustomError(Exception):
            pass

        async def call_next(_):
            raise CustomError("boom")

        import asyncio

        with pytest.raises(CustomError):
            asyncio.get_event_loop().run_until_complete(mw.on_message(ctx, call_next))

        assert len(records) == 2  # _start, _error
        start_record = records[0]
        error_record = records[1]

        assert "input_bytes" in start_record
        assert "output_bytes" not in start_record

        assert "input_bytes" in error_record
        assert "output_bytes" not in error_record

    def test_operation_fields_present_on_success_record(self, middleware):
        mw, records = middleware
        ctx = self._mock_context("workitem", "list")

        async def call_next(_):
            return {"content": "result"}

        import asyncio

        asyncio.get_event_loop().run_until_complete(mw.on_message(ctx, call_next))

        success_record = records[1]
        assert success_record["tool"] == "workitem"
        assert success_record["resource"] == "workitem"
        assert success_record["action"] == "list"

    def test_non_tools_call_method_passes_through(self, middleware):
        mw, records = middleware
        ctx = MagicMock()
        ctx.method = "resources/list"
        ctx.message = MagicMock()

        async def call_next(_):
            return {"content": "resource"}

        import asyncio

        result = asyncio.get_event_loop().run_until_complete(mw.on_message(ctx, call_next))

        assert result == {"content": "resource"}
        # No log records for non-instrumented methods
        assert len(records) == 0

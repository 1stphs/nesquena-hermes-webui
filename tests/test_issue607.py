"""Tests for PR #648 — Gemma 4 thinking token stripping (closes #607)."""
import re
import pathlib
import pytest


# ---------------------------------------------------------------------------
# _strip_thinking_markup tests
# ---------------------------------------------------------------------------

from api.streaming import _strip_thinking_markup, _looks_invalid_generated_title


class TestGemma4ThinkingTokenStrip:
    """Verify that <|turn|>thinking\n...\n<turn|> blocks are stripped."""

    def test_strip_gemma4_basic(self):
        """Basic Gemma 4 thinking block stripped, answer kept."""
        raw = "<|turn|>thinking\nSome internal reasoning\n<turn|>Final answer"
        result = _strip_thinking_markup(raw)
        assert result == "Final answer"

    def test_strip_gemma4_multiline_reasoning(self):
        """Multi-line reasoning block stripped cleanly."""
        raw = "<|turn|>thinking\nLine 1\nLine 2\nLine 3\n<turn|>Answer here"
        result = _strip_thinking_markup(raw)
        assert result == "Answer here"

    def test_strip_gemma4_no_thinking_passthrough(self):
        """Normal response without thinking tokens passes through unchanged."""
        raw = "Normal response without thinking tokens"
        result = _strip_thinking_markup(raw)
        assert result == raw

    def test_strip_gemma4_with_leading_whitespace(self):
        """Leading whitespace before the thinking block is handled."""
        raw = "\n\n<|turn|>thinking\nReasoning\n<turn|>Answer"
        result = _strip_thinking_markup(raw)
        assert result == "Answer"

    def test_strip_gemma4_empty_reasoning(self):
        """Empty reasoning block (just delimiters) is stripped."""
        raw = "<|turn|>thinking\n<turn|>Response"
        result = _strip_thinking_markup(raw)
        assert result == "Response"

    def test_strip_gemma4_case_insensitive(self):
        """Pattern is case-insensitive (though Gemma 4 uses fixed case)."""
        raw = "<|TURN|>THINKING\nreasoning\n<TURN|>answer"
        result = _strip_thinking_markup(raw)
        # The regex uses re.IGNORECASE — should strip uppercase variant too
        assert "THINKING" not in result
        assert "reasoning" not in result

    def test_existing_think_tag_still_works(self):
        """Ensure <think>...</think> still stripped (no regression)."""
        raw = "<think>inner reasoning</think>Final"
        result = _strip_thinking_markup(raw)
        assert result == "Final"

    def test_existing_channel_tag_still_works(self):
        """Ensure <|channel|>thought...</channel|> still stripped."""
        raw = "<|channel|>thoughtSome reasoning<channel|>Answer"
        result = _strip_thinking_markup(raw)
        assert result == "Answer"


class TestGemma4TitleLeakDetection:
    """Verify _looks_invalid_generated_title catches Gemma 4 leak."""

    def test_detects_gemma4_leak_in_title(self):
        raw = "<|turn|>thinking\nUser asked about X\n<turn|>Session Title"
        assert _looks_invalid_generated_title(raw) is True

    def test_clean_title_not_flagged(self):
        assert _looks_invalid_generated_title("Python debugging session") is False

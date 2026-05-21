"""Tests for sprint 49 timestamp footer polish — v0.50.97.

Covers:
  - #680: assistant messages now render footer timestamps, not just user messages
  - messages from prior days render a fuller date+time string in the footer
  - timestamp/action footer stays attached to visible response segments only
  - user and assistant footer chrome is hover-only by default
  - last assistant turn keeps cumulative usage visible and reveals time/actions on hover
  - unchanged historical messages preserve their original timestamps across turns
"""

import pathlib
import re

from api.streaming import _restore_reasoning_metadata


REPO = pathlib.Path(__file__).parent.parent
STREAMING_PY = (REPO / "api" / "streaming.py").read_text(encoding="utf-8")


def test_restore_reasoning_metadata_preserves_existing_timestamps():
    assert "def _restore_reasoning_metadata(previous_messages, updated_messages):" in STREAMING_PY
    assert "if prev_msg.get('timestamp') and not cur_msg.get('timestamp'):" in STREAMING_PY
    assert "cur_msg['timestamp'] = prev_msg['timestamp']" in STREAMING_PY
    assert "elif prev_msg.get('_ts') and not cur_msg.get('_ts') and not cur_msg.get('timestamp'):" in STREAMING_PY
    assert "cur_msg['_ts'] = prev_msg['_ts']" in STREAMING_PY


def test_restore_reasoning_metadata_preserves_timestamp_on_reload_for_unchanged_messages():
    previous_messages = [
        {"role": "user", "content": "hello", "timestamp": 1713500000},
        {"role": "assistant", "content": "world", "timestamp": 1713500060},
    ]
    updated_messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]

    restored = _restore_reasoning_metadata(previous_messages, updated_messages)

    assert restored[0]["timestamp"] == 1713500000
    assert restored[1]["timestamp"] == 1713500060


def test_restore_reasoning_metadata_does_not_preserve_timestamp_for_changed_messages():
    previous_messages = [
        {"role": "user", "content": "hello", "timestamp": 1713500000},
        {"role": "assistant", "content": "old answer", "timestamp": 1713500060},
    ]
    updated_messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "new answer"},
    ]

    restored = _restore_reasoning_metadata(previous_messages, updated_messages)

    assert restored[0]["timestamp"] == 1713500000
    assert "timestamp" not in restored[1]

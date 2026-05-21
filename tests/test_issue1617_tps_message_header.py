"""Regression coverage for issue #1617: TPS belongs on message headers.

Product decision:
- show live TPS in the assistant message header while streaming when real TPS is available;
- persist/show the final TPS at the end of the turn;
- do not show placeholder or estimated TPS when unavailable.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_PY = (REPO / "api" / "config.py").read_text(encoding="utf-8")
STREAMING_PY = (REPO / "api" / "streaming.py").read_text(encoding="utf-8")
def test_backend_marks_streaming_metering_availability_explicitly():
    assert "tps_available" in STREAMING_PY, "metering SSE payloads must explicitly say whether TPS is displayable"
    assert "estimated" in STREAMING_PY, "metering SSE payloads must explicitly distinguish estimated readings"
    assert "record_token(stream_id, len(STREAM_PARTIAL_TEXT[stream_id]))" not in STREAMING_PY, (
        "live TPS must not be derived from streamed character count / byte-size estimates"
    )

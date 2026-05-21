"""Sprint 20 API regression tests."""

def test_routes_define_transcribe_endpoint():
    """Server routes must expose /api/transcribe for MediaRecorder fallback uploads."""
    from tests.route_source import read_route_sources
    routes = read_route_sources()
    assert '"/api/transcribe"' in routes

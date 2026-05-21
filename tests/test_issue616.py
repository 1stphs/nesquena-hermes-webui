import pathlib

from tests.route_source import read_route_sources


def test_workspace_suggest_endpoint_is_wired():
    src = read_route_sources()
    assert '"/api/workspaces/suggest"' in src

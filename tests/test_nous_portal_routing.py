"""Regression tests for Nous portal model routing bugs (issue #854).

Two bugs fixed:
1. Nous static model IDs were bare names (claude-opus-4.6) instead of
   slash-prefixed (anthropic/claude-opus-4.6), causing Nous to reject them.
2. resolve_model_provider() routed slash-prefixed cross-namespace models
   through OpenRouter instead of the configured portal provider.

Invariant: when a portal provider (Nous, OpenCode) is active, the full
slash-prefixed model ID MUST be preserved end-to-end — portals use the
provider/model path as the canonical name at their inference endpoint.
Stripping the prefix to a bare name is exactly Bug 1, so the fix for Bug 2
must not reintroduce it.
"""
import sys
import types


def _models_with_provider(provider, monkeypatch):
    """Patch config.cfg to simulate an active provider, return resolve_model_provider."""
    import api.config as config

    old = dict(config.cfg)
    config.cfg.clear()
    config.cfg["model"] = {"provider": provider}
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0
    try:
        from api.config import resolve_model_provider
        return resolve_model_provider
    finally:
        config.cfg.clear()
        config.cfg.update(old)


class TestNousModelIds:
    """Nous static model IDs must use @nous: prefix for explicit portal routing."""

    def test_nous_models_use_at_prefix(self):
        """All Nous static models must carry the @nous: explicit provider prefix.

        This ensures they route through the @provider:model branch of
        resolve_model_provider() — identical to the live-fetched path — rather
        than relying on the slash-only portal provider guard.
        """
        from api.config import _PROVIDER_MODELS
        nous_models = _PROVIDER_MODELS.get("nous", [])
        assert nous_models, "Nous must have at least one static model"
        for m in nous_models:
            mid = m["id"]
            assert mid.startswith("@nous:"), (
                f"Nous model '{mid}' must start with '@nous:' "
                f"(e.g. @nous:anthropic/claude-opus-4.6) so it routes through "
                f"the explicit provider hint branch, not the weaker portal guard."
            )

    def test_nous_known_models_present(self):
        """Key Nous models must be present with correct @nous:-prefixed IDs."""
        from api.config import _PROVIDER_MODELS
        nous_ids = {m["id"] for m in _PROVIDER_MODELS.get("nous", [])}
        assert "@nous:anthropic/claude-opus-4.6" in nous_ids, (
            "@nous:anthropic/claude-opus-4.6 must be in Nous model list"
        )
        assert "@nous:anthropic/claude-sonnet-4.6" in nous_ids, (
            "@nous:anthropic/claude-sonnet-4.6 must be in Nous model list"
        )
        assert "@nous:openai/gpt-5.4-mini" in nous_ids, (
            "@nous:openai/gpt-5.4-mini must be in Nous model list"
        )

    def test_nous_models_no_bare_or_slash_only(self):
        """No Nous static model should be bare or slash-only without @nous: prefix."""
        from api.config import _PROVIDER_MODELS
        bad_ids = {
            "claude-opus-4.6", "claude-sonnet-4.6", "gpt-5.4-mini",
            "gemini-3.1-pro-preview",
            "anthropic/claude-opus-4.6", "anthropic/claude-sonnet-4.6",
            "openai/gpt-5.4-mini", "google/gemini-3.1-pro-preview",
        }
        nous_ids = {m["id"] for m in _PROVIDER_MODELS.get("nous", [])}
        for bad in bad_ids:
            assert bad not in nous_ids, (
                f"Model ID '{bad}' found in Nous static list without @nous: prefix. "
                f"Use '@nous:{bad}' so routing matches the live-fetched path."
            )

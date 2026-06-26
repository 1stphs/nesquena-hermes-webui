"""Compatibility helpers for relocated api modules."""

from __future__ import annotations

import importlib
import sys


def alias_module(module_name: str, target_module_name: str) -> None:
    """Make *module_name* behave exactly like *target_module_name*.

    This preserves monkeypatch semantics because callers receive the real
    target module object instead of a copied namespace.
    """
    module = importlib.import_module(target_module_name)
    sys.modules[module_name] = module


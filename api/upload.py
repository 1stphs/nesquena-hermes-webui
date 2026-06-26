"""Compatibility wrapper for moved module."""

from api._compat import alias_module as _alias_module

_alias_module(__name__, "api.features.files.upload")

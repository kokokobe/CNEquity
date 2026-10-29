"""Config errors whose message is safe to print."""

from __future__ import annotations


class ConfigError(ValueError):
    """A config problem named by key and path only, never by value.

    The CLI prints these as they are. Any other ``ValueError`` from loading is
    reduced to its type, because a parser message can quote a token or proxy
    straight out of the TOML.
    """

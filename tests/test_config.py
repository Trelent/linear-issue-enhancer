"""Tests for sync config helpers."""

import os
import pytest


class TestIsInternalEmail:
    """Tests for the is_internal_email function."""

    def test_internal_email_matches(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_DOMAINS", "acme.com,example.org")
        
        # Re-import to pick up new env var
        import importlib
        from src.sync import config
        importlib.reload(config)
        
        assert config.is_internal_email("alice@acme.com") is True
        assert config.is_internal_email("bob@example.org") is True

    def test_external_email_does_not_match(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_DOMAINS", "acme.com")
        
        import importlib
        from src.sync import config
        importlib.reload(config)
        
        assert config.is_internal_email("external@gmail.com") is False

    def test_empty_email_returns_false(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_DOMAINS", "acme.com")
        
        import importlib
        from src.sync import config
        importlib.reload(config)
        
        assert config.is_internal_email("") is False
        assert config.is_internal_email("not-an-email") is False

    def test_case_insensitive_domain_matching(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_DOMAINS", "acme.com")
        
        import importlib
        from src.sync import config
        importlib.reload(config)
        
        # Domain comparison is lowercase
        assert config.is_internal_email("alice@ACME.COM") is True

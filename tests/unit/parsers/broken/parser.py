"""Fixture parser that blows up at import time; discovery must record it as broken."""
raise RuntimeError("boom")

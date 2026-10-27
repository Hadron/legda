"""Portable provision-extraction logic.

This package is intended to be reusable inside the IRS system with minimal
change. It must not import from the local-only harness (``local``) or hard
depend on the graph integration beyond the shared data models.
"""

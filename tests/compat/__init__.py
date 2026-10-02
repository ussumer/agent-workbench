"""Compatibility experiments run before the complex integration work (T06).

The modules here answer one question each, with real evidence attached:

* :mod:`tests.compat.signatures` — what the installed classes and functions actually
  accept, so later tasks are written against the pinned versions rather than a memory
  of the documentation.
* :mod:`tests.compat.streaming` — how the v2 stream is shaped, including the raw
  tool-argument fragments before they are normalised.
* :mod:`tests.compat.capabilities` — the eight CAP experiments from
  ``docs/plan/dependencies.md``, each returning a redacted record.
"""

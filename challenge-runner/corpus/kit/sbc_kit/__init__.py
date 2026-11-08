"""Shared verifier kit for corpus entries.

Library calls that decide a verdict run in a forked child that re-imports a clean
copy of the library from /work and refuses to run if the library's source files
changed since the session started. Captures are written by the parent from what
the child reported, never from a test's assertions.
"""

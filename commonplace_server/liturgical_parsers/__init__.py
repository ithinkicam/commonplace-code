"""Liturgical parsers for the Commonplace liturgical ingest pipeline.

Each parser is a pure function: HTML/PDF in → structured dataclasses out.
No DB writes, no pipeline calls, no job-queue integration.
"""

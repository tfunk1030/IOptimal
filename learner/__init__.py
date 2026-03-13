"""Learner — cumulative knowledge system for IOptimal.

Every IBT session is an experiment. The learner extracts what changed (setup,
conditions, driving) and what resulted (performance, handling, telemetry),
then stores that as a structured observation. Over time, observations compound
into empirical models that refine the physics solver's predictions.

Architecture:
    knowledge_store.py  — JSON-based persistent storage for all learnings
    observation.py      — Extract a structured observation from one IBT analysis
    delta_detector.py   — Compare consecutive sessions to find what changed & what resulted
    empirical_models.py — Fit lightweight models from accumulated observations
    recall.py           — Query interface: "what do we know about X?"
    ingest.py           — CLI entry point: analyze IBT → store observation → update models
"""

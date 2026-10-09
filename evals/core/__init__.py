"""Shared floor of the evals package.

Modules here may import only other ``evals.core`` modules (plus the
standard library and third-party packages). They must not import
runners, drivers, seeders, tasks, report code, or the recording proxy.
Callers import ``evals.core.<module>`` directly — this package does not
re-export its submodules.
"""

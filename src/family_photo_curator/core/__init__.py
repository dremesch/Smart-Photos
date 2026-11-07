"""Core domain and pipeline logic.

Pure, headless and deterministic for fixed inputs and versions. Defines ports
(interfaces) that adapters implement. Must not import ui, services, adapters,
persistence or workers.
"""

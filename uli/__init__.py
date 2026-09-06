"""Universal Log Intelligence (ULI).

Package layout mirrors the architecture (see docs/architecture.md):
ingestion → fingerprint → parsers → normalization → storage; detection (unknown),
drift (parser + resource), ml (optional sidecar), api, workers.
"""

__version__ = "0.1.0"
ENVELOPE_SCHEMA_VERSION = "uli.v1"
OCSF_VERSION = "1.9.0"
NORMALIZER_VERSION = "1.0.0"

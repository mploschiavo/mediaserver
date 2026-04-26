"""Download-client-adapter application layer — registry / lookup.

ADR-0002 Phase 16-E (cross-cutting download_client_adapters). Houses the
factory that resolves a concrete adapter class by service id from the
plugin manifest registry. Pure-typed-data lives under
``media_stack.domain.download_client_adapters``; concrete port impls
live under ``media_stack.adapters.download_client_adapters``.
"""

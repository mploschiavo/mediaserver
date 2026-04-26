"""Edge-specific infrastructure.

ADR-0002 Phase 16-E (cross-cutting edge) — tech-specific I/O for the
edge gateway: the Envoy runtime-config generator that reads the
compose file + bootstrap profile and writes ``CONFIG_ROOT/envoy/
envoy.yaml``. No business policy lives here.
"""

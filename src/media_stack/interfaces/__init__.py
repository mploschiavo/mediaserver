"""Interfaces layer — Ports (Protocols / ABCs) declared here.

The interfaces layer is what makes the rest of the hexagon work.
A *port* is a contract: a Protocol or Abstract Base Class that
the domain or application layer depends on, and that an adapter
or infrastructure module implements. Ports invert the dependency
between business logic and external systems.

Status (Phase 16-A): scaffolding + a small set of *base* ports.
The base ports here are deliberately narrow — they capture the
shape that almost every adapter / job / store will share. Per-tech
ports (e.g. a refined ``MediaServer`` port for Jellyfin/Plex/Emby
or a refined ``Arr`` port for Sonarr/Radarr/Lidarr/Readarr) will
be added in Phase 16-B and 16-D as each migration lands and the
shape stabilises.

Files in this package:

* ``adapter.py`` — the base ``Adapter`` Protocol. Every adapter
  declares a ``name`` and exposes ``health()``, ``startup()``,
  ``shutdown()`` lifecycle hooks.
* ``job.py`` — the base ``Job`` Protocol. A job ``run()``s with a
  ``JobContext`` and returns a ``JobResult``.
* ``media_server.py`` — the ``MediaServer`` port (Jellyfin / Plex /
  Emby in Phase 16-D).
* ``arr.py`` — the ``ArrApp`` port (Sonarr / Radarr / Lidarr /
  Readarr / Bazarr).
* ``notification.py`` — the ``NotificationSink`` port.
* ``store.py`` — the ``Store`` port for persistence.

Layering rules (enforced by ``tests/unit/test_architecture_layering.py``):

* ``interfaces/`` MUST NOT import from ``domain/``,
  ``application/``, ``adapters/``, or ``infrastructure/``.
* ``interfaces/`` MAY import from the standard library and from
  small pure-data dependencies needed to *declare* the contract
  (typing, ``dataclasses``, ``typing.Protocol``).

Why is ``interfaces/`` so strictly isolated?

A Protocol that imports from ``domain/`` ties the contract to a
specific concept. When the domain evolves the port has to follow
even though the external surface didn't change. Keeping ports
free of domain types means an adapter can be exercised against a
fake input without dragging in an entire bounded context — and a
third-party adapter (e.g. a hypothetical ``media-stack-emby-plus``
plugin) can implement the port without depending on the
in-tree domain at all.

Where domain-typed payloads are needed, declare a small
serialization-friendly shape *here*, and let the domain expose
adapters between its rich types and the port shape.
"""

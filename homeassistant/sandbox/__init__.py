"""Run untrusted integrations in a separate, unprivileged process.

This package is imported by the loader, so the package itself must stay free of
imports from the rest of Home Assistant. The parts live in submodules:

- ``const``: configuration keys and limits.
- ``policy``: decides where an integration runs and stores the user's consent.
- ``protocol``: the framed JSON channel between core and a sandbox.
- ``confine``: drops the privileges of the sandbox process (sandbox side).
- ``runtime``: the minimal Home Assistant that runs inside the sandbox.
- ``manager``: starts sandboxes and validates everything they send (core side).
- ``proxy``: the stand-in component, platforms and entities in core.
"""

"""The `devstuff configure docker` wizard — the Docker daemon's `daemon.json`.

Unlike the other configurators this one writes a root-owned file outside the user's
home, so every write goes through an explicit `sudo` step the user sees first, and
the daemon reload is a separate, separately-confirmed action.
"""

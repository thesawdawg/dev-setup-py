"""The `devstuff configure lazygit` wizard — `config.yml`.

lazygit rejects a value of the wrong *type* and silently ignores an unknown *key*,
so a config full of settings from an out-of-date guide starts cleanly and does
nothing. That asymmetry is what shaped this package — see `model.py` for how the key
set was verified, and `validate.py` for what can and cannot be checked at run time.
"""

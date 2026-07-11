from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devstuff")
except PackageNotFoundError:
    __version__ = "dev"

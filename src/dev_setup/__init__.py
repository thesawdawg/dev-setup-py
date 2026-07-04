from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dev-setup")
except PackageNotFoundError:
    __version__ = "dev"

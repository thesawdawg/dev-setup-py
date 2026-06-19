from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("dev-setup")
except PackageNotFoundError:
    __version__ = "dev"

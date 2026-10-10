from __future__ import annotations

from dataclasses import dataclass

from dev_setup import links_catalog as catalog


@dataclass
class LinkDef:
    key: str = ""
    name: str = ""
    description: str = ""
    url: str = ""
    category: str = "custom"
    builtin: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.key

    @classmethod
    def from_dict(cls, data: dict, key: str) -> LinkDef:
        return cls(
            key=key,
            name=data.get("name", ""),
            description=data.get("description", ""),
            url=data.get("url", ""),
            category=data.get("category", "custom"),
        )


_registry: dict[str, LinkDef] = {}
_initialized = False


def init() -> None:
    global _initialized
    if _initialized:
        return
    _initialized = True
    effective, bundled, user = catalog.load_effective_catalog()
    for key, data in effective.items():
        link = LinkDef.from_dict(data, key=key)
        link.builtin = key in bundled and key not in user
        _registry[key] = link


def reload() -> None:
    global _initialized
    _registry.clear()
    _initialized = False
    init()


def get(key: str) -> LinkDef | None:
    init()
    return _registry.get(key)


def all_links() -> list[LinkDef]:
    init()
    return list(_registry.values())


def search(query: str, *, links: list[LinkDef] | None = None) -> list[LinkDef]:
    """Links whose title or description contains every whitespace-separated term.

    Case-insensitive. Terms are ANDed, so adding a word narrows the result — the
    behaviour people expect from a search box. Title matches rank ahead of
    description-only matches; within a rank the registry's order is kept.
    The link's key is deliberately *not* searched: it is an address, not prose, and
    matching it would surface links whose visible text never mentions the query.
    """
    terms = query.lower().split()
    if not terms:
        return []

    ranked: list[tuple[int, int, LinkDef]] = []
    for i, link in enumerate(all_links() if links is None else links):
        name = link.name.lower()
        text = f"{name} {link.description.lower()}"
        if all(t in text for t in terms):
            in_title = all(t in name for t in terms)
            ranked.append((0 if in_title else 1, i, link))
    ranked.sort(key=lambda r: r[:2])
    return [link for _, _, link in ranked]

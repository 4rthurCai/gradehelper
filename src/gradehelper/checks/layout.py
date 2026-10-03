"""Pure checks on a homework directory layout (file names only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def is_readme(name: str) -> bool:
    lowered = name.lower()
    return lowered == "readme" or lowered.startswith("readme.")


@dataclass(frozen=True)
class LayoutReport:
    hw_dir_missing: bool = False
    missing_files: tuple[str, ...] = ()
    readme_missing: bool = False
    extra_root: tuple[str, ...] = ()
    extra_hw: tuple[str, ...] = ()

    @property
    def incomplete(self) -> bool:
        return self.hw_dir_missing or bool(self.missing_files) or self.readme_missing

    @property
    def untidy(self) -> bool:
        return bool(self.extra_root or self.extra_hw)


def root_allowlist(base: list[str], max_hw_dirs: int) -> set[str]:
    return {*base, *(f"h{n}" for n in range(max_hw_dirs))}


def inspect_layout(
    root_entries: list[str],
    hw_entries: list[str] | None,
    mandatory: list[str],
    optional: list[str],
    allowed_root: set[str],
) -> LayoutReport:
    """hw_entries is None when the hN/ directory does not exist."""
    extra_root = tuple(e for e in root_entries if e not in allowed_root and not is_readme(e))
    if hw_entries is None:
        return LayoutReport(hw_dir_missing=True, extra_root=extra_root)
    present = set(hw_entries)
    known = {*mandatory, *optional}
    return LayoutReport(
        missing_files=tuple(f for f in dict.fromkeys(mandatory) if f not in present),
        readme_missing=not any(is_readme(e) for e in hw_entries),
        extra_root=extra_root,
        extra_hw=tuple(e for e in hw_entries if e not in known and not is_readme(e)),
    )


def list_dir(path: Path) -> list[str] | None:
    return sorted(p.name for p in path.iterdir()) if path.is_dir() else None

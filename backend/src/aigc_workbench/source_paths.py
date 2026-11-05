"""Resolve archived runtime locations without changing immutable Snapshot/Prompt provenance."""
from pathlib import Path

from .c01_context import C01Error


def archived_path(settings, value) -> Path:
    original = Path(value)
    root = settings.runtime_root
    old_root = settings.archived_runtime_root
    target = original
    if old_root and root:
        try:
            target = root / original.relative_to(old_root)
        except ValueError:
            pass
    if settings.environment == 'pilot' or root:
        if root is None or not target.is_absolute():
            raise C01Error('ARCHIVE_ROOT_REQUIRED', 'SOURCE_SELECTION', '需要明确的服务器运行资料目录。')
        try:
            relative = target.relative_to(root)
            if '..' in relative.parts or target.resolve().is_relative_to(root.resolve()) is False:
                raise ValueError()
            # Refuse directory/file symlinks even if they happen to resolve inside the root.
            if root.is_symlink() or any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts)+1)):
                raise ValueError()
        except ValueError:
            raise C01Error('ARCHIVE_PATH_DENIED', 'SOURCE_SELECTION', '归档来源不在指定运行资料目录内。') from None
    return target

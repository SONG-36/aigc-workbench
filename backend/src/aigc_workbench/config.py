"""Non-secret process configuration; environment files are not auto-loaded."""

from dataclasses import dataclass
import os
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8021", "http://localhost:8021",
    )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "workbench.sqlite3"

    @classmethod
    def from_environment(cls) -> "Settings":
        path = Path(os.environ.get("AIGC_DATA_DIR", ".runtime/f01")).expanduser()
        if not path.is_absolute():
            path = REPOSITORY_ROOT / path
        origins = tuple(filter(None, (
            item.strip() for item in os.environ.get(
                "AIGC_ALLOWED_ORIGINS", "http://127.0.0.1:8021,http://localhost:8021"
            ).split(",")
        )))
        return cls(path.resolve(), origins)

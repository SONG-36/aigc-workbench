"""Process settings and explicit backend-only credential loading for the C01 developer CLI."""

from dataclasses import dataclass
import os
from pathlib import Path
import stat

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ENV_FILE = REPOSITORY_ROOT / "backend" / ".env.local"


class CredentialConfigError(Exception):
    """Messages are fixed and never include file content or credential values."""


def _valid_key(value: str) -> bool:
    return bool(value) and value.isascii() and all(33 <= ord(char) <= 126 for char in value)


def load_openai_credentials() -> str:
    """Existing nonblank process key wins; otherwise load only OPENAI_API_KEY from a private file.

    No shell evaluation, interpolation, arbitrary env loading, logging, or frontend injection.
    Called by the gated developer CLI or explicitly enabled development Workspace execution.
    Settings/imports and the default (generation-disabled) app never load credentials.
    """
    existing = os.environ.get("OPENAI_API_KEY", "").strip()
    if existing:
        if not _valid_key(existing):
            raise CredentialConfigError("OPENAI_API_KEY 进程配置格式无效；未用文件覆盖。")
        return "KEY_CONFIGURED"
    try:
        descriptor = os.open(BACKEND_ENV_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return "KEY_MISSING"
    except OSError:
        raise CredentialConfigError("无法安全打开 backend/.env.local；未加载凭证。") from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384):
                raise CredentialConfigError("backend/.env.local 必须为当前用户拥有的 0600 常规小文件。")
            content = stream.read(16385)
        if len(content) > 16384:
            raise CredentialConfigError("backend/.env.local 超过配置大小限制。")
        lines = content.decode("utf-8").splitlines()
    except (OSError, UnicodeError):
        raise CredentialConfigError("无法读取 backend/.env.local 配置；未加载凭证。") from None
    values = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if separator != "=" or name.strip() != "OPENAI_API_KEY":
            raise CredentialConfigError("backend/.env.local 仅支持 OPENAI_API_KEY= 单行配置及注释。")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise CredentialConfigError("backend/.env.local 配置引号不匹配。")
            value = value[1:-1]
        values.append(value)
    if len(values) > 1:
        raise CredentialConfigError("backend/.env.local 存在重复 OPENAI_API_KEY；未加载凭证。")
    if not values or not values[0]:
        return "KEY_MISSING"
    value = values[0]
    if not _valid_key(value) or any(char in value for char in ('$', '`', '\\', '"', "'")):
        raise CredentialConfigError("backend/.env.local 凭证格式无效；不支持变量展开或命令。")
    os.environ["OPENAI_API_KEY"] = value
    return "KEY_CONFIGURED"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8021", "http://localhost:8021",
    )
    environment: str = "development"
    enable_c01_testing: bool = False
    enable_c01_real: bool = False
    enable_workspace_generation: bool = False

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
        return cls(path.resolve(), origins, os.environ.get("AIGC_ENV", "development"),
                   os.environ.get("AIGC_ENABLE_C01_TESTING", "0") == "1",
                   os.environ.get("AIGC_ENABLE_C01_REAL", "0") == "1",
                   os.environ.get("AIGC_ENABLE_WORKSPACE_GENERATION", "0") == "1")

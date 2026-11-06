"""Process settings and explicit backend-only private provider credentials."""

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
    return load_provider_credentials("OPENAI_API_KEY")


def load_provider_credentials(name: str = "DASHSCOPE_API_KEY") -> str:
    """Existing nonblank process key wins; otherwise load the selected key from a private file.

    No shell evaluation, interpolation, arbitrary env loading, logging, or frontend injection.
    Called by the gated developer CLI or explicitly enabled development Workspace execution.
    Settings/imports and the default (generation-disabled) app never load credentials.
    """
    if name not in {"OPENAI_API_KEY", "DASHSCOPE_API_KEY"}:
        raise CredentialConfigError("不支持的凭证名称。")
    existing = os.environ.get(name, "").strip()
    if existing:
        if not _valid_key(existing):
            raise CredentialConfigError("供应商进程凭证格式无效；未用文件覆盖。")
        return "KEY_CONFIGURED"
    path = Path(os.environ.get("AIGC_PRIVATE_KEY_FILE", str(BACKEND_ENV_FILE))).expanduser()
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
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
    values = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        variable, separator, value = line.partition("=")
        variable = variable.strip()
        if separator != "=" or variable not in {"OPENAI_API_KEY", "DASHSCOPE_API_KEY"}:
            raise CredentialConfigError("backend/.env.local 仅支持两个供应商的 API_KEY 单行配置及注释。")
        value = value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise CredentialConfigError("backend/.env.local 配置引号不匹配。")
            value = value[1:-1]
        if variable in values:
            raise CredentialConfigError("backend/.env.local 存在重复凭证；未加载凭证。")
        values[variable] = value
    if not values.get(name):
        return "KEY_MISSING"
    value = values[name]
    if not _valid_key(value) or any(char in value for char in ('$', '`', '\\', '"', "'")):
        raise CredentialConfigError("backend/.env.local 凭证格式无效；不支持变量展开或命令。")
    os.environ[name] = value
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
    pilot_hosts: tuple[str, ...] = ()
    pilot_networks: tuple[str, ...] = ()
    frontend_dist: Path | None = None
    runtime_root: Path | None = None
    archived_runtime_root: Path | None = None
    model_service_disabled_reason: str | None = None

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
                   os.environ.get("AIGC_ENABLE_WORKSPACE_GENERATION", "0") == "1",
                   tuple(filter(None, os.environ.get("AIGC_PILOT_HOSTS", "").split(","))),
                   tuple(filter(None, os.environ.get("AIGC_PILOT_NETWORKS", "").split(","))),
                   Path(os.environ["AIGC_FRONTEND_DIST"]).expanduser().resolve() if os.environ.get("AIGC_FRONTEND_DIST") else None,
                   Path(os.environ["AIGC_RUNTIME_ROOT"]).expanduser().resolve() if os.environ.get("AIGC_RUNTIME_ROOT") else None,
                   Path(os.environ["AIGC_ARCHIVED_RUNTIME_ROOT"]).expanduser() if os.environ.get("AIGC_ARCHIVED_RUNTIME_ROOT") else None)

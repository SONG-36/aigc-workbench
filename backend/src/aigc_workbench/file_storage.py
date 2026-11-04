"""Bounded image validation and immutable local files, not a media platform."""

from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
from uuid import UUID
import warnings

from PIL import Image, UnidentifiedImageError

from .application import StorageUnavailable
from .domain import Asset, ImageMetadata, InvalidTask

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def inspect_image(content: bytes, declared_type: str) -> ImageMetadata:
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise InvalidTask("图片必须非空，且单张不超过 10 MiB。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content), formats=list(MEDIA_TYPES)) as image:
                if image.width * image.height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1:
                    raise InvalidTask("仅支持不超过 2500 万像素的静态图片。")
                media_type, width, height = MEDIA_TYPES[image.format], image.width, image.height
                image.verify()
            with Image.open(BytesIO(content), formats=list(MEDIA_TYPES)) as image:
                image.load()  # verify alone does not decode all JPEG/WebP pixel data.
    except InvalidTask:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise InvalidTask("图片损坏，或不是有效的 JPEG / PNG / WebP。") from exc
    if declared_type.split(";", 1)[0].strip().lower() not in {media_type, "application/octet-stream"}:
        raise InvalidTask("声明的文件类型与实际图片类型不一致。")
    return ImageMetadata(sha256(content).hexdigest(), len(content), media_type, width, height)


class LocalImageFiles:
    def __init__(self, data_dir: Path):
        self.staging = data_dir / "staging"
        self.assets = data_dir / "assets"

    def initialize(self) -> None:
        self.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.assets.mkdir(mode=0o700, parents=True, exist_ok=True)

    def stage(self, asset_id: UUID, content: bytes, media_type: str) -> ImageMetadata:
        metadata = inspect_image(content, media_type)
        try:
            with (self.staging / (asset_id.hex + ".part")).open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise StorageUnavailable("素材暂存失败，未确认保存成功。") from exc
        return metadata

    def publish(self, asset_id: UUID) -> None:
        try:
            # Same-filesystem hard link publishes atomically and refuses overwrite.
            os.link(self.staging / (asset_id.hex + ".part"), self.assets / asset_id.hex)
            descriptor = os.open(self.assets, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self.discard_staging(asset_id)
        except OSError as exc:
            raise StorageUnavailable("素材发布失败，未确认保存成功。") from exc

    def discard_staging(self, asset_id: UUID) -> None:
        try:
            (self.staging / (asset_id.hex + ".part")).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageUnavailable("暂存文件清理失败，请核对存储状态。") from exc

    def read(self, asset: Asset) -> bytes:
        try:
            # Only a validated UUID reaches this path; original names are never paths.
            descriptor = os.open(self.assets / asset.id.hex, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                content = stream.read(MAX_UPLOAD_BYTES + 1)
        except OSError as exc:
            raise StorageUnavailable("已记录素材的文件不可读，请核对存储。") from exc
        if len(content) != asset.image.size_bytes or sha256(content).hexdigest() != asset.image.sha256:
            raise StorageUnavailable("素材完整性检查失败，未返回该文件。")
        return content

    def orphan_ids(self, known_ids: set[UUID]) -> list[str]:
        known = {item.hex for item in known_ids}
        return sorted([path.name for path in self.assets.iterdir() if path.name not in known]
                      + ["staging/" + path.name for path in self.staging.iterdir()])

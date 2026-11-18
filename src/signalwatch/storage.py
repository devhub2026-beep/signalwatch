"""对象存储抽象层。

正文 / 附件 / 导出的文件，既可以落在本地磁盘（默认，零依赖），
也可以切到 S3 兼容的对象存储（MinIO / OSS / COS）。业务代码只依赖
``StorageBackend`` 协议，换后端不碰调用方。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageBackend(ABC):
    """存储后端协议：按 key 读写字节，并提供可访问 URL。"""

    @abstractmethod
    def put(self, key: str, data: bytes) -> None:
        """写入一个对象，覆盖同 key 旧值。"""

    @abstractmethod
    def get(self, key: str) -> bytes | None:
        """读取对象；不存在返回 None。"""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除对象；不存在返回 False。"""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """判断对象是否存在。"""

    @abstractmethod
    def url(self, key: str) -> str:
        """返回对象可访问的 URL（本地用 blob 路由，S3 用签名 URL）。"""


class LocalStorage(StorageBackend):
    """本地文件后端（默认）。对象按 key 落到 base_dir 下。"""

    def __init__(self, base_dir: Path | str) -> None:
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # 防路径遍历：key 一律当作相对 base_dir 的路径，越界即拒绝
        key = key.replace("\\", "/").lstrip("/")
        p = (self.base / key).resolve()
        if self.base not in p.parents and p != self.base:
            raise ValueError(f"非法 key：{key}")
        return p

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.is_file() else None

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.is_file():
            p.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def url(self, key: str) -> str:
        return f"/api/blob/{key.lstrip('/')}"


class S3Storage(StorageBackend):
    """S3 兼容后端（MinIO / OSS / COS）。

    接真实服务时用 boto3 实现 put/get/delete/exists/url（预签名 GET）。
    当前留接口契约，不引入 boto3 依赖。
    """

    def __init__(self, endpoint: str, bucket: str, access_key: str,
                 secret_key: str) -> None:
        self.endpoint = endpoint
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key

    def put(self, key: str, data: bytes) -> None:
        raise NotImplementedError("接 S3 兼容 OSS 时用 boto3 实现")

    def get(self, key: str) -> bytes | None:
        raise NotImplementedError("接 S3 兼容 OSS 时用 boto3 实现")

    def delete(self, key: str) -> bool:
        raise NotImplementedError("接 S3 兼容 OSS 时用 boto3 实现")

    def exists(self, key: str) -> bool:
        raise NotImplementedError("接 S3 兼容 OSS 时用 boto3 实现")

    def url(self, key: str) -> str:
        raise NotImplementedError("接 S3 兼容 OSS 时返回预签名 URL")

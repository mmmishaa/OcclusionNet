"""Доступ к бакету: чтение кусками и запись с защитой от порчи сырья.

Сырьё в `raw/` неприкосновенно. Заезд стоил времени и трафика, а восстановить
испорченный архив можно только повторным скачиванием — у SID это вообще Globus
с ручными шагами. Поэтому запись разрешена только под явно указанный префикс,
и попытка выйти за него — исключение, а не предупреждение.
"""

from __future__ import annotations

import io


class WriteOutsideAllowedPrefix(Exception):
    """Попытка записи вне разрешённого префикса."""


class S3File(io.RawIOBase):
    """Объект в бакете как обычный seekable-файл поверх range-запросов.

    Нужен, чтобы zipfile и tarfile читали архив на месте, не скачивая целиком.
    """

    def __init__(self, client, bucket: str, key: str):
        self.c, self.b, self.k = client, bucket, key
        self.size = client.head_object(Bucket=bucket, Key=key)["ContentLength"]
        self.pos = 0

    def readable(self) -> bool: return True
    def seekable(self) -> bool: return True
    def tell(self) -> int:      return self.pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if   whence == io.SEEK_SET: self.pos = offset
        elif whence == io.SEEK_CUR: self.pos += offset
        else:                       self.pos = self.size + offset
        self.pos = max(0, min(self.pos, self.size))
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        r = self.c.get_object(Bucket=self.b, Key=self.k,
                              Range=f"bytes={self.pos}-{self.pos + n - 1}")
        data = r["Body"].read()
        self.pos += len(data)
        return data

    def readinto(self, buf) -> int:
        data = self.read(len(buf))
        buf[:len(data)] = data
        return len(data)


class S3Store:
    """Бакет с правом чтения везде и правом записи только под write_prefix."""

    def __init__(self, client, bucket: str, write_prefix: str | None = None):
        self.client = client
        self.bucket = bucket
        self.write_prefix = write_prefix

    # --- чтение -----------------------------------------------------------

    def open(self, key: str, buffered: bool = True):
        raw = S3File(self.client, self.bucket, key)
        # буферизация ускоряет zip и мешает tar: у tar seek должен быть
        # бесплатным, иначе пропуск данных превращается в их скачивание
        return io.BufferedReader(raw, buffer_size=1 << 20) if buffered else raw

    def read(self, key: str, offset: int = 0, size: int | None = None) -> bytes:
        if offset == 0 and size is None:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        end = "" if size is None else offset + size - 1
        return self.client.get_object(Bucket=self.bucket, Key=key,
                                      Range=f"bytes={offset}-{end}")["Body"].read()

    def list(self, prefix: str = "") -> list[dict]:
        out = []
        for page in self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix):
            out.extend(page.get("Contents", []))
        return out

    # --- запись -----------------------------------------------------------

    def _check(self, key: str) -> None:
        if self.write_prefix is None:
            raise WriteOutsideAllowedPrefix(
                f"хранилище открыто только на чтение, запись в {key!r} запрещена")
        if not key.startswith(self.write_prefix):
            raise WriteOutsideAllowedPrefix(
                f"запись разрешена только под {self.write_prefix!r}, "
                f"а попытка была в {key!r}")

    def put(self, key: str, data: bytes) -> None:
        self._check(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def put_file(self, key: str, path) -> None:
        self._check(key)
        with open(path, "rb") as fh:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=fh.read())


class LocalStore:
    """Каталог на диске с той же раскладкой, что бакет. Для проверок без облака."""

    def __init__(self, root, write_prefix: str | None = None):
        from pathlib import Path
        self.root = Path(root)
        self.write_prefix = write_prefix

    def open(self, key: str, buffered: bool = True):
        return open(self.root / key, "rb")

    def read(self, key: str, offset: int = 0, size: int | None = None) -> bytes:
        with open(self.root / key, "rb") as f:
            f.seek(offset)
            return f.read(size if size is not None else -1)

    def list(self, prefix: str = "") -> list[dict]:
        base = self.root / prefix
        if not base.exists():
            return []
        return [{"Key": str(p.relative_to(self.root)), "Size": p.stat().st_size}
                for p in base.rglob("*") if p.is_file()]

    def put(self, key: str, data: bytes) -> None:
        if self.write_prefix is None or not key.startswith(self.write_prefix):
            raise WriteOutsideAllowedPrefix(
                f"запись разрешена только под {self.write_prefix!r}, а не в {key!r}")
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def client(endpoint: str = "https://storage.yandexcloud.net",
           region: str = "ru-central1", key: str | None = None,
           secret: str | None = None):
    """boto3-клиент Object Storage. Ключи по умолчанию из S3_KEY и S3_SECRET."""
    import os
    import boto3
    return boto3.client(
        "s3", endpoint_url=endpoint, region_name=region,
        aws_access_key_id=key or os.environ["S3_KEY"],
        aws_secret_access_key=secret or os.environ["S3_SECRET"])

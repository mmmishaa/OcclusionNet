"""Стадия build: сырьё + рецепт -> кадры единого формата в шардах.

Приводит кадры разных источников к одной геометрии, одному кодеку и, по
желанию, к одной резкости. Ничего специфичного для конкретных датасетов в коде
нет: способ добраться до сырья описывается в sources.yaml, параметры
преобразования — в рецепте. Новый источник добавляется правкой yaml.

    python -m data.build --recipe data/recipe.example.yaml --dry-run
    python -m data.build --recipe data/recipe.example.yaml

Про приведение к «наихудшему». Источники различимы не только содержанием, но и
техническими признаками: разрешением, детальностью, весом файла. Модель с
удовольствием выучит их вместо помех. Поэтому:

  * геометрия — общий квадрат, разрешение как признак исчезает;
  * кодек и качество — одни для всех, вес файла как признак исчезает;
  * резкость — опционально приводится к самому мягкому источнику.

Последнее необратимо огрубляет данные и включается осознанно: если в датасете
планируется класс «смаз», выравнивание резкости уничтожит именно тот сигнал,
который нужно распознавать.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageFilter

HERE = Path(__file__).parent
METRIC_HEIGHT = 256          # та же нормировка, что в land.py


# --------------------------------------------------------------------------
# параметры кадра
# --------------------------------------------------------------------------

@dataclass
class FrameSpec:
    size: int = 512
    fit: str = "crop_center"      # crop_center | stretch
    fmt: str = "jpeg"
    quality: int = 90

    @classmethod
    def from_recipe(cls, recipe: dict) -> "FrameSpec":
        t = recipe.get("target", {})
        return cls(size=int(t.get("size", 512)),
                   fit=t.get("fit", "crop_center"),
                   fmt=t.get("format", "jpeg"),
                   quality=int(t.get("quality", 90)))


def to_square(img: Image.Image, spec: FrameSpec) -> Image.Image:
    """Приводит кадр к квадрату size x size.

    crop_center режет по короткой стороне: геометрия не искажается, но теряется
    поле зрения — у 16:9 уходит 44% ширины.

    Дополнение полями намеренно не поддержано: толщина полос кодирует исходное
    соотношение сторон, то есть возвращает ровно тот признак источника, ради
    устранения которого всё и делается.
    """
    if spec.fit == "stretch":
        return img.resize((spec.size, spec.size), Image.Resampling.LANCZOS)

    if spec.fit != "crop_center":
        raise ValueError(f"неизвестный fit: {spec.fit}")

    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    return (img.crop((left, top, left + side, top + side))
               .resize((spec.size, spec.size), Image.Resampling.LANCZOS))


def sharpness(img: Image.Image) -> float:
    """Дисперсия лапласиана на копии высотой METRIC_HEIGHT."""
    g = img.convert("L")
    scale = METRIC_HEIGHT / max(g.height, 1)
    g = g.resize((max(int(g.width * scale), 1), METRIC_HEIGHT),
                 Image.Resampling.LANCZOS)
    a = np.asarray(g, dtype=np.float32)
    lap = (a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
           - 4.0 * a[1:-1, 1:-1])
    return float(lap.var())


def encode(img: Image.Image, spec: FrameSpec) -> bytes:
    buf = io.BytesIO()
    if spec.fmt == "jpeg":
        img.convert("RGB").save(buf, "JPEG", quality=spec.quality, optimize=True)
    elif spec.fmt == "png":
        img.convert("RGB").save(buf, "PNG", optimize=True)
    else:
        raise ValueError(f"неизвестный формат: {spec.fmt}")
    return buf.getvalue()


def process(raw: bytes, spec: FrameSpec, sigma: float = 0.0) -> tuple[bytes, float]:
    """Байты исходного кадра -> байты нормализованного и его резкость."""
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img = to_square(img, spec)
    if sigma > 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=sigma))
    return encode(img, spec), sharpness(img)


# --------------------------------------------------------------------------
# выравнивание резкости
# --------------------------------------------------------------------------

def calibrate_sigma(samples: list[bytes], spec: FrameSpec, target: float,
                    lo: float = 0.0, hi: float = 3.0, steps: int = 12) -> float:
    """Подбирает размытие, приводящее медианную резкость выборки к target.

    Подбор один на источник, а не на кадр: так дешевле и, главное,
    воспроизводимо — иначе одинаковые кадры обрабатывались бы по-разному.
    """
    def median_at(sigma: float) -> float:
        return float(np.median([process(b, spec, sigma)[1] for b in samples]))

    if median_at(0.0) <= target:
        return 0.0
    for _ in range(steps):
        mid = (lo + hi) / 2
        if median_at(mid) > target:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


# --------------------------------------------------------------------------
# доступ к сырью: декларативно, без кода на источник
# --------------------------------------------------------------------------

class RawReader:
    """Читает исходные байты кадра по описанию raw_storage из sources.yaml.

        raw_storage:
          kind: zip | tar | plain
          key: raw/foo/archive.zip                 # для zip и tar
          key_by_prefix: {lens_snow: raw/a.tar}    # если архивов несколько
          prefix: raw/foo/                         # для plain
    """

    def __init__(self, store, spec: dict):
        self.store = store              # объект с .open(key) -> seekable файл
        self.spec = spec or {}
        self.kind = self.spec.get("kind", "plain")
        self._tar_index: dict[str, dict] = {}

    def _key_for(self, raw_path: str) -> str:
        by_prefix = self.spec.get("key_by_prefix")
        if by_prefix:
            head = raw_path.split("/")[0]
            if head not in by_prefix:
                raise KeyError(f"нет архива для префикса {head!r}")
            return by_prefix[head]
        if "key" in self.spec:
            return self.spec["key"]
        return self.spec.get("prefix", "") + raw_path

    def read(self, raw_path: str) -> bytes:
        key = self._key_for(raw_path)

        if self.kind == "plain":
            return self.store.read(key)

        if self.kind == "zip":
            fh = self.store.open(key, buffered=True)
            try:
                with zipfile.ZipFile(fh) as z:
                    return z.read(raw_path)
            finally:
                fh.close()

        if self.kind == "tar":
            # у tar нет оглавления: строим индекс один раз, дальше точное чтение
            if key not in self._tar_index:
                fh = self.store.open(key, buffered=False)
                idx = {}
                with tarfile.open(fileobj=fh, mode="r:") as t:
                    for m in t:
                        if m.isfile():
                            idx[m.name] = (m.offset_data, m.size)
                self._tar_index[key] = idx
            offset, size = self._tar_index[key][raw_path]
            return self.store.read(key, offset, size)

        raise ValueError(f"неизвестный kind: {self.kind}")


class LocalStore:
    """Сырьё в каталоге на диске — для проверки пайплайна без облака."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def open(self, key: str, buffered: bool = True):
        return open(self.root / key, "rb")

    def read(self, key: str, offset: int = 0, size: int | None = None) -> bytes:
        with open(self.root / key, "rb") as f:
            f.seek(offset)
            return f.read(size if size is not None else -1)


# --------------------------------------------------------------------------
# сборка
# --------------------------------------------------------------------------

def recipe_hash(recipe: dict) -> str:
    blob = json.dumps(recipe, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha1(blob).hexdigest()[:10]


@dataclass
class ShardWriter:
    """Пишет пары кадр + метаданные в tar-шарды заданного размера."""
    out_dir: Path
    prefix: str
    shard_bytes: int = 512 * 1024**2
    idx: int = 0
    _tar: tarfile.TarFile | None = None
    _size: int = 0
    written: list[str] = field(default_factory=list)

    def _rotate(self):
        if self._tar is not None:
            self._tar.close()
            self.written.append(self._path.name)
            self.idx += 1
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.out_dir / f"{self.prefix}-{self.idx:05d}.tar"
        self._tar = tarfile.open(self._path, "w")
        self._size = 0

    def add(self, key: str, image: bytes, meta: dict):
        if self._tar is None or self._size >= self.shard_bytes:
            self._rotate()
        for name, blob in ((f"{key}.jpg", image),
                           (f"{key}.json", json.dumps(meta, ensure_ascii=False).encode())):
            ti = tarfile.TarInfo(name)
            ti.size = len(blob)
            ti.mtime = 0
            self._tar.addfile(ti, io.BytesIO(blob))
            self._size += len(blob)

    def close(self):
        if self._tar is not None:
            self._tar.close()
            self.written.append(self._path.name)
            self._tar = None


def load_recipe(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recipe", type=Path, default=HERE / "recipe.example.yaml")
    ap.add_argument("--manifests", type=Path, default=HERE / "manifest",
                    help="каталог с parquet-манифестами источников")
    ap.add_argument("--raw-root", type=Path,
                    help="локальный корень, повторяющий раскладку бакета: внутри "
                         "должен лежать каталог raw/, потому что ключи в "
                         "sources.yaml — это ключи объектов S3")
    ap.add_argument("--out", type=Path, default=HERE / "curated")
    ap.add_argument("--limit", type=int, help="взять первые N кадров, для проверки")
    ap.add_argument("--dry-run", action="store_true",
                    help="только калибровка и план, без записи шардов")
    a = ap.parse_args()

    import pandas as pd
    import pyarrow.parquet as pq

    recipe = load_recipe(a.recipe)
    spec = FrameSpec.from_recipe(recipe)
    sources = yaml.safe_load((HERE / "sources.yaml").read_text())

    files = sorted(Path(a.manifests).glob("*.parquet"))
    if not files:
        sys.exit(f"нет манифестов в {a.manifests}")
    df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
    df = df[df.error.isna()].copy()
    if a.limit:
        df = df.groupby("source", group_keys=False).head(a.limit)

    print(f"рецепт {a.recipe.name}, хеш {recipe_hash(recipe)}")
    print(f"кадров на входе: {len(df):,}, источников: {df.source.nunique()}")
    print(f"целевой кадр: {spec.size}x{spec.size} {spec.fmt} q{spec.quality}, {spec.fit}\n")

    if a.raw_root is None:
        sys.exit("укажите --raw-root: работа напрямую с S3 запускается из блокнота")
    store = LocalStore(a.raw_root)
    readers = {s: RawReader(store, (sources.get(s) or {}).get("raw_storage"))
               for s in df.source.unique()}

    # --- калибровка резкости ------------------------------------------------
    equalize = bool((recipe.get("target") or {}).get("equalize_sharpness", False))
    sigmas = {s: 0.0 for s in readers}
    if equalize:
        n = int((recipe.get("target") or {}).get("calibration_frames", 24))
        medians = {}
        cache = {}
        for src in readers:
            rows = df[df.source == src].sample(min(n, (df.source == src).sum()),
                                               random_state=0)
            cache[src] = [readers[src].read(r.raw_path) for r in rows.itertuples()]
            medians[src] = float(np.median([process(b, spec)[1] for b in cache[src]]))
        target = min(medians.values())
        print("резкость после нормализации геометрии (медиана по выборке):")
        for src, m in medians.items():
            print(f"  {src:<24} {m:8.1f}")
        print(f"приводим к самому мягкому: {target:.1f}\n")
        for src in readers:
            sigmas[src] = calibrate_sigma(cache[src], spec, target)
            print(f"  {src:<24} размытие sigma = {sigmas[src]}")
        print()

    if a.dry_run:
        print("сухой прогон, шарды не пишутся")
        return

    # --- обработка ----------------------------------------------------------
    out = Path(a.out) / f"{recipe.get('name','dataset')}-{recipe_hash(recipe)}"
    writer = ShardWriter(out, recipe.get("name", "dataset"),
                         shard_bytes=int((recipe.get("output") or {})
                                         .get("shard_bytes_mb", 512)) * 1024**2)
    done = failed = 0
    for r in df.itertuples():
        try:
            raw = readers[r.source].read(r.raw_path)
            img, sharp = process(raw, spec, sigmas[r.source])
        except Exception as exc:
            failed += 1
            if failed <= 5:
                print(f"  пропущен {r.source}/{r.raw_path}: {type(exc).__name__}: {exc}")
            continue
        writer.add(r.frame_uid, img, {
            "frame_uid": r.frame_uid,
            "source": r.source,
            "sequence_id": r.sequence_id,
            "labels": list(r.labels),
            "sharpness_out": round(sharp, 2),
            "sigma": sigmas[r.source],
        })
        done += 1
        if done % 500 == 0:
            print(f"  обработано {done:,}")
    writer.close()

    (out / "recipe.yaml").write_text(yaml.safe_dump(recipe, allow_unicode=True,
                                                    sort_keys=False))
    print(f"\nготово: {done:,} кадров, пропущено {failed}, шардов {len(writer.written)}")
    print(f"каталог: {out}")


if __name__ == "__main__":
    main()

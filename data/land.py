"""Стадия land: каталог с кадрами источника -> манифест.

    python -m data.land <источник> --root <путь> --out manifest.parquet

Пиксели не трогаются и не перекодируются: читается только то, что нужно для
метаданных и метрик. Метрики считаются на приведённой к общей высоте копии,
иначе кадры 720p и 1080p окажутся в разных классах резкости ни за что.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from PIL import Image

HERE = Path(__file__).parent
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
METRIC_HEIGHT = 256          # общая высота для всех метрик


# --------------------------------------------------------------------------
# метрики
# --------------------------------------------------------------------------

def _laplacian_var(a: np.ndarray) -> float:
    """Дисперсия лапласиана — мера резкости. Больше значит резче."""
    lap = (a[:-2, 1:-1] + a[2:, 1:-1] + a[1:-1, :-2] + a[1:-1, 2:]
           - 4.0 * a[1:-1, 1:-1])
    return float(lap.var())


def _dhash(img: Image.Image) -> int:
    """64-битный перцептивный хеш по горизонтальным градиентам."""
    a = np.asarray(img.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _mask_nonempty(path: Path) -> bool | None:
    """Есть ли в маске хоть один ненулевой пиксель."""
    if path is None or not path.exists():
        return None
    try:
        with Image.open(path) as im:
            return bool(np.asarray(im.convert("L")).any())
    except Exception:
        return None


def measure(job: tuple[Path, Path | None]) -> dict:
    """Всё, что читается из самих файлов. Выполняется в отдельном процессе."""
    path, mask_path = job
    try:
        with Image.open(path) as im:
            width, height = im.size
            fmt = (im.format or "").lower()
            gray = im.convert("L")
            scale = METRIC_HEIGHT / max(gray.height, 1)
            small = gray.resize(
                (max(int(gray.width * scale), 1), METRIC_HEIGHT),
                Image.Resampling.LANCZOS,
            )
            a = np.asarray(small, dtype=np.float32)
            return {
                "width": width,
                "height": height,
                "format": fmt,
                "bytes": path.stat().st_size,
                "sharpness": _laplacian_var(a),
                "luma_mean": float(a.mean()),
                "luma_p01": float(np.percentile(a, 1)),
                "luma_p99": float(np.percentile(a, 99)),
                "phash": _dhash(small),
                "mask_nonempty": _mask_nonempty(mask_path),
                "error": None,
            }
    except Exception as exc:                       # битые файлы не роняют заезд
        return {"width": 0, "height": 0, "format": "", "bytes": 0,
                "sharpness": 0.0, "luma_mean": 0.0, "luma_p01": 0.0,
                "luma_p99": 0.0, "phash": 0, "mask_nonempty": None,
                "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# метки
# --------------------------------------------------------------------------

DROP = "__drop__"    # кадр не относится к нашей таксономии и в манифест не идёт
CLEAN = "__clean__"  # кадр заведомо чистый: правило гасит все прочие метки


def resolve_labels(spec: dict, path_fields: dict, attrs: dict) -> list[str] | None:
    """Правила источника из sources.yaml -> метки кадра.

    Пустой список означает clean. Чтобы выбросить кадр, правило должно давать
    __drop__: пустой список и «не наш кадр» — разные вещи, и путать их нельзя,
    иначе песчаная буря приедет в класс чистых кадров.

    __clean__ гасит все прочие метки. Нужен там, где путь одновременно несёт
    условие съёмки и признак эталона: у ACDC парные кадры лежат в каталогах
    вида fog/train_ref, то есть по одному правилу это туман, а по другому —
    заведомо чистый снимок той же сцены. Побеждает второе.
    """
    out: list[str] = list(spec.get("const", []))

    for key, mapping in (spec.get("from_path") or {}).items():
        value = path_fields.get(key)
        if value is not None and value in mapping:
            out.extend(mapping[value])

    for key, mapping in (spec.get("from_attrs") or {}).items():
        value = attrs.get(key)
        if value is None:
            continue
        # ключи в yaml всегда строки, значения атрибутов бывают bool и числами
        for candidate in (value, str(value), str(value).lower()):
            if candidate in mapping:
                out.extend(mapping[candidate])
                break

    if DROP in out:
        return None
    if CLEAN in out:
        return []
    return sorted(set(out))


# --------------------------------------------------------------------------
# загрузчики метаданных источника
#
# Проверен только bdd100k: формат его labels json описан в документации.
# Остальные написаны по описанию датасета и почти наверняка потребуют правки
# под фактические имена колонок — смотреть на реальный файл, а не угадывать.
# --------------------------------------------------------------------------

def attrs_bdd100k(root: Path) -> dict[str, dict]:
    """labels/*.json: список записей {name, attributes:{weather,scene,timeofday}}."""
    index: dict[str, dict] = {}
    for jf in sorted(root.rglob("*.json")):
        try:
            data = json.loads(jf.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, list):
            continue
        for rec in data:
            if isinstance(rec, dict) and "name" in rec:
                index[Path(rec["name"]).stem] = rec.get("attributes", {}) or {}
    return index


def attrs_csv_by_sequence(root: Path, pattern: str = "*Metadata*.csv") -> dict[str, dict]:
    """Метаданные на уровне сиквенса: первая колонка — идентификатор сиквенса."""
    import csv
    index: dict[str, dict] = {}
    for cf in sorted(root.rglob(pattern)):
        with cf.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                if not row:
                    continue
                key = str(next(iter(row.values()))).strip()
                index[key] = row
    return index


ATTR_LOADERS = {
    "bdd100k": attrs_bdd100k,
    "sid": attrs_csv_by_sequence,
}


# --------------------------------------------------------------------------
# сборка
# --------------------------------------------------------------------------

# Схема задаётся явно, а не выводится из данных: манифесты разных источников
# должны склеиваться без конфликта типов. phash обязан быть uint64 — вывод
# типов отправляет его в int64 и переполняется на старшем бите.
SCHEMA = pa.schema([
    ("frame_uid", pa.string()),
    ("source", pa.string()),
    ("source_version", pa.string()),
    ("sequence_id", pa.string()),
    ("raw_path", pa.string()),
    ("camera_type", pa.string()),
    ("labels", pa.list_(pa.string())),
    ("width", pa.int32()),
    ("height", pa.int32()),
    ("format", pa.string()),
    ("bytes", pa.int64()),
    ("sharpness", pa.float32()),
    ("luma_mean", pa.float32()),
    ("luma_p01", pa.float32()),
    ("luma_p99", pa.float32()),
    ("phash", pa.uint64()),
    # null у источников без масок — колонка общая для всех, иначе манифесты
    # не склеиваются
    ("mask_nonempty", pa.bool_()),
    ("error", pa.string()),
])

def build(source: str, root: Path, out: Path, workers: int, limit: int | None) -> None:
    sources = yaml.safe_load((HERE / "sources.yaml").read_text())
    if source not in sources:
        sys.exit(f"источник {source!r} не описан в sources.yaml")
    spec = sources[source]

    layout = spec.get("in_layout") or spec.get("layout") or {}
    rx = re.compile(layout["path_regex"]) if layout.get("path_regex") else None

    # include_regex отсекает то, что лежит рядом с кадрами, но кадром не
    # является — у Evocargo это зеркальный каталог масок, вдвое раздувший бы
    # манифест дубликатами
    inc = re.compile(layout["include_regex"]) if layout.get("include_regex") else None

    files = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    if inc is not None:
        files = [p for p in files if inc.search(p.relative_to(root).as_posix())]
    if limit:
        files = files[:limit]
    if not files:
        sys.exit(f"в {root} не найдено изображений")

    # маска ищется подменой куска пути: images/... -> masks/...
    swap = layout.get("mask_from_path")

    def mask_for(p: Path) -> Path | None:
        if not swap:
            return None
        rel = p.relative_to(root).as_posix()
        if swap[0] not in rel:
            return None
        return root / rel.replace(swap[0], swap[1], 1)

    jobs = [(p, mask_for(p)) for p in files]
    print(f"{source}: {len(files):,} кадров"
          + (", маски есть" if swap else "")
          + f", метрики в {workers} процессов")

    loader = ATTR_LOADERS.get(source)
    attr_index = loader(root) if loader else {}
    if loader:
        print(f"метаданные источника: {len(attr_index):,} записей")

    label_spec = spec.get("labels") or {}
    rows: list[dict] = []
    dropped = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        for path, m in zip(files, pool.map(measure, jobs, chunksize=32)):
            rel = path.relative_to(root).as_posix()

            fields = {}
            if rx is not None:
                hit = rx.search(rel)
                if hit:
                    fields = {k: v for k, v in hit.groupdict().items() if v is not None}

            # sequence_id можно собрать из нескольких групп регекса — это нужно,
            # когда имя каталога съёмки уникально только внутри своего раздела.
            # Без группы sequence сиквенсом считается каталог кадра, обязательно
            # относительным путём: абсолютный утащил бы в манифест путь
            # конкретной машины и сломал склейку манифестов.
            parts = layout.get("sequence_from")
            if parts:
                sequence = "/".join(str(fields.get(x, "")) for x in parts).strip("/")
            else:
                sequence = fields.get("sequence") or path.parent.relative_to(root).as_posix()
            attrs = attr_index.get(path.stem) or attr_index.get(sequence) or {}

            labels = resolve_labels(label_spec, fields, attrs)
            if labels is None:
                dropped += 1
                continue

            # метки из маски: непустая маска и есть сам признак помехи
            from_mask = label_spec.get("from_mask") or {}
            if from_mask and m["mask_nonempty"] is not None:
                key = "nonempty" if m["mask_nonempty"] else "empty"
                labels = sorted(set(labels) | set(from_mask.get(key, [])))

            rows.append({
                "frame_uid": hashlib.sha1(
                    f"{source}|{rel}".encode()).hexdigest()[:16],
                "source": source,
                "source_version": str(spec.get("snapshot", "")),
                "sequence_id": sequence,
                "raw_path": rel,
                "camera_type": (spec.get("properties") or {}).get("camera", "unknown"),
                "labels": labels,
                **m,
            })

    ok = [r for r in rows if r["error"] is None]
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")

    print(f"\nзаписано {out}  ({out.stat().st_size / 1024**2:.1f} МБ)")
    print(f"прочитано без ошибок: {len(ok):,} из {len(rows):,}")
    if dropped:
        print(f"выброшено вне таксономии: {dropped:,}")

    counts: dict[str, int] = {}
    for r in ok:
        for lb in r["labels"] or ["<пусто = clean>"]:
            counts[lb] = counts.get(lb, 0) + 1
    print("\nметки:")
    for lb, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {lb:<24} {n:>8,}")

    if ok:
        sh = np.array([r["sharpness"] for r in ok])
        print(f"\nрезкость: p05 {np.percentile(sh, 5):.1f} | "
              f"медиана {np.median(sh):.1f} | p95 {np.percentile(sh, 95):.1f}")
        print("порог blur_motion выбирать по этому распределению, "
              "отдельно для каждого источника")

    dupes = len(ok) - len({r["phash"] for r in ok})
    print(f"точных дублей по phash: {dupes:,}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="ключ из sources.yaml")
    ap.add_argument("--root", type=Path, required=True, help="каталог с кадрами")
    ap.add_argument("--out", type=Path, help="по умолчанию manifest/<источник>.parquet")
    ap.add_argument("--workers", type=int, default=0, help="0 = по числу ядер")
    ap.add_argument("--limit", type=int, help="взять первые N кадров, для проверки")
    a = ap.parse_args()

    import os
    build(
        source=a.source,
        root=a.root,
        out=a.out or HERE / "manifest" / f"{a.source}.parquet",
        workers=a.workers or (os.cpu_count() or 4),
        limit=a.limit,
    )


if __name__ == "__main__":
    main()

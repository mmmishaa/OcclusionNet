"""Выгрузка собранного датасета в папки по классам, без архивов.

    python -m data.export_folders --bucket <бакет> --version occlusionnet-536a94027a

Раскладка ImageFolder: <датасет>/<train|test>/<класс>/*.jpg

Про CADC 2018. Авторская метка Cam 00 lens snow cover = Partial стоит на весь
заезд и смешивает три разных явления: круглые прозрачные капли, белёсые брызги
мокрого снега и почти чистое стекло. Посмотрели глазами по два кадра с каждого
заезда и развели руками — таблица ниже. Значение в скобках это Snow points
removed из авторской таблицы, с видом на стекле оно не коррелирует.

Правьте CADC_2018 и запускайте заново, если не согласны: это дешевле, чем
спорить, и заведомо честнее, чем оставлять чужую метку как есть.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
from pathlib import Path

CADC_2018 = {
    # заезд               что видно на стекле
    "2018_03_07_0001": "drops",   # (557) крупные круглые капли
    "2018_03_06_0006": "snow",    # (500) белёсые брызги
    "2018_03_07_0002": "snow",    # (472) плотные разводы мокрого снега
    "2018_03_06_0005": "skip",    # (396) почти чистое
    "2018_03_06_0008": "snow",    # (265) брызги
    "2018_03_06_0009": "snow",    # (235) мелкие белые вкрапления
    "2018_03_07_0007": "snow",    # (182) белые крупинки
    "2018_03_07_0005": "skip",    # (100) почти чистое
    "2018_03_06_0010": "skip",    # (75)  чистое
    "2018_03_07_0006": "drops",   # (42)  круглые капли
}


def greedy(sizes: dict, ratios: dict, seed: str) -> dict:
    """Сиквенсы целиком, доли выравниваются по кадрам."""
    total = sum(sizes.values())
    target = {k: total * v for k, v in ratios.items()}
    got = {k: 0 for k in ratios}
    out = {}
    for name in sorted(sizes, key=lambda n: (-sizes[n],
                       hashlib.sha1(f"{seed}|{n}".encode()).hexdigest())):
        part = max(ratios, key=lambda k: target[k] - got[k])
        out[name] = part
        got[part] += sizes[name]
    return out


def classify(row) -> tuple[str | None, str | None]:
    """Кадр -> (датасет, класс). None означает «не берём»."""
    if row.source == "evocargo_raindrops":
        return "drops_on_glass", ("drops" if "raindrops" in row.labels else "clean")
    if row.source == "cadc":
        if row.sequence_id.startswith("2019"):
            # снегопад при чистом стекле: тяжёлый отрицательный пример для обоих
            # датасетов — заставляет отличать помеху на стекле от погоды в кадре
            return "both_clean", "clean"
        kind = CADC_2018.get(row.sequence_id, "skip")
        if kind == "drops":
            return "drops_on_glass", "drops"
        if kind == "snow":
            return "snow_on_glass", "snow"
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--version", required=True, help="каталог внутри curated/")
    ap.add_argument("--out", type=Path, default=Path("dataset_export"))
    ap.add_argument("--test-share", type=float, default=0.2)
    a = ap.parse_args()

    import pandas as pd
    import pyarrow.parquet as pq
    from .s3io import S3Store, client

    store = S3Store(client(), a.bucket)
    prefix = f"curated/{a.version}/"
    m = pq.read_table(io.BytesIO(store.read(prefix + "manifest.parquet"))).to_pandas()
    m[["ds", "cls"]] = [classify(r) for r in m.itertuples()]
    m = m[m.ds.notna()].copy()

    # кадры CADC 2019 идут отрицательными в оба датасета
    both = m[m.ds == "both_clean"]
    m = pd.concat([m[m.ds != "both_clean"]]
                  + [both.assign(ds=name) for name in ("drops_on_glass", "snow_on_glass")])

    ratios = {"train": 1 - a.test_share, "test": a.test_share}
    assign = {}
    for (ds, _), grp in m.groupby(["ds", "cls"]):
        sizes = grp.groupby(["source", "sequence_id"]).size()
        part = greedy({k: int(v) for k, v in sizes.items()}, ratios, f"{ds}")
        assign.update({(ds, *k): v for k, v in part.items()})
    m["part"] = [assign[(r.ds, r.source, r.sequence_id)] for r in m.itertuples()]

    print(m.groupby(["ds", "part", "cls"]).size().rename("кадров").to_string())

    want = {}
    for r in m.itertuples():
        want.setdefault(r.frame_uid, []).append((r.ds, r.part, r.cls, r.source, r.sequence_id))

    n = 0
    for o in store.list(prefix):
        if not o["Key"].endswith(".tar"):
            continue
        with tarfile.open(fileobj=io.BytesIO(store.read(o["Key"]))) as t:
            for name in t.getnames():
                if not name.endswith(".jpg") or name[:-4] not in want:
                    continue
                blob = t.extractfile(name).read()
                for ds, part, cls, src, seq in want[name[:-4]]:
                    d = a.out / ds / part / cls
                    d.mkdir(parents=True, exist_ok=True)
                    (d / f"{src}__{seq}__{name[:-4]}.jpg").write_bytes(blob)
                    n += 1
    print(f"\nзаписано {n:,} файлов в {a.out}")


if __name__ == "__main__":
    main()

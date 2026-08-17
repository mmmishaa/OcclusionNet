"""Отбор кадров и разбиение на выборки — до всякой работы с картинками.

Всё здесь делается по манифесту, то есть по таблице. Ни один кадр не читается:
решить, что попадёт в датасет, можно на десяти тысячах строк за миллисекунды,
а стоит это решение дороже всего остального.
"""

from __future__ import annotations

import hashlib

import pandas as pd


def _combo(labels) -> str:
    return "+".join(sorted(labels)) or "clean"


def apply_select(df: pd.DataFrame, recipe: dict) -> pd.DataFrame:
    """Фильтр из recipe.select.where, синтаксис pandas.query."""
    where = (recipe.get("select") or {}).get("where")
    if not where:
        return df
    out = df.query(where)
    print(f"select: {len(out):,} из {len(df):,}")
    return out


def apply_sampling(df: pd.DataFrame, recipe: dict) -> pd.DataFrame:
    """Прореживание внутри сиквенса и снятие точных дублей.

    Прореживание задаётся шагом, а не частотой в герцах: временных меток в
    манифесте нет, а выводить их из имён файлов — гадание.
    """
    s = recipe.get("sampling") or {}
    out = df

    step = int(s.get("every_nth", 1))
    if step > 1:
        out = (out.sort_values(["source", "sequence_id", "raw_path"])
                  .groupby(["source", "sequence_id"], group_keys=False)
                  .apply(lambda g: g.iloc[::step]))
        print(f"прореживание каждый {step}-й: {len(out):,} из {len(df):,}")

    if (s.get("dedup") or {}).get("exact_phash", True):
        before = len(out)
        # phash 0 бывает у полностью однородных кадров, их не схлопываем
        nz = out[out.phash != 0]
        out = pd.concat([out[out.phash == 0],
                         nz.drop_duplicates(subset="phash", keep="first")])
        if before != len(out):
            print(f"точные дубли по phash: убрано {before - len(out):,}")
    return out


def apply_balance(df: pd.DataFrame, recipe: dict, seed: int) -> pd.DataFrame:
    """Потолок на группу источник x сочетание меток.

    Источник входит в группу намеренно: иначе класс, целиком пришедший из
    одного датасета, останется перекошенным и модель выучит датасет.
    """
    b = recipe.get("balance") or {}
    cap = b.get("max_per_group")
    if not cap:
        return df
    out = (df.assign(_combo=df.labels.apply(_combo))
             .groupby(["source", "_combo"], group_keys=False)
             .apply(lambda g: g.sample(min(len(g), int(cap)), random_state=seed))
             .drop(columns="_combo"))
    print(f"баланс, потолок {cap} на группу: {len(out):,} из {len(df):,}")
    return out


def _greedy(seq_sizes: dict[str, int], ratios: dict[str, float],
            seed: str) -> dict[str, str]:
    """Раздаёт сиквенсы по выборкам, выравнивая доли КАДРОВ, а не сиквенсов.

    Сиквенсы сильно разного размера: у Evocargo по 1700 кадров, у CADC по 100.
    Если делить по счёту сиквенсов, доли кадров разъезжаются — на первой сборке
    вышло 22% в val вместо 15%. Поэтому сиквенсы идут от большего к меньшему, и
    каждый достаётся выборке с наибольшим отставанием от своей квоты.

    Порядок при равных размерах задаётся хешем: воспроизводимо и не зависит от
    порядка строк в манифесте.
    """
    total = sum(seq_sizes.values())
    target = {k: total * v for k, v in ratios.items()}
    got = {k: 0 for k in ratios}
    out: dict[str, str] = {}

    order = sorted(seq_sizes,
                   key=lambda n: (-seq_sizes[n],
                                  hashlib.sha1(f"{seed}|{n}".encode()).hexdigest()))
    for name in order:
        part = max(ratios, key=lambda k: target[k] - got[k])
        out[name] = part
        got[part] += seq_sizes[name]

    # каждая выборка должна получить хотя бы один сиквенс, если их хватает
    n = len(order)
    if n >= len(ratios):
        for part in ratios:
            if part not in out.values():
                donor = max((x for x in order if
                             sum(1 for y in order if out[y] == out[x]) > 1),
                            key=lambda x: seq_sizes[x], default=None)
                if donor is not None:
                    out[donor] = part
    return out


def assign_splits(df: pd.DataFrame, recipe: dict) -> pd.DataFrame:
    """Раздаёт train/val/test ЦЕЛЫМИ сиквенсами, согласованно между источниками.

    По кадрам резать нельзя: при съёмке 10-20 Гц соседние кадры почти
    идентичны, и случайный сплит кладёт в тест те же изображения, что в трейн.

    По источникам резать независимо тоже нельзя: выборка может целиком достаться
    одному датасету, и метрика будет про него, а не про помеху.
    """
    sp = recipe.get("splits") or {}
    ratios = sp.get("ratios") or {"train": 0.7, "val": 0.15, "test": 0.15}
    seed = int(sp.get("seed", 0))

    df = df.assign(_combo=df.labels.apply(_combo))
    seqs = (df.groupby(["source", "sequence_id"])["_combo"]
              .agg(lambda s: s.value_counts().index[0])
              .reset_index(name="combo"))
    counts = df.groupby(["source", "sequence_id"]).size()   # кадров в сиквенсе

    min_src = int((recipe.get("balance") or {}).get("min_sources_per_label", 1))
    pairs = [(l, s) for ls, s in zip(df.labels, df.source) for l in (list(ls) or ["clean"])]
    per_label = (pd.DataFrame(pairs, columns=["label", "source"])
                   .groupby("label")["source"].nunique())
    weak_labels = set(per_label[per_label < min_src].index)
    if weak_labels:
        print(f"меток с источниками < {min_src}: {sorted(weak_labels)}")

    assign: dict[tuple[str, str], str] = {}
    for combo, grp in seqs.groupby("combo"):
        sources = sorted(grp.source.unique())

        # все метки сочетания слабые — сочетание не идёт в оценку
        if set(combo.split("+")) <= weak_labels:
            for r in grp.itertuples():
                assign[(r.source, r.sequence_id)] = "train"
            continue

        proposal: dict[tuple[str, str], str] = {}
        for src in sources:
            names = sorted(grp[grp.source == src].sequence_id)
            sizes = {n: int(counts.get((src, n), 0)) for n in names}
            for name, part in _greedy(sizes, ratios, f"{seed}|{src}").items():
                proposal[(src, name)] = part

        # проверка согласованности: и val, и test должны собрать min_src источников
        cover = {part: {src for (src, _), p in proposal.items() if p == part}
                 for part in ("val", "test")}
        thin = [p for p in ("val", "test") if len(cover[p]) < min_src]
        if thin:
            print(f"  {combo}: {', '.join(thin)} собрали бы меньше {min_src} "
                  f"источников — сочетание уходит только в train")
            for key in proposal:
                proposal[key] = "train"
        assign.update(proposal)

    out = df.drop(columns="_combo").copy()
    out["split"] = [assign[(s, q)] for s, q in zip(out.source, out.sequence_id)]
    return out


def build_plan(df: pd.DataFrame, recipe: dict) -> pd.DataFrame:
    seed = int((recipe.get("splits") or {}).get("seed", 0))
    out = apply_select(df, recipe)
    out = apply_sampling(out, recipe)
    out = apply_balance(out, recipe, seed)
    out = assign_splits(out, recipe)

    print("\nсостав датасета:")
    tab = (out.assign(метка=out.labels.apply(_combo))
              .groupby(["split", "метка"]).size().rename("кадров"))
    print(tab.to_string())

    leak = (out.groupby("sequence_id")["split"].nunique() > 1).sum()
    print(f"\nсиквенсов, попавших в несколько выборок: {leak}  (должно быть 0)")

    # Доли по всему датасету ни о чём не говорят: метки с одним источником
    # уходят в train целиком и перекашивают картину. Смысл имеет доля внутри
    # той части, которая вообще участвует в оценке.
    ev_labels = {l for ls in out[out.split != "train"].labels for l in ls} or {"clean"}
    ev = out[out.labels.apply(lambda ls: bool(set(ls) & ev_labels))]
    if len(ev):
        print("\nдоли выборок среди оцениваемых меток "
              f"({', '.join(sorted(ev_labels))}):")
        for part in ("train", "val", "test"):
            c = int((ev.split == part).sum())
            print(f"  {part:<6}{c:>6}  {100*c/len(ev):5.1f}%")
        print(f"  вне оценки (только train): {len(out) - len(ev):,} кадров")

    print("\nисточники внутри выборок (по меткам, идущим в оценку):")
    ev = out[out.split != "train"]
    if len(ev):
        print(ev.assign(метка=ev.labels.apply(_combo))
                .groupby(["метка", "split"])["source"].nunique()
                .rename("источников").to_string())

    for part in ("val", "test"):
        if (out.split == part).sum() == 0:
            print(f"\nВНИМАНИЕ: выборка {part} пуста. Это не сбой сборки, а состояние\n"
                  "данных: у каждой метки пока единственный источник, и честно\n"
                  "оценивать не на чем — любая метрика мерила бы узнавание датасета.\n"
                  "Лечится вторым независимым источником на метку, не правкой кода.")
            break
    return out

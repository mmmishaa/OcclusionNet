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


def assign_splits(df: pd.DataFrame, recipe: dict) -> pd.DataFrame:
    """Раздаёт train/val/test ЦЕЛЫМИ сиквенсами.

    По кадрам резать нельзя: при съёмке 10-20 Гц соседние кадры почти
    идентичны, и случайный сплит кладёт в тест те же изображения, что в трейн.
    Метрики получаются завышенными, а на новой съёмке модель разваливается.
    """
    sp = recipe.get("splits") or {}
    ratios = sp.get("ratios") or {"train": 0.7, "val": 0.15, "test": 0.15}
    seed = int(sp.get("seed", 0))

    # доминирующее сочетание меток сиквенса — по нему стратифицируем
    df = df.assign(_combo=df.labels.apply(_combo))
    seqs = (df.groupby(["source", "sequence_id"])["_combo"]
              .agg(lambda s: s.value_counts().index[0])
              .reset_index(name="combo"))

    # Метки, представленные единственным источником, не идут в val и test:
    # иначе оценка меряет узнавание датасета, а не помехи.
    #
    # Считать надо по ОТДЕЛЬНЫМ меткам, а не по сочетаниям: иначе snowfall и
    # snowfall+soiling выглядят как две разные метки, и snowfall с двумя
    # источниками ошибочно признаётся слабым.
    min_src = int((recipe.get("balance") or {}).get("min_sources_per_label", 1))
    pairs = [(l, s) for ls, s in zip(df.labels, df.source) for l in (list(ls) or ["clean"])]
    per_label = (pd.DataFrame(pairs, columns=["label", "source"])
                   .groupby("label")["source"].nunique())
    weak_labels = set(per_label[per_label < min_src].index)
    if weak_labels:
        print(f"меток с источниками < {min_src}: {sorted(weak_labels)}")

    def is_weak(combo: str) -> bool:
        """Сиквенс уходит только в train, если ВСЕ его метки слабые.

        Если хотя бы одна метка обеспечена двумя источниками, сиквенс нужен в
        оценке — иначе сильная метка останется без val и test за компанию.
        """
        return set(combo.split("+")) <= weak_labels

    assign = {}
    for (src, combo), grp in seqs.groupby(["source", "combo"]):
        names = sorted(grp.sequence_id)
        # порядок задаётся хешем от имени и seed: воспроизводимо и не зависит
        # от порядка строк в манифесте
        names.sort(key=lambda n: hashlib.sha1(f"{seed}|{src}|{n}".encode()).hexdigest())
        if is_weak(combo):
            for n in names:
                assign[(src, n)] = "train"
            continue
        n_total = len(names)
        n_train = max(1, round(n_total * ratios.get("train", 0.7)))
        n_val = round(n_total * ratios.get("val", 0.15))
        if n_total >= 3:
            n_val = max(1, n_val)
        for i, n in enumerate(names):
            assign[(src, n)] = ("train" if i < n_train
                                else "val" if i < n_train + n_val
                                else "test")

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

    for part in ("val", "test"):
        if (out.split == part).sum() == 0:
            print(f"\nВНИМАНИЕ: выборка {part} пуста. Это не сбой сборки, а состояние\n"
                  "данных: у каждой метки пока единственный источник, и честно\n"
                  "оценивать не на чем — любая метрика мерила бы узнавание датасета.\n"
                  "Лечится вторым независимым источником на метку, не правкой кода.")
            break
    return out

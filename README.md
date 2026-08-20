# OcclusionNet

Модель для выявления и классификации окклюзий на кадрах с автомобильных камер.

Цель — классифицировать тип загрязнения/помехи на изображении и оценить степень
загрязнённости кадра. Задача multilabel: на одном кадре может быть несколько
окклюзий одновременно, чистый кадр — это нулевой вектор меток.

**Классы:** `DaytimeFlare`, `Fog`, `MotionBlur`, `NighttimeFlare`, `Raindrops`,
`Reflections`, `Soil`. Плюс `Clean` — отсутствие всех семи.

## Структура

| Папка | Что внутри |
|---|---|
| `training/` | `occlusionnet.ipynb` — обучение модели |
| `validation/` | `testclassifier.ipynb` — прогон обученных весов на отложенной выборке |
| `occlusion_score/` | оценка степени загрязнённости через расхождение двух SegFormer'ов |
| `data/` | описание датасетов, манифесты, заливка Google Drive → Kaggle |
| `infra/` | ClearML-сервер в Yandex Cloud (OpenTofu), см. [infra/README.md](infra/README.md) |

## Модель

EfficientNet-B3 (ImageNet-веса) в качестве backbone, классификатор заменён на
`Identity`, поверх — семь независимых MLP-голов (`Linear → LayerNorm → GELU →
Dropout → Linear(1)`), по одной на класс. Логиты конкатенируются в вектор длины 7,
лосс — `BCEWithLogitsLoss`.

Обучение: AdamW с разными lr для backbone (`1e-5`) и голов (`2e-4`), weight decay
`1e-3`, 3 эпохи линейного warmup → cosine annealing до `1e-6`, всего 20 эпох,
batch 16, mixed precision. Лучший чекпойнт выбирается по macro F1 на тесте.

## Данные

Ссылки и подробности предобработки — в [data/occlusions_datasets.md](data/occlusions_datasets.md).

* Kaggle: https://www.kaggle.com/datasets/mishasavinov/occlusion-dataset
* Google Drive: [папка с датасетами](https://drive.google.com/drive/folders/1P81qiLkSbpOT2rNEV-64RXUGqdlRCUep)

Раскладка — `Datasets/<Класс>/<Источник>/{train,test}/`, кадры 512×512.
Папка `Clean/` разбирается отдельным датасетом и даёт нулевые таргеты.

Залить датасет с Drive на Kaggle (запускается в Colab, рядом нужен `config.env`
с `KAGGLE_USERNAME` / `KAGGLE_API_TOKEN` и `DRIVE_SOURCE_PATH`):

```bash
python data/gdrive_to_kaggle_data_uploader.py
```

## Как запускать

Всё считается в ноутбуках снаружи облака — Kaggle, Colab или DataSphere.
В облаке живёт только ClearML для трекинга экспериментов.

### 1. Обучение — `training/occlusionnet.ipynb`

1. Подключите датасеты и проверьте пути в ячейке с `DATA_PATH` и `CLEAN_*_PATH`.
2. Заполните пустые `%env CLEARML_*` во второй ячейке своими ключами
   (Kaggle — **Add-ons → Secrets**, Colab — панель ключей слева; ключи создаются
   в ClearML: **Settings → Workspace → Create new credentials**).
3. Выполните ноутбук целиком.

По ходу в ClearML уходят loss, per-class и macro precision/recall/F1, learning
rate и норма градиентов. В конце подбираются пороги по каждому классу (перебор
0.1…0.9 с шагом 0.02 по F1) и в артефакты задачи загружаются:

`best_weights`, `best_thresholds_json`, `best_metrics_json`,
`metrics_csv_0.5`, `metrics_csv_tuned`.

### 2. Валидация — `validation/testclassifier.ipynb`

Пропишите `TRAIN_TASK_ID` — id задачи обучения в ClearML, веса и пороги
подтянутся из её артефактов автоматически. Проверьте `VAL_ROOT` (папка
`val_samples/<Класс>/`). Ноутбук считает метрики при пороге 0.5, при сохранённых
и при заново подобранных порогах, печатает размер модели и рисует предсказания
на отдельных кадрах.

### 3. Степень загрязнённости — `occlusion_score/diff_model_conf/`

Скор без обучения: один и тот же кадр сегментируется тяжёлой (`segformer-b5`) и
лёгкой (`segformer-b0`) моделями Cityscapes, разница их уверенности усредняется
по пикселям. Чем сильнее расходятся модели, тем грязнее кадр. Считаются два
варианта — разница индивидуальных максимумов и разница на классе, выбранном B5;
формулы в первой ячейке ноутбука. Результат — `occlusion_scores.csv`.

## Зависимости

`torch`, `torchvision`, `clearml`, `scikit-learn`, `pandas`, `numpy`, `pillow`,
`tqdm`, `matplotlib`, `seaborn`; для скора дополнительно `transformers`.
На Kaggle/Colab всё, кроме `clearml`, уже стоит — его ставит первая ячейка.

# Руководство по датасетам

Ссылка на гугл диск со всеми датасетами:

https://drive.google.com/drive/folders/1P81qiLkSbpOT2rNEV-64RXUGqdlRCUep?usp=drive_link

# Структура папки Datasets:

```text
Datasets/
├── Clean/
│   ├── ACDC/
│   │   ├── train/
│   │   └── test/
│   └── STF/
│       ├── train/
│       └── test/
│
├── Fog/
│   ├── ACDC/
│   │   ├── train/
│   │   └── test/
│   └── STF/
│       ├── fog_dense/
|       |   ├── train/
|       |   └── test/
│       └── fog_light/
|           ├── train/
|           └── test/
│
├── .../
│   ├── .../
│   │   ├── train/
│   │   └── test/
│   └── .../
│       ├── train/
│       └── test/
│
├── .../
│   ├── .../
│   │   ├── train/
│   │   └── test/
│   └── .../
│       ├── train/
│       └── test/
│
├── .../
│   ├── .../
│   │   ├── train/
│   │   └── test/
│   └── .../
│       ├── train/
│       └── test/
│
├── .../
│   ├── .../
│   │   ├── train/
│   │   └── test/
│   └── .../
│       ├── train/
│       └── test/
│
├── .../
│   ├── .../
│   │   ├── train/
│   │   └── test/
│   └── .../
│       ├── train/
│       └── test/
│
└── .../
    ├── .../
    │   ├── train/
    │   └── test/
    └── .../
        ├── train/
        └── test/
```

# Предобработка датасетов

## 1. Clean

Данные собирались из 2 датасетов:

* **ACDC**:
  * Исходно: 1000 кадров
  * Фильтрация: удаление дубликатов по парным кадрам с туманом (MAE >= 15.0). Оставлено **591 кадр**
  * Сплит: по маршрутам `GOPRxxxx` -> Train: **591**, Test: **230**
  * Геометрия: Bicubic Downscale ($1920 \times 1080 \rightarrow 910 \times 512$) + Center Crop $512 \times 512$

* **STF**:
  * Исходно: 2183 кадра
  * Фильтрация: обрезка краев на 6% + удаление дубликатов (MAE >= 15.0). Оставлено **2115 кадров**
  * Сплит: по сессиям `YYYY-MM-DD_HH-MM-SS` -> Train: **1692**, Test: **423**
  * Геометрия: Bicubic Downscale ($1689 \times 901 \rightarrow 960 \times 512$) + Center Crop $512 \times 512$

## 2. Fog

Данные собирались из 2 датасетов:

* **ACDC**:
  * Исходно: 1000 кадров
  * Фильтрация: удаление дубликатов (MAE >= 15.0). Оставлено **591 кадр**
  * Сплит: по маршрутам `GOPRxxxx` -> Train: **591**, Test: **230**
  * Геометрия: Bicubic Downscale ($1920 \times 1080 \rightarrow 910 \times 512$) + Center Crop $512 \times 512$

* **STF**:
  * Исходно: 1205 кадров (572 `dense_fog` + 633 `light_fog`)
  * Фильтрация: обрезка краев на 6% + удаление дубликатов (MAE >= 15.0). Оставлено **805 кадров** (`dense_fog`: 320, `light_fog`: 485)
  * Сплит: по сессиям `YYYY-MM-DD_HH-MM-SS` -> Train: **629**, Test: **176**
  * Геометрия: Bicubic Downscale ($1689 \times 901 \rightarrow 960 \times 512$) + Center Crop $512 \times 512$

## 3

## 4

## 5

## 6

## 7

## 8

## Сводная таблица итогового датасета

| Класс | Источники | Train | Test | Всего |
| :--- | :--- | :---: | :---: | :---: |
| **Clean** | ACDC + STF | 2283 | 653 | **2936** |
| **Fog** | ACDC + STF | 1220 | 406 | **1626** |
| **Итого** | **ACDC + STF** | **3503** | **1059** | **4562** |
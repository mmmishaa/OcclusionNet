# Инфраструктура ClearML

Сервер трекинга экспериментов в Yandex Cloud. Обучение идёт снаружи — Google
Colab, Kaggle, DataSphere и локальные машины, — в облаке живёт только control plane.

Разбор граблей и объяснения принятых решений — в [INSIGHTS.md](INSIGHTS.md).

## Состав

| Ресурс | Назначение |
|---|---|
| VPC + подсеть `10.10.0.0/24` | зона `ru-central1-a` |
| Security group | 80/443 отовсюду, 22 по ключу |
| ВМ 4 vCPU (20%) / 8 ГБ / 50 ГБ SSD | ClearML Server за Caddy |
| Object Storage | артефакты и финальные чекпойнты |
| Lockbox | S3-ключи, пароль веб-входа |
| Сервисный аккаунт | `storage.admin`, `lockbox.payloadViewer` |
| Расписание снапшотов | ежедневно, хранение 3 штуки |
| DataSphere (опционально) | community, проект, бакет под датасеты |

## Требования

OpenTofu 1.8+ и `yc`. Конфигурация в файлах `.tofu` — запускать нужно именно
`tofu`, Terraform их не видит.

## Развёртывание

```bash
export YC_TOKEN=$(yc iam create-token)   # живёт 12 часов
tofu init
tofu apply
```

Готово через 8–10 минут: cloud-init ставит Docker, поднимает ClearML,
устанавливает Caddy и получает сертификат Let's Encrypt. Следить за ходом:

```bash
ssh ubuntu@$(tofu output -raw vm_public_ip) 'tail -f /var/log/bootstrap.log'
```

Признак успешного завершения — файл `/var/lib/clearml-bootstrap-done`.

## Снос

```bash
tofu destroy
```

Удаляет всё, включая диск и снапшоты. Бакеты должны быть пусты.

## Экономия

Выключенная ВМ не тарифицируется, платится только диск — останавливать на ночь
безопасно. При запуске публичный адрес меняется, но `/etc/cron.d/duckdns`
обновляет запись каждые 5 минут, домен переезжает сам.

## Доступы и секреты

```bash
tofu output -raw clearml_web_password   # пароль admin
tofu output -raw bucket                 # имя бакета
tofu output -raw clearml_conf           # шаблон ~/clearml.conf
tofu output -raw vm_public_ip
```

Имя бакета и пароль генерируются провайдером `random`. Перекрыть можно
переменными `bucket_name` и `clearml_web_password`, но **не на живом стенде** —
почему, см. INSIGHTS.

## Подключение участников

Каждый заходит на web-адрес, создаёт себе ключи в **Settings → Workspace →
Create new credentials** и кладёт их в `~/clearml.conf` по шаблону из
`tofu output -raw clearml_conf`.

## Ноутбуки: Colab, Kaggle, DataSphere

Ключи храните в родных хранилищах секретов, не в коде: Kaggle — **Add-ons →
Secrets**, Colab — панель ключа слева.

```python
%pip install -q clearml
import os
from clearml import Task

# Kaggle: from kaggle_secrets import UserSecretsClient
# Colab:  from google.colab import userdata
os.environ["CLEARML_WEB_HOST"]        = "https://app.occlusionnet.duckdns.org"
os.environ["CLEARML_API_HOST"]        = "https://api.occlusionnet.duckdns.org"
os.environ["CLEARML_FILES_HOST"]      = "https://files.occlusionnet.duckdns.org"
os.environ["CLEARML_API_ACCESS_KEY"]  = "..."
os.environ["CLEARML_API_SECRET_KEY"]  = "..."

Task.init(project_name="OcclusionNet", task_name="baseline")
```

### Где держать датасеты

В бакете `<bucket>-datasets`, внутри облака. Заезд идёт на временную ВМ
(`enable_data_vm`), локально ничего не качается — подробности в
[../data/README.md](../data/README.md).

Первоначальный план с Google Drive и Kaggle не пережил столкновения с
реальностью: Deep Blue не отдаёт данные HTTP-клиентам, у Drive упирается квота,
а распаковка сотен тысяч мелких файлов через Drive API идёт часами. Если всё же
считаете в Colab — помните, что скачивание из бакета наружу это платный
исходящий трафик, первые 100 ГБ в месяц бесплатны, дальше 1,42 руб за гигабайт.

## DataSphere

Выключен по умолчанию:

```hcl
enable_datasphere = true
```

Создаёт community, проект, сервисный аккаунт с ключом и отдельный бакет под
датасеты. В простое бесплатны — тарифицируются только запущенные вычисления.

Бакет подключается внутри проекта через S3-коннектор:

```bash
tofu output -raw datasphere_s3_access_key
tofu output -raw datasphere_s3_secret_key
```

### Лимиты расхода

```hcl
datasphere_max_units_per_hour      = 50   # потолок в час
datasphere_max_units_per_execution = 100  # потолок на один запуск
```

Второй ловит забытый на ночь ноутбук с GPU. При исчерпании проект останавливает
вычисления, а не продолжает тратить.

### Доступ команде

Выдан группе организации Studcamp целиком — добавили человека в группу, доступ
появился автоматически:

```hcl
datasphere_members = ["group:aje4ob7jsve1c7dgtneq"]
```

Поштучно тоже можно, префикс другой:

```bash
yc iam user-account get <логин или почта>
```

```hcl
datasphere_members = ["group:aje...", "userAccount:aje..."]
```

## Не автоматизировано

**Бюджетный алерт.** Ни в провайдере, ни в `yc` этого нет — только консоль,
Billing → Бюджеты. Поставьте порог с уведомлением на почту, особенно при
включённом DataSphere.

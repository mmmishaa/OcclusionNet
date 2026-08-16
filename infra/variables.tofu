variable "cloud_id" {
  type = string
}

variable "folder_id" {
  type = string
}

variable "zone" {
  description = "Одна зона на всё — трафик внутри зоны бесплатный."
  type        = string
  default     = "ru-central1-a"
}

# --- DuckDNS -------------------------------------------------------------

variable "duckdns_subdomain" {
  description = "Имя на DuckDNS без суффикса, например occlusionnet"
  type        = string
  default     = "occlusionnet"
}

variable "duckdns_token" {
  description = "Токен с главной страницы duckdns.org"
  type        = string
  sensitive   = true
}

variable "acme_email" {
  description = "Почта для уведомлений Let's Encrypt"
  type        = string
}

# --- Доступ --------------------------------------------------------------

variable "ssh_public_key_path" {
  type    = string
  default = "~/.ssh/id_ed25519.pub"
}

variable "ssh_cidr" {
  description = <<-EOT
    Откуда разрешён SSH. По умолчанию отовсюду: пускает только ключ,
    пароли и root отключены. Сузить до своего /32 можно в любой момент.
  EOT
  type        = string
  default     = "0.0.0.0/0"
}

variable "clearml_web_password" {
  description = <<-EOT
    Пароль веб-входа ClearML, логин admin.
    Пусто — сгенерируется провайдером random, смотреть в
    tofu output -raw clearml_web_password
  EOT
  type        = string
  sensitive   = true
  default     = ""
}

# --- Размеры -------------------------------------------------------------

variable "vm_cores" {
  type    = number
  default = 4
}

variable "vm_core_fraction" {
  description = "Сервер почти всегда простаивает, пики закрывает burst до полных ядер."
  type        = number
  default     = 20
}

variable "vm_memory_gb" {
  description = "Официальный минимум ClearML. Хватает, т.к. артефакты вынесены в Object Storage."
  type        = number
  default     = 8
}

variable "disk_gb" {
  description = "Под Elasticsearch и MongoDB. Артефакты сюда не попадают."
  type        = number
  default     = 50
}

variable "bucket_name" {
  description = <<-EOT
    Имя бакета, глобально уникальное. Пусто — соберётся как
    occlusionnet-clearml-<случайные 6 символов>.
  EOT
  type        = string
  default     = ""
}

variable "artifact_expire_days" {
  description = "Автоудаление артефактов — страховка от забытого бакета."
  type        = number
  default     = 30
}

# --- DataSphere (опционально) --------------------------------------------

variable "enable_datasphere" {
  description = <<-EOT
    Создавать ли community и проект DataSphere вместе с бакетом под датасеты.
    Сами по себе они бесплатны — тарифицируются только запущенные вычисления.
  EOT
  type        = bool
  default     = false
}

variable "organization_id" {
  type = string
}

variable "billing_account_id" {
  description = "Без него в community нельзя запускать платные вычисления."
  type        = string
}

variable "datasphere_bucket_name" {
  description = "Бакет под датасеты. Пусто — соберётся как <bucket_name>-datasets."
  type        = string
  default     = ""
}

variable "datasphere_max_units_per_hour" {
  description = <<-EOT
    Потолок расхода вычислительных единиц в час — главный предохранитель
    бюджета. При исчерпании проект останавливает вычисления, а не продолжает
    тратить.

    ВНИМАНИЕ на порядок величины: юнит стоит 0,0013 руб и считается посекундно.
    Конфигурация c1.4 без GPU расходует около 14 400 юнитов в час, gt4i.1 с
    T4 — около 180 000. Значение 50 остановит любые вычисления через несколько
    секунд после старта, и выглядеть это будет как непонятный сбой, а не как
    сработавший лимит. Осмысленный дневной потолок считать от цены: 1000 руб
    в час это примерно 770 000 юнитов.
  EOT
  type        = number
  default     = 770000
}

variable "datasphere_max_units_per_execution" {
  description = <<-EOT
    Потолок на один запуск: ловит забытый на ночь ноутбук.
    Про порядок величины см. datasphere_max_units_per_hour — 100 юнитов
    это доли секунды работы GPU, а не ночь.
  EOT
  type        = number
  default     = 10000
}

variable "datasphere_balance" {
  description = <<-EOT
    Баланс проекта в вычислительных единицах — запас, из которого списывается
    работа ячеек. Не путать с max_units_per_hour: тот ограничивает скорость
    расхода, а это общий остаток.

    Грабля: при создании проекта API проставляет баланс 0, и ячейки не
    запускаются вообще. В веб-интерфейсе это выглядит как молчаливый отказ,
    а не как исчерпанный лимит.

    Арифметика: юнит стоит 0,0013 руб. 1 000 000 юнитов это примерно 1300 руб,
    то есть около 5 часов на gt4i.1 с T4 (180 000 юнитов в час) или 70 часов
    на c1.4 без GPU (14 400 в час).

    null — не управлять полем из кода. Это оставит уже работающий проект как
    есть, но на новом стенде баланс снова окажется нулевым.
  EOT
  type        = number
  default     = null
}

variable "datasphere_members" {
  description = <<-EOT
    Кому дать доступ к community, в формате userAccount:<id>.
    ID участника: yc iam user-account get <email или логин>.
    Пусто — доступ только у владельца облака.
  EOT
  type        = list(string)
  default     = []
}

# --- Заездная ВМ (опционально) -------------------------------------------

variable "enable_data_vm" {
  description = <<-EOT
    Поднимать ли временную машину под заезд датасетов. Нужна на несколько
    часов: скачать источник, посчитать манифест, сложить сырьё в бакет.
    После заезда выставить обратно false и сделать apply — машина и её диск
    удалятся, платить будет не за что.
  EOT
  type        = bool
  default     = false
}

variable "data_vm_cores" {
  description = "Инвентаризация упирается в CPU: декодирование кадров и метрики."
  type        = number
  default     = 8
}

variable "data_vm_memory_gb" {
  type    = number
  default = 16
}

variable "data_vm_disk_gb" {
  description = <<-EOT
    Диск под скачанный архив и распаковку. 150 ГБ хватает на любой источник
    из sources.yaml поштучно. Тип network-hdd: чтение последовательное,
    IOPS не нужны.
  EOT
  type        = number
  default     = 150
}

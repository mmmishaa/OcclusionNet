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
    тратить. Поднять можно в любой момент.
  EOT
  type        = number
  default     = 50
}

variable "datasphere_max_units_per_execution" {
  description = "Потолок на один запуск: ловит забытый на ночь ноутбук."
  type        = number
  default     = 100
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

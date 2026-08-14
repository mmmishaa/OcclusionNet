# DataSphere — управляемые ноутбуки Yandex Cloud.
#
# Всё в этом файле создаётся только при enable_datasphere = true. Community и
# проект сами по себе бесплатны, деньги идут за запущенные вычисления, поэтому
# держать их созданными между сессиями не накладно.
#
# Отдельный бакет под датасеты, и вот почему он здесь уместен, а для Colab и
# Kaggle был бы вреден: DataSphere работает ВНУТРИ облака, трафик до Object
# Storage в том же регионе не тарифицируется как исходящий. Датасеты для
# ноутбуков DataSphere правильно держать именно тут, а не в Google Drive.
# Смешивать с бакетом артефактов ClearML не стоит: там lifecycle с
# автоудалением через 30 дней, датасетам такое противопоказано.

locals {
  ds_bucket = var.datasphere_bucket_name != "" ? var.datasphere_bucket_name : "${local.bucket_name}-datasets"
  ds_count  = var.enable_datasphere ? 1 : 0

  # Обе роли выдаются НА СООБЩЕСТВЕ: права распространяются вниз по иерархии,
  # поэтому проектная роль покрывает и текущий проект, и все будущие. Привязка
  # людей к конкретному проекту потребовала бы правки Terraform на каждый новый.
  ds_roles = [
    "datasphere.communities.developer",        # создавать проекты в сообществе
    "datasphere.community-projects.developer", # работать внутри проектов: IDE, вычисления
  ]
}

resource "yandex_datasphere_community" "main" {
  count              = local.ds_count
  name               = "occlusionnet"
  description        = "CV Studcamp: ноутбуки и эксперименты"
  organization_id    = var.organization_id
  billing_account_id = var.billing_account_id
}

# Доступ команде. Одной роли на сообщество мало: communities.developer даёт
# право создавать проекты, но не работать в них — для запуска IDE и вычислений
# нужна community-projects.developer.
#
# Имена ролей именно такие: множественное число и точка, не дефис. Похожего на
# вид datasphere.community-developer не существует, и apply с ним падает уже
# после создания community. Список: yc iam role list | grep datasphere
resource "yandex_datasphere_community_iam_binding" "members" {
  for_each = var.enable_datasphere && length(var.datasphere_members) > 0 ? toset(local.ds_roles) : toset([])

  community_id = yandex_datasphere_community.main[0].id
  role         = each.value
  members      = var.datasphere_members
}

# --- Сервисный аккаунт проекта -------------------------------------------
# Отдельный от clearml-server: у того storage.admin, здесь хватает editor.

resource "yandex_iam_service_account" "datasphere" {
  count       = local.ds_count
  name        = "datasphere-project"
  description = "Проект DataSphere: чтение и запись датасетов"
}

resource "yandex_resourcemanager_folder_iam_member" "ds_storage" {
  count     = local.ds_count
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.datasphere[0].id}"
}

# Проект запускает вычисления от имени этого аккаунта — нужно право его использовать.
resource "yandex_resourcemanager_folder_iam_member" "ds_sa_user" {
  count     = local.ds_count
  folder_id = var.folder_id
  role      = "iam.serviceAccounts.user"
  member    = "serviceAccount:${yandex_iam_service_account.datasphere[0].id}"
}

resource "yandex_iam_service_account_static_access_key" "datasphere_s3" {
  count              = local.ds_count
  service_account_id = yandex_iam_service_account.datasphere[0].id
  description        = "Ключ для S3-коннектора DataSphere"
}

# --- Проект ---------------------------------------------------------------

resource "yandex_datasphere_project" "main" {
  count        = local.ds_count
  name         = "occlusionnet"
  description  = "Обучение и эксперименты, трекинг уходит в ClearML"
  community_id = yandex_datasphere_community.main[0].id

  limits = {
    max_units_per_hour      = var.datasphere_max_units_per_hour
    max_units_per_execution = var.datasphere_max_units_per_execution
  }

  settings = {
    service_account_id = yandex_iam_service_account.datasphere[0].id
    default_folder_id  = var.folder_id

    # subnet_id намеренно не задан. Привязка к нашей подсети означает, что
    # вычислениям проекта понадобится выход в интернет через NAT-шлюз (pip,
    # веса моделей, наш же ClearML) — а это отдельные деньги за шлюз и за
    # трафик. Без subnet_id проект работает в управляемой сети Yandex, где
    # интернет уже есть, а до ClearML достучится по публичному адресу с TLS.
    # Привязывать подсеть нужно только ради доступа к приватным ресурсам.
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.ds_sa_user]
}

# --- Бакет под датасеты ---------------------------------------------------

resource "yandex_storage_bucket" "datasets" {
  count = local.ds_count

  bucket = local.ds_bucket

  # Создаём ключом clearml-server: у него storage.admin, а у datasphere-project
  # только editor, которого на настройку самого бакета не хватает.
  access_key = yandex_iam_service_account_static_access_key.s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.s3.secret_key

  anonymous_access_flags {
    read = false
    list = false
  }

  versioning {
    enabled = false
  }

  lifecycle_rule {
    id      = "abort-incomplete"
    enabled = true

    # Автоудаления объектов здесь намеренно нет: датасеты это входные данные,
    # тихо стереть их через N дней — потерять работу. Чистим только обрывки
    # многочастевых загрузок, они тарифицируются, не показываясь в списке.
    abort_incomplete_multipart_upload_days = 7
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.storage]
}

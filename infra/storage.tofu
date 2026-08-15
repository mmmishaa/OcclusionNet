# Бакет только под артефакты и финальные чекпойнты.
#
# Датасеты сюда НЕ кладём — у них отдельный бакет в datasphere.tofu. Причина
# не в географии, а в lifecycle: здесь стоит автоудаление через N дней, что
# для входных данных недопустимо.
#
# Изначально датасеты планировались снаружи, в Google Drive и Kaggle, рядом с
# Colab. От этого отказались: Deep Blue не отдаёт данные HTTP-клиентам, у Drive
# упирается квота, а распаковка сотен тысяч мелких файлов в Drive идёт часами.
# Заезд и хранение переехали в облако, см. data/README.md.

resource "yandex_iam_service_account_static_access_key" "s3" {
  service_account_id = yandex_iam_service_account.server.id
  description        = "S3-ключ для ClearML SDK и fileserver"
}

resource "yandex_storage_bucket" "artifacts" {
  bucket     = local.bucket_name
  access_key = yandex_iam_service_account_static_access_key.s3.access_key
  secret_key = yandex_iam_service_account_static_access_key.s3.secret_key

  anonymous_access_flags {
    read = false
    list = false
  }

  # Выключено намеренно: удвоило бы объём хранения, а ClearML и так пишет
  # каждый чекпойнт под уникальным именем — перезаписи не бывает.
  versioning {
    enabled = false
  }

  lifecycle_rule {
    id      = "expire-artifacts"
    enabled = true

    expiration {
      days = var.artifact_expire_days
    }

    # Colab рвёт сессию посреди заливки чекпойнта. Недозагруженные части
    # тарифицируются, но не видны в списке объектов — чистим принудительно.
    abort_incomplete_multipart_upload_days = 3
  }

  depends_on = [yandex_resourcemanager_folder_iam_member.storage]
}

# --- Lockbox -------------------------------------------------------------
# ВМ читает секреты при старте через свой сервисный аккаунт.
# В cloud-init и в git не попадает ничего.

resource "yandex_lockbox_secret" "clearml" {
  name        = "clearml-secrets"
  description = "S3-ключи и пароль веб-входа ClearML"
}

resource "yandex_lockbox_secret_version" "clearml" {
  secret_id = yandex_lockbox_secret.clearml.id

  entries {
    key        = "s3-access-key"
    text_value = yandex_iam_service_account_static_access_key.s3.access_key
  }

  entries {
    key        = "s3-secret-key"
    text_value = yandex_iam_service_account_static_access_key.s3.secret_key
  }

  entries {
    key        = "web-password"
    text_value = local.web_password
  }
}

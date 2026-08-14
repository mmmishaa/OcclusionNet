output "vm_public_ip" {
  value = yandex_compute_instance.clearml.network_interface[0].nat_ip_address
}

output "web_url" {
  value = "https://${local.app_host}"
}

output "ssh" {
  value = "ssh ubuntu@${yandex_compute_instance.clearml.network_interface[0].nat_ip_address}"
}

output "bucket" {
  value = yandex_storage_bucket.artifacts.bucket
}

# Пароль admin: заданный в tfvars либо сгенерированный.
output "clearml_web_password" {
  value     = local.web_password
  sensitive = true
}

output "s3_access_key" {
  value     = yandex_iam_service_account_static_access_key.s3.access_key
  sensitive = true
}

# --- DataSphere -----------------------------------------------------------
# Пусто, пока enable_datasphere = false.

output "datasphere_community_id" {
  value = one(yandex_datasphere_community.main[*].id)
}

output "datasphere_project_id" {
  description = "Открывать в консоли: DataSphere -> Сообщества -> Проекты."
  value       = one(yandex_datasphere_project.main[*].id)
}

output "datasphere_bucket" {
  value = one(yandex_storage_bucket.datasets[*].bucket)
}

# Для S3-коннектора внутри проекта DataSphere.
output "datasphere_s3_access_key" {
  value     = one(yandex_iam_service_account_static_access_key.datasphere_s3[*].access_key)
  sensitive = true
}

output "datasphere_s3_secret_key" {
  value     = one(yandex_iam_service_account_static_access_key.datasphere_s3[*].secret_key)
  sensitive = true
}

output "s3_secret_key" {
  value     = yandex_iam_service_account_static_access_key.s3.secret_key
  sensitive = true
}

# Раскрыть: tofu output -raw clearml_conf
output "clearml_conf" {
  description = "Шаблон ~/clearml.conf для участников. credentials взять в веб-интерфейсе: Settings -> Workspace -> Create new credentials."
  value       = <<-EOT
    api {
        web_server:   https://${local.app_host}
        api_server:   https://${local.api_host}
        files_server: https://${local.files_host}
        credentials { access_key: "ВАШ_КЛЮЧ", secret_key: "ВАШ_СЕКРЕТ" }
    }
    sdk {
        development.default_output_uri: "s3://storage.yandexcloud.net:443/${local.bucket_name}"
        aws.s3 {
            region: "ru-central1"
            credentials: [{
                host: "storage.yandexcloud.net:443"
                key: "${yandex_iam_service_account_static_access_key.s3.access_key}"
                secret: "${yandex_iam_service_account_static_access_key.s3.secret_key}"
                secure: true
            }]
        }
    }
  EOT
  sensitive   = true
}

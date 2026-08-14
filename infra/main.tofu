terraform {
  required_version = ">= 1.5"
  required_providers {
    yandex = {
      source  = "registry.terraform.io/yandex-cloud/yandex"
      version = "~> 0.220"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "yandex" {
  cloud_id  = var.cloud_id
  folder_id = var.folder_id
  zone      = var.zone
}

locals {
  domain     = "${var.duckdns_subdomain}.duckdns.org"
  app_host   = "app.${local.domain}"
  api_host   = "api.${local.domain}"
  files_host = "files.${local.domain}"

  # Заданное значение побеждает сгенерированное. Так развёртывание с нуля не
  # требует ручного openssl, а существующий стенд не переименовывается.
  #
  # Менять имя бакета на живом стенде нельзя просто так: cloud-init запекает
  # его в /opt/clearml/config/clearml.conf при загрузке, а на инстансе стоит
  # ignore_changes на user-data. ВМ не пересоздастся, и ClearML начнёт писать
  # артефакты в несуществующий бакет — молча. То же и с паролем: он попадает
  # в apiserver.conf на этапе загрузки.
  bucket_name  = var.bucket_name != "" ? var.bucket_name : "occlusionnet-clearml-${random_id.bucket_suffix.hex}"
  web_password = var.clearml_web_password != "" ? var.clearml_web_password : random_password.clearml_admin.result
}

# 3 байта = 6 hex-символов, как в именах, сгенерированных руками.
resource "random_id" "bucket_suffix" {
  byte_length = 3
}

resource "random_password" "clearml_admin" {
  length = 20
  # Спецсимволы выключены: пароль подставляется в HOCON-конфиг ClearML
  # внутри двойных кавычек, экранировать его там нечем.
  special = false
}

# Берём последний образ семейства, а не фиксированный ID: он обновляется.
data "yandex_compute_image" "ubuntu" {
  family = "ubuntu-2404-lts"
}

# --- Сеть ----------------------------------------------------------------
# Агентов в облаке нет: обучение идёт на Colab, Kaggle и локально.
# Поэтому ни NAT-шлюза, ни приватной подсети — только сервер с публичным IP.

resource "yandex_vpc_network" "main" {
  name = "clearml-net"
}

resource "yandex_vpc_subnet" "main" {
  name           = "clearml-subnet"
  zone           = var.zone
  network_id     = yandex_vpc_network.main.id
  v4_cidr_blocks = ["10.10.0.0/24"]
}

resource "yandex_vpc_security_group" "server" {
  name       = "clearml-server-sg"
  network_id = yandex_vpc_network.main.id

  # Нужен постоянно: Let's Encrypt валидирует по HTTP-01 при каждом продлении.
  ingress {
    protocol       = "TCP"
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "HTTP: ACME + редирект на HTTPS"
  }

  # Открыт миру намеренно: Colab и Kaggle ходят с непредсказуемых адресов.
  # Защита здесь — TLS и fixed_users ClearML, а не ограничение по IP.
  ingress {
    protocol       = "TCP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
    description    = "HTTPS: app/api/files через Caddy"
  }

  # Открыт в мир осознанно: парольная аутентификация и вход root в облачном
  # образе Ubuntu выключены, пускает только ключ. Плюс Ansible сможет ходить
  # с любого адреса. Сузить обратно — поставить свой /32 в ssh_cidr.
  ingress {
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = [var.ssh_cidr]
    description    = "SSH, аутентификация только по ключу"
  }

  egress {
    protocol       = "ANY"
    from_port      = 0
    to_port        = 65535
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}

# Порты 8080/8008/8081 наружу не открыты — Caddy проксирует их с localhost.

# --- IAM -----------------------------------------------------------------

resource "yandex_iam_service_account" "server" {
  name        = "clearml-server"
  description = "ВМ ClearML: секреты из Lockbox, артефакты в Object Storage"
}

# storage.admin, а не storage.editor: editor разрешает операции с объектами,
# но настройку самого бакета (версионирование, lifecycle) — только admin.
# Тот же аккаунт использует ВМ для заливки артефактов, где хватило бы editor.
resource "yandex_resourcemanager_folder_iam_member" "storage" {
  folder_id = var.folder_id
  role      = "storage.admin"
  member    = "serviceAccount:${yandex_iam_service_account.server.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "lockbox" {
  folder_id = var.folder_id
  role      = "lockbox.payloadViewer"
  member    = "serviceAccount:${yandex_iam_service_account.server.id}"
}

# --- Виртуальная машина --------------------------------------------------

resource "yandex_compute_instance" "clearml" {
  name        = "clearml-server"
  platform_id = "standard-v3"
  zone        = var.zone

  service_account_id = yandex_iam_service_account.server.id

  resources {
    cores         = var.vm_cores
    core_fraction = var.vm_core_fraction
    memory        = var.vm_memory_gb
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      type     = "network-ssd"
      size     = var.disk_gb
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.main.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.server.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${file(pathexpand(var.ssh_public_key_path))}"
    user-data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
      secret_id         = yandex_lockbox_secret.clearml.id
      domain            = local.domain
      app_host          = local.app_host
      api_host          = local.api_host
      files_host        = local.files_host
      acme_email        = var.acme_email
      bucket_name       = local.bucket_name
      duckdns_subdomain = var.duckdns_subdomain
      duckdns_token     = var.duckdns_token
    })
  }

  # Секрет должен существовать до старта ВМ: cloud-init читает его сразу.
  depends_on = [
    yandex_lockbox_secret_version.clearml,
    yandex_resourcemanager_folder_iam_member.lockbox,
  ]

  # cloud-init отрабатывает один раз, правки в нём не должны пересоздавать ВМ.
  lifecycle {
    ignore_changes = [metadata["user-data"]]
  }
}

# --- Бэкап ---------------------------------------------------------------

resource "yandex_compute_snapshot_schedule" "daily" {
  name     = "clearml-daily"
  disk_ids = [yandex_compute_instance.clearml.boot_disk[0].disk_id]

  schedule_policy {
    expression = "0 3 * * *"
  }

  # Удержание задаётся ЛИБО по количеству, либо по периоду. Если указать оба,
  # API принимает только snapshot_count и не возвращает retention_period —
  # и tofu plan вечно показывает попытку его выставить.
  snapshot_count = 3
}

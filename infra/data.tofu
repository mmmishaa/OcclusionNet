# Временная ВМ под заезд датасетов.
#
# Всё в этом файле создаётся только при enable_data_vm = true. Машина нужна на
# несколько часов: скачать источник, прогнать инвентаризацию, сложить сырьё в
# бакет — и погаснуть. Держать её постоянно смысла нет, поэтому она прерываемая
# и с отдельным диском, который уезжает вместе с ней.
#
# Смысл упражнения: гигабайты никогда не покидают облако. На локальную машину
# возвращаются только манифесты — parquet на единицы мегабайт.

locals {
  data_vm_count = var.enable_data_vm ? 1 : 0
}

resource "yandex_compute_instance" "data" {
  count = local.data_vm_count

  name        = "data-ingest"
  platform_id = "standard-v3"
  zone        = var.zone

  # Тот же сервисный аккаунт, что и у сервера: ему уже выданы storage.admin
  # и lockbox.payloadViewer, отдельная роль не нужна.
  service_account_id = yandex_iam_service_account.server.id

  # Прерываемая: втрое дешевле, а принудительный стоп не страшен — заезд
  # возобновляемый, состояние лежит в бакете.
  scheduling_policy {
    preemptible = true
  }

  resources {
    cores         = var.data_vm_cores
    core_fraction = 100 # инвентаризация упирается в CPU, доля 20% её растянет
    memory        = var.data_vm_memory_gb
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      # HDD, а не SSD: чтение идёт длинными последовательными кусками,
      # IOPS не нужны, а за 150 ГБ разница в цене заметная.
      type = "network-hdd"
      size = var.data_vm_disk_gb
    }
  }

  network_interface {
    subnet_id          = yandex_vpc_subnet.main.id
    nat                = true
    security_group_ids = [yandex_vpc_security_group.server.id]
  }

  metadata = {
    ssh-keys = "ubuntu:${file(pathexpand(var.ssh_public_key_path))}"
    user-data = templatefile("${path.module}/data-cloud-init.yaml.tftpl", {
      secret_id = yandex_lockbox_secret.clearml.id
      bucket    = local.ds_bucket
    })
  }

  depends_on = [
    yandex_lockbox_secret_version.clearml,
    yandex_resourcemanager_folder_iam_member.lockbox,
    yandex_resourcemanager_folder_iam_member.storage,
  ]

  lifecycle {
    ignore_changes = [metadata["user-data"]]
  }
}

output "data_vm_ssh" {
  value = length(yandex_compute_instance.data) > 0 ? "ssh ubuntu@${yandex_compute_instance.data[0].network_interface[0].nat_ip_address}" : "enable_data_vm = false"
}

output "datasets_bucket" {
  value = length(yandex_storage_bucket.datasets) > 0 ? yandex_storage_bucket.datasets[0].bucket : "не создан"
}

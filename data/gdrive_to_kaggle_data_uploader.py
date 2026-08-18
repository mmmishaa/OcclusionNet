import json
import os
import shutil
import subprocess
from pathlib import Path
from dotenv import dotenv_values
from google.colab import drive # type: ignore

drive.mount("/content/drive")

CONFIG_PATH = "config.env"

config = dotenv_values(CONFIG_PATH)
os.environ["KAGGLE_USERNAME"] = config["KAGGLE_USERNAME"]
os.environ["KAGGLE_KEY"] = config["KAGGLE_KEY"]

source_path = Path(
    config.get(
        "DRIVE_SOURCE_PATH",
        "/content/drive/MyDrive/Yandex StudCamp project/Datasets",
    )
)
tmp_dir = Path("/content/kaggle_upload_temp")

if tmp_dir.exists():
    shutil.rmtree(tmp_dir)
tmp_dir.mkdir(parents=True, exist_ok=True)

for item in source_path.iterdir():
    target = tmp_dir / item.name
    if item.is_dir():
        shutil.copytree(item, target)
    else:
        shutil.copy2(item, target)

metadata = {
    "title": config.get("DATASET_TITLE", "Occlusion Dataset"),
    "id": f"{config['KAGGLE_USERNAME']}/{config.get('DATASET_SLUG', 'occlusion-dataset')}",
    "licenses": [{"name": "CC0-1.0"}],
}

with open(tmp_dir / "dataset-metadata.json", "w", encoding="utf-8") as f:
    json.dump(metadata, f, indent=2)

subprocess.run(
    ["kaggle", "datasets", "create", "-p", str(tmp_dir), "-r", "zip", "--public"],
    check=True,
)
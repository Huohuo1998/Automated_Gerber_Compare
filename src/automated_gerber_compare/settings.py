from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path


def config_directory() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "AutomatedGerberCompare"


def default_output_directory() -> Path:
    return Path.home() / "Documents" / "AutomatedGerberCompare"


@dataclass(frozen=True)
class Settings:
    rcom_exe: str = ""
    database: str = "eda_server_db"
    port: int = 11000
    command_timeout: float = 3600
    tolerance_mils: float = 1.0
    map_resolution_mils: int = 100
    margin_inch: float = 0.2
    image_size: int = 2000

    def validate(self, require_executable: bool = True) -> None:
        if not re.fullmatch(r"[a-z0-9_.-]+", self.database):
            raise ValueError("Valor 数据库名称应为小写字母、数字、下划线、点或短横线")
        for name, value, low, high in (
            ("RCOM 端口", self.port, 1, 65535),
            ("网格大小 (mil)", self.map_resolution_mils, 10, 1000),
            ("截图像素边长", self.image_size, 100, 2000),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} 必须是 {low}–{high} 的整数")
        for name, value, low, high in (
            ("命令超时 (秒)", self.command_timeout, 1, 86400),
            ("比较容差 (mil)", self.tolerance_mils, 0.2, 10),
            ("截图外扩 (inch)", self.margin_inch, 0.001, 10),
        ):
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} 必须在 {low}–{high} 范围内")
        if require_executable and (not self.rcom_exe or not Path(self.rcom_exe).is_file()):
            raise ValueError("请在 Valor 配置中选择有效的 rcom.exe")


def load_settings(path: Path | None = None) -> Settings:
    path = path or config_directory() / "settings.json"
    if not path.exists():
        return Settings()
    settings = Settings(**json.loads(path.read_text(encoding="utf-8")))
    settings.validate(require_executable=False)
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    settings.validate()
    path = path or config_directory() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)

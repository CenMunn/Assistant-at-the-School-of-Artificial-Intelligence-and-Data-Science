"""统一从源代码位置定位项目文件，与终端所在目录无关。"""

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent.parent
TEXT_DIR = PROJECT_DIR / "texts"
IMAGE_DIR = PROJECT_DIR / "images"
RESOURCE_DIR = PROJECT_DIR / "resources"

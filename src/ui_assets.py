"""只嵌入明确指定的界面图片，不开放项目文件目录。"""
import base64
from functools import lru_cache
from project_paths import IMAGE_DIR


@lru_cache(maxsize=3)
def image_data(name):
    if name not in ('uibe-logo.png', 'user-avatar.png', 'assistant-avatar.png'):
        raise ValueError('未知界面图片。')
    return 'data:image/png;base64,' + base64.b64encode((IMAGE_DIR / name).read_bytes()).decode('ascii')

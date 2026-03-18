import yaml
import os

# 多加一层 dirname，从 myBot/plugins/ 跳回到 myBot/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

# 导出一个全局单例配置对象，供其他文件直接引用
config = load_config()
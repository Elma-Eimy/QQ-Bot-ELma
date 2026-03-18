import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter
import nonebot.log as logger

# 导入我们写好的数据库管理器
from plugins._db_manager import init_db, close_db

# 1. 初始化 NoneBot 引擎
nonebot.init()

# 2. 注册 OneBot V11 协议适配器
driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

# 数据库生命周期管理
@driver.on_startup
async def startup():
    await init_db()
    logger.logger.info("✨ 艾尔玛的记忆数据库已成功连接！")

@driver.on_shutdown
async def shutdown():
    await close_db()
    logger.logger.info("💤 艾尔玛的记忆数据库已安全关闭。")
# ==========================================

# 3. 加载业务代码（插件）
nonebot.load_plugins("plugins")

if __name__ == "__main__":
    # 点火启动
    nonebot.run()
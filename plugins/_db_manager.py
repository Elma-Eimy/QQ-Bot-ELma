import aiomysql
import json
import os
from dotenv import load_dotenv

load_dotenv()

# 从环境变量里面读取数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', '127.0.0.1'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD'),
    'db': os.getenv('DB_NAME', 'qq_bot'),
    'autocommit': True
}

if not DB_CONFIG['password']:
    raise ValueError("数据库密码未配置！请检查 .env 文件。")

# 全局连接池变量
pool = None


async def init_db():
    """初始化数据库连接池，在NoneBot启动时调用"""
    global pool
    pool = await aiomysql.create_pool(**DB_CONFIG)


async def close_db():
    """关闭数据库连接池，在NoneBot关闭时调用"""
    global pool
    if pool:
        pool.close()
        await pool.wait_closed()


async def update_user_interaction(user_id: str, nickname: str):
    """更新用户的互动次数，如果是新用户则自动插入"""
    global pool
    if not pool: return

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            sql = """
                INSERT INTO users (user_id, nickname, interaction_count) 
                VALUES (%s, %s, 1)
                ON DUPLICATE KEY UPDATE 
                interaction_count = interaction_count + 1,
                nickname = %s;
            """
            await cur.execute(sql, (user_id, nickname, nickname))


async def get_user_profile(user_id: str):
    """获取用户的画像和事实，用于拼接Prompt"""
    global pool
    if not pool: return None

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute("SELECT nickname, affinity FROM users WHERE user_id = %s", (user_id,))
            user = await cur.fetchone()

            if not user: return None

            await cur.execute("SELECT fact_key, fact_value FROM user_facts WHERE user_id = %s", (user_id,))
            facts = await cur.fetchall()

            user['facts'] = {f['fact_key']: f['fact_value'] for f in facts}
            return user


async def upsert_user_fact(user_id: str, fact_key: str, fact_value: str):
    """
    最清爽的事实更新逻辑：
    如果 user_id + fact_key 已存在，就直接覆盖 value；不存在则插入。
    """
    global pool
    if not pool: return

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            sql = """
                INSERT INTO user_facts (user_id, fact_key, fact_value) 
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                fact_value = VALUES(fact_value);
            """
            await cur.execute(sql, (user_id, fact_key, fact_value))

async def save_chat_message(user_id: str, role: str, content_list: list):
    """把单条聊天记录持久化到数据库"""
    global pool
    if not pool: return

    # 把大模型格式的content列表转成JSON字符串存进去
    content_json = json.dumps(content_list, ensure_ascii=False)

    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO chat_messages (user_id, role, content) VALUES (%s, %s, %s)",
                (user_id, role, content_json)
            )


async def get_recent_chat_messages(user_id: str, limit: int = 20):
    """提取最近的N条聊天记录用于恢复上下文"""
    global pool
    if not pool: return []

    async with pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 倒序查出最近的N条（也就是最新的记录）
            await cur.execute(
                "SELECT role, content FROM chat_messages WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                (user_id, limit)
            )
            rows = await cur.fetchall()

            # 因为查出来是最新的在前，大模型需要最旧的在前，所以要把列表反转一下
            history = []
            for row in reversed(rows):
                history.append({
                    "role": row['role'],
                    "content": json.loads(row['content'])
                })
            return history
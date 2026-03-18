import os
import jieba.posseg as pseg
import aiohttp
import chromadb
import uuid
import time
import asyncio
import re
import math
from ._config_loader import config
from dotenv import load_dotenv

load_dotenv()

# 你的豆包 API 密钥
DOUBAO_API_KEY = os.getenv("DOUBAO_EMBEDDING_KEY")
DOUBAO_EMBEDDING_URL = config['llm']['embedding_api_url']
# 【注意】这里填你新申请的 Embedding 模型接入点！
DOUBAO_EMBEDDING_MODEL = config['llm']['embedding_model']
TIME_DECAY_ALPHA=config['memory']['time_decay_alpha']


# 初始化本地的 ChromaDB 客户端，数据会保存在当前目录下的 chroma_data 文件夹中
chroma_client = chromadb.PersistentClient(path="./chroma_data")
# 获取或创建一个叫做 elma_memory 的集合
collection = chroma_client.get_or_create_collection(name="elma_memory")

USELESS_PHRASES = {
    "这样吗", "那很坏了", "闹麻了", "确实", "啊这", "6", "666",
    "哦", "嗯", "好", "好的", "行", "没毛病", "牛逼", "绝了", "草"
}
STOP_WORDS = {"是", "有", "在", "去", "就", "的", "了", "啊", "呢", "这", "那", "我", "你", "他"}


def is_worth_remembering(text: str) -> bool:
    """
    高阶语义信息熵过滤器：结合正则与 Jieba 词性标注
    """
    text = text.strip()
    pure_text = re.sub(r'[^\w\s\u4e00-\u9fa5]', '', text)

    # 1. 基础拦截：黑名单、太短、或者是无意义的重复拟声词
    if text in USELESS_PHRASES or len(pure_text) < 2:
        return False
    if re.match(r'^(哈)+$|^(草)+$|^(w)+$|^(呜)+$', text, re.IGNORECASE):
        return False

    # 2. 高阶词性拦截 (词法分析)
    # 我们认为有信息熵的词性前缀：
    # n: 名词 (包含 nr 人名, ns 地名, nz 专名等)
    # v: 动词 (包含 vn 名动词等)
    # a: 形容词 (表达情绪或状态)
    # i: 成语
    # eng: 英文 (极其重要，大概率是代码、游戏名、技术术语如 C++, ChromaDB)
    valuable_pos_prefixes = ('n', 'v', 'a', 'i', 'eng')

    valuable_word_count = 0

    # 将句子切分为带词性的 Token 列表
    words = pseg.cut(text)

    for word, flag in words:
        # 如果这个词在停用词表里，直接跳过
        if word in STOP_WORDS:
            continue

        # 如果这个词的词性属于我们定义的“有价值词性”
        if flag.startswith(valuable_pos_prefixes):
            valuable_word_count += 1
            # 只要找到至少 2 个有价值的词（或者 1 个特别长的专有名词），就认为这句话值得记
            if valuable_word_count >= 2 or (valuable_word_count == 1 and len(word) >= 2):
                return True

    # 扫描完整个句子，没发现足够的“主干词汇”，判定为低信息熵废话
    print(f"♻️ 触发 NLP 语义拦截：[{text}] 信息熵过低，不存入 ChromaDB。")
    return False


async def get_embedding(text: str) -> list:
    """调用豆包的 Embedding API，把文字变成高维向量"""
    headers = {
        "Authorization": f"Bearer {DOUBAO_API_KEY}",
        "Content-Type": "application/json"
    }

    # 多模态接口要求的特定结构：明确声明这是一个 type="text"
    payload = {
        "model": DOUBAO_EMBEDDING_MODEL,  # 再次提醒：这里千万记得填 ep- 开头的接入点 ID 哦！
        "input": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(DOUBAO_EMBEDDING_URL, headers=headers, json=payload) as resp:
            if resp.status == 200:
                result = await resp.json()
                data_node = result.get("data")

                if isinstance(data_node, list) and len(data_node) > 0:
                    # 如果返回的是标准列表格式
                    return data_node[0].get("embedding", [])
                elif isinstance(data_node, dict):
                    # 如果返回的是多模态 API 特有的字典格式
                    return data_node.get("embedding", [])
                else:
                    print(f"呜...解析向量失败，未知的返回格式：{result}")
                    return []
            else:
                # 把官方的具体报错信息打印出来
                error_body = await resp.text()
                print(f"呜...获取向量失败啦！状态码：{resp.status}，详情：{error_body}")
                return []


async def save_memory_to_chroma(user_id: str, role: str, text: str):
    """把聊天记录存入向量数据库"""
    if not text.strip():
        return

    if not is_worth_remembering(text):
        print(f"♻️ 触发垃圾回收：[{text}] 太水了，艾尔玛拒绝将它存入脑海。")
        return

    vector = await get_embedding(text)
    if not vector:
        return

    # ChromaDB原生是同步的，为了不卡顿，我们把它扔进异步线程池里执行
    def _add_to_chroma():
        collection.add(
            embeddings=[vector],
            documents=[text],
            metadatas=[{"user_id": user_id, "role": role, "timestamp": int(time.time())}],
            ids=[str(uuid.uuid4())]  # 给这条记忆生成一个唯一的 ID
        )

    await asyncio.to_thread(_add_to_chroma)
    print(f"✅ 成功将 {role} 的记忆写入脑海：{text[:15]}...")


async def search_memory_from_chroma(user_id: str, query_text: str, top_k: int = 3) -> str:
    """带有时间衰减重排 (Time-Decay Reranking) 的记忆检索"""
    query_vector = await get_embedding(query_text)
    if not query_vector:
        return ""

    # 1. 超额召回：为了给重排提供足够的素材，我们先从数据库捞出更多的候选记忆
    # 比如最终需要 3 条，我们就先捞 10 条
    fetch_k = max(10, top_k * 3)

    # 【新增修复】获取当前集合中的文档总数，防止 n_results 超限导致崩溃
    collection_size = collection.count()
    if collection_size == 0:
        return ""

    # 取期望召回数与集合实际容量的最小值
    actual_n_results = min(fetch_k, collection_size)

    def _search():
        return collection.query(
            query_embeddings=[query_vector],
            n_results=actual_n_results,  # 【修改】使用动态计算的安全数量
            where={"user_id": user_id},
            # 必须显式要求 ChromaDB 返回 distances 距离数据
            include=["documents", "metadatas", "distances"]
        )

    results = await asyncio.to_thread(_search)

    # 校验是否捞到了数据
    if not results or not results.get('documents') or not results['documents'][0]:
        return ""

    docs = results['documents'][0]
    metas = results['metadatas'][0]
    distances = results['distances'][0]

    now_ts = int(time.time())
    alpha = TIME_DECAY_ALPHA  # 时间惩罚系数 (可根据测试效果微调：0.1 ~ 0.5 之间)

    scored_results = []

    # 2. 核心数学运算：计算时间衰减得分
    for doc, meta, distance in zip(docs, metas, distances):
        # 提取这条记忆存入时的时间戳 (防范早期没存时间戳的脏数据，给个当前时间的保底)
        memory_ts = meta.get('timestamp', now_ts)

        # 计算距离现在经过了多少小时 (如果时间错乱出现负数，用 max 归零)
        delta_hours = max(0, now_ts - memory_ts) / 3600.0

        # 代入公式：最终得分 = 语义距离 + 时间惩罚
        time_penalty = alpha * math.log(1 + delta_hours)
        final_score = distance + time_penalty

        scored_results.append({
            'doc': doc,
            'role': meta.get('role', 'user'),
            'score': final_score,
            'delta_hours': delta_hours  # 保留这个字段方便你调试打印
        })

    # 3. 重新排序：得分越低（距离越小、时间越近），排得越靠前
    scored_results.sort(key=lambda x: x['score'])

    # 4. 截取真正的 Top-K
    final_top_k = scored_results[:top_k]

    # 5. 拼凑回忆文本塞给艾尔玛
    memory_str = ""
    for item in final_top_k:
        # 调试小妙招：计算时间标签
        if item['delta_hours'] < 24:
            time_tag = "刚刚/今天"
        elif item['delta_hours'] < 24 * 7:
            time_tag = "几天前"
        else:
            time_tag = "很久以前"

        # 因为 doc 里面已经包含了“群友说...艾尔玛回答...”，所以我们直接把 doc 塞进去就行
        if item['role'] == "dialogue":
            memory_str += f"- [脑海中浮现出{time_tag}的一段对话]: {item['doc']}\n"
        else:
            # 兼容以前存的单句话老数据
            role_name = "群友" if item['role'] == "user" else "艾尔玛"
            memory_str += f"- [{time_tag}, {role_name} 曾经说过]: {item['doc']}\n"

    return memory_str


async def save_interaction_to_chroma(user_id: str, user_text: str, assistant_text: str):
    """【上下文配对存储】将一问一答打包存入向量库"""
    if not user_text.strip() or not assistant_text.strip():
        return

    # 这里我们复用刚才写好的 Jieba 过滤器
    # 只要群友的问题或者艾尔玛的回答里，有一方是有信息熵的，这段对话就值得记！
    if not is_worth_remembering(user_text) and not is_worth_remembering(assistant_text):
        print("♻️ 触发拦截：这轮对话太水了，双方都在水时长，不存入 ChromaDB。")
        return

    # 将一问一答强行绑定成一个语义块
    context_block = f"群友说：「{user_text}」，艾尔玛回答：「{assistant_text}」"

    vector = await get_embedding(context_block)
    if not vector:
        return

    def _add_to_chroma():
        collection.add(
            embeddings=[vector],
            documents=[context_block],
            # role 统一标记为 dialogue (对话块)
            metadatas=[{"user_id": user_id, "role": "dialogue", "timestamp": int(time.time())}],
            ids=[str(uuid.uuid4())]
        )

    await asyncio.to_thread(_add_to_chroma)
    print(f"✅ 成功写入配对对话块：{context_block[:20]}...")
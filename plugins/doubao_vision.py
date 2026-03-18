import re
import aiohttp
import json
import os
import sys
import asyncio
from nonebot import get_driver
from nonebot.plugin import on_message
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment, GroupMessageEvent, Message
from .profile_manager import auto_extract_and_save_facts
from ._db_manager import update_user_interaction, get_user_profile, save_chat_message, get_recent_chat_messages
from dotenv import load_dotenv
import random
from ._rag_manager import search_memory_from_chroma, save_interaction_to_chroma

# 将根目录加入环境变量，方便导入外部模块
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ._config_loader import config

# 艾尔玛的专属表情包映射表
ELMA_EMOJIS = {
    "鄙视": "bishi.webp",
    "比爱心": "biaixin.webp",
    "暴躁哈气": "baozaohaqi.webp",
    "无语": "wuyv.webp",
    "害羞": "haixiu.webp",
    "晕了": "yunle.webp",
    "不屑": "buxie.webp",
    "嘲笑": "chaoxiao.png",
    "惊讶": "jingya.webp",
    "疑惑": "yihuo.webp",
    "委屈": "weiqv.webp",
    "卖萌": "maimeng.webp",
    "哭了": "kule.webp",
    "开心":"kaixin.png",
    "冷漠":"lengmo.png",
    "看别处的害羞":"kanbiechudehaixiu.png",
    "嫌弃":"xianqi.png",
}

load_dotenv()
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
llm_matcher = on_message(priority=10, block=True)

DOUBAO_API_URL = config['llm']['chat_api_url']
MODEL_NAME = config['llm']['chat_model']
SYSTEM_PROMPT = config['prompts']['system']
EMOJI_PROBABILITY = config['emotion']['base_emoji_prob']
TOP_K = config['memory']['chroma_top_k']
CHAT_HISTORY_LIMIT = config['memory']['chat_history_limit']
EXTRACT_TRIGGER_COUNT=config['memory']['extract_trigger_count']

# 短期记忆工作台
chat_history = {}
message_buffer = {}

@llm_matcher.handle()
async def handle_receive(bot: Bot, event: Event):
    if isinstance(event, GroupMessageEvent) and not event.is_tome():
        await llm_matcher.skip()
    user_id = str(event.get_user_id())
    user_text = event.get_plaintext().strip()

    # 1. 获取发送者的昵称
    sender_name = "未知群友"
    if hasattr(event, 'sender'):
        sender_name = getattr(event.sender, 'card', None) or getattr(event.sender, 'nickname', "未知群友")

    # 2. 更新数据库互动记录
    await update_user_interaction(user_id, sender_name)

    image_urls = [
        seg.data.get("url")
        for seg in event.get_message()
        if seg.type == "image"
    ]

    if not user_text and not image_urls:
        return

    # 3. 分别拉取：结构化记忆(MySQL)和语义化记忆(ChromaDB)
    user_profile = await get_user_profile(user_id)

    flashback_memory = ""
    if user_text:
        try:
            # 【新增修复】加上 try...except 保护，防止 ChromaDB 抛出的异常冒泡
            flashback_memory = await search_memory_from_chroma(user_id, user_text, top_k=TOP_K)
        except Exception as e:
            # 如果记忆库报错，只打印日志，假装没想起任何事，不影响这轮正常的回复
            print(f"⚠️ 艾尔玛提取潜意识记忆失败，呜...头好痛: {e}")
            flashback_memory = ""

    # 4. 独立拼装 Prompt
    dynamic_prompt = SYSTEM_PROMPT

    # 注入设定 1：人物画像
    if user_profile:
        affinity = user_profile.get('affinity', 0)
        facts_str = ", ".join([f"{k}: {v}" for k, v in user_profile.get('facts', {}).items()])

        if affinity < 0:
            attitude = "极度嫌弃，甚至想拉黑"
        elif affinity > 50:
            attitude = "有点好感，语气可以稍微软一点点，但绝不承认"
        else:
            attitude = "普通对待的笨蛋群友"

        dynamic_prompt += f"""
        \n\n【艾尔玛的潜意识记忆】：
        现在和你对话的人是 {sender_name} (QQ号:{user_id})。
        - 你对TA的好感度潜意识：{attitude} (好感值: {affinity})
        - 你记得关于TA的事情：{facts_str if facts_str else "暂时没有任何特别的印象"}
        """

    # 注入设定 2：情景回忆
    if flashback_memory:
        dynamic_prompt += f"""\n【潜意识记忆闪回】：
听到这句话，你的脑海中浮现出你们以前聊过的几句话（请根据这些上下文自然地接话，不要像念稿子）：
{flashback_memory}"""

    # 5. 组装当前会话并维护短期记忆窗口
    current_content = []
    for img_url in image_urls:
        current_content.append({"type": "input_image", "image_url": img_url})

    text_to_send = user_text if user_text else "发送了一张图片，没有说话。"
    current_content.append({"type": "input_text", "text": text_to_send})

    if user_id not in chat_history:
        history = await get_recent_chat_messages(user_id, limit=CHAT_HISTORY_LIMIT)
        # 确保历史记录必须以 user 开头
        while history and history[0].get("role") == "assistant":
            history.pop(0)
        chat_history[user_id] = history

    chat_history[user_id].append({
        "role": "user",
        "content": current_content
    })

    for old_msg in chat_history[user_id][:-1]:
        old_msg["content"] = [c for c in old_msg["content"] if c.get("type") == "input_text"]

    content_for_db = [item for item in current_content if item.get("type") == "input_text"]
    asyncio.create_task(save_chat_message(user_id, "user", content_for_db))

    # 6. 发起请求
    payload = {
        "model": MODEL_NAME,
        "input": [
                     {
                         "role": "system",
                         "content": [{"type": "input_text", "text": dynamic_prompt}]
                     }
                 ] + chat_history[user_id]
    }

    headers = {
        "Authorization": f"Bearer {DOUBAO_API_KEY}",
        "Content-Type": "application/json"
    }

    reply_text = "呜...默认回复，如果看到这句说明出大bug了"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(DOUBAO_API_URL, headers=headers, json=payload) as resp:
                raw_text = await resp.text()

                if resp.status != 200:
                    reply_text = f"呜...请求失败 (状态码 {resp.status})!"
                    chat_history[user_id].pop()
                else:
                    result = json.loads(raw_text)
                    reply_text = ""

                    for item in result.get("output", []):
                        if item.get("type") == "message":
                            for content_block in item.get("content", []):
                                if content_block.get("type") == "output_text":
                                    reply_text += content_block.get("text", "")

                    if not reply_text:
                        reply_text = "艾尔玛发呆了，没返回任何文本~"
                        if chat_history[user_id]:
                            chat_history[user_id].pop()
                    else:
                        # 必须在finish之前把助手的回复塞进记忆里！
                        reply_content = [{"type": "input_text", "text": reply_text}]
                        chat_history[user_id].append({
                            "role": "assistant",
                            "content": reply_content
                        })

                        if len(chat_history[user_id]) > CHAT_HISTORY_LIMIT:
                            chat_history[user_id] = chat_history[user_id][-CHAT_HISTORY_LIMIT:]
                            while chat_history[user_id] and chat_history[user_id][0].get("role") == "assistant":
                                chat_history[user_id].pop(0)
                        asyncio.create_task(save_chat_message(user_id, "assistant", reply_content))

                        # 异步写入长期向量记忆 (不阻塞当前响应)
                        if user_text:
                            # 1. 画像提炼系统（无条件收集群友发言）
                            # 只要群友说话了，就记在小本本上，不管艾尔玛这轮有没有报错！
                            if user_id not in message_buffer:
                                message_buffer[user_id] = []
                            message_buffer[user_id].append(user_text)

                            if len(message_buffer[user_id]) >= EXTRACT_TRIGGER_COUNT:
                                print(f"🔍 触发画像提炼！正在分析 {user_id} 的最近 8 条发言...")
                                chat_log_to_analyze = "\n".join(message_buffer[user_id])
                                current_facts_dict = user_profile.get('facts', {}) if user_profile else {}

                                asyncio.create_task(auto_extract_and_save_facts(
                                    user_id=user_id,
                                    chat_log=chat_log_to_analyze,
                                    current_facts=current_facts_dict
                                ))
                                message_buffer[user_id] = []  # 清空缓存罐

                            # 2. ChromaDB 长期记忆系统（双向校验）
                            # 只有在艾尔玛正常回复的情况下，才进行“上下文配对”存入向量库
                            if "呜..." not in reply_text and "发呆了" not in reply_text:
                                asyncio.create_task(save_interaction_to_chroma(user_id, user_text, reply_text))
                            else:
                                # （可选）降级处理：如果艾尔玛报错了，但这句原话很有价值，
                                # 我们可以只把它单独存进向量库，不带艾尔玛的报错回复。
                                print(f"⚠️ 艾尔玛回复异常，跳过这轮 ChromaDB 上下文配对。")

    except Exception as e:
        reply_text = f"脑袋转不过来了...网络或解析出错啦: {str(e)}"
        if chat_history[user_id]:
            chat_history[user_id].pop()  # 出错也把那条发不出去的话弹掉

    # 使用 finish 将回复发出去并结束事件
    final_message = Message()
    parts = re.split(r'(\[表情:.*?\])', reply_text)

    # 发图锁：这条消息发过表情包了吗？
    has_sent_emoji = False

    for part in parts:
        if not part:
            continue

        match = re.match(r'\[表情:(.*?)\]', part)
        if match:
            emoji_name = match.group(1)

            # 如果还没发过图，且掷骰子通过了50%的概率，才真正发图！
            if not has_sent_emoji and random.random() < EMOJI_PROBABILITY:
                if emoji_name in ELMA_EMOJIS:
                    filename = ELMA_EMOJIS[emoji_name]
                    img_path = os.path.join(os.getcwd(), "emojis", filename)

                    if os.path.exists(img_path):
                        final_message += MessageSegment.image(f"file:///{img_path}")
                        has_sent_emoji = True  # 锁上，这条消息不再发别的表情包了
                    else:
                        print(f"⚠️ 找不到表情包文件: {img_path}")
            # 如果没通过概率判定，或者已经发过图了，这个 [表情:xxx] 标签就会被直接丢弃（不转成文字也不发图）
        else:

            final_message += MessageSegment.text(part)
    await llm_matcher.finish(final_message)

    # await llm_matcher.finish(MessageSegment.text(reply_text))
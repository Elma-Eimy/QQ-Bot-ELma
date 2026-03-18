import aiohttp
import json
import re
from ._db_manager import upsert_user_fact
import os
from dotenv import load_dotenv
from ._config_loader import config

load_dotenv()
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
DOUBAO_API_URL = config['llm']['chat_api_url']
MODEL_NAME = config['llm']['chat_model']

# 告诉大模型：要做的是“更新”和“合并”
EXTRACT_PROMPT = config['prompts']['extract']


async def auto_extract_and_save_facts(user_id: str, chat_log: str, current_facts: dict):
    """后台任务：分析8条打包消息，并结合旧画像自动更新数据库"""

    # 把已有的画像格式化成可读文本
    facts_str = "无"
    if current_facts:
        facts_str = "\n".join([f"- {k}: {v}" for k, v in current_facts.items()])

    user_input = f"""
【用户当前的已有画像】：
{facts_str}

【用户最近的8条聊天记录】：
{chat_log}

请分析并返回更新后的 JSON 事实数组。
"""

    payload = {
        "model": MODEL_NAME,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": EXTRACT_PROMPT}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_input}]}
        ]
    }
    headers = {
        "Authorization": f"Bearer {DOUBAO_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(DOUBAO_API_URL, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    raw_text = await resp.text()
                    result_data = json.loads(raw_text)

                    ai_reply = ""
                    for item in result_data.get("output", []):
                        if item.get("type") == "message":
                            for block in item.get("content", []):
                                if block.get("type") == "output_text":
                                    ai_reply += block.get("text", "")

                    clean_json_str = re.sub(r'```json\n|\n```|```', '', ai_reply).strip()

                    if clean_json_str and clean_json_str != "[]":
                        new_facts = json.loads(clean_json_str)
                        for fact in new_facts:
                            fact_key = fact.get("fact_key")
                            fact_value = fact.get("fact_value")
                            if fact_key and fact_value:
                                await upsert_user_fact(user_id, fact_key, fact_value)
                                print(f"📝 艾尔玛的记忆小本本更新啦：[{fact_key} -> {fact_value}]")
    except Exception as e:
        print(f"提取画像失败了，呜...: {e}")
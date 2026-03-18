import os
import aiohttp
import json
from nonebot.plugin import on_command
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from dotenv import load_dotenv
from nonebot.exception import FinishedException
from ._config_loader import config


# 1. 注册专属命令：只有人发了带有下面这些词的消息，才会触发总结！
summary_matcher = on_command("总结", aliases={"艾尔玛总结", "翻旧账"}, priority=5, block=True)

load_dotenv()

DOUBAO_API_URL = config['llm']['chat_api_url']
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY")
MODEL_NAME = config['llm']['chat_model']

# 专门为总结功能定制的艾尔玛PROMPT
SUMMARY_PROMPT = config['prompts']['summary']

@summary_matcher.handle()
async def handle_summary(bot: Bot, event: GroupMessageEvent):
    # 先发一条消息安抚一下，因为大模型总结可能需要几秒钟
    await summary_matcher.send(MessageSegment.text("哼，等一下，本小姐这就去翻翻你们刚才背着我聊了什么..."))

    try:
        # 2. 调用底层API拉取当前群的历史消息
        history_data = await bot.call_api("get_group_msg_history", group_id=event.group_id)
        messages = history_data.get("messages", [])

        if not messages:
            await summary_matcher.finish(MessageSegment.text("呜...群里空荡荡的，什么都没翻到！"))

        # 3. 提取最近的20条消息，拼接成一段长文本
        recent_msgs = messages[-20:]
        chat_log = ""

        for msg in recent_msgs:
            sender_name = msg.get("sender", {}).get("nickname", "未知群友")
            text_content = msg.get("raw_message", "[非文本消息/图片]")
            chat_log += f"{sender_name}: {text_content}\n"

        # 4. 把拼接好的聊天记录发给豆包
        payload = {
            "model": MODEL_NAME,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": SUMMARY_PROMPT}]},
                {"role": "user",
                 "content": [{"type": "input_text", "text": f"下面是最近的群聊记录，请看一眼：\n{chat_log}"}]}
            ]
        }

        headers = {
            "Authorization": f"Bearer {DOUBAO_API_KEY}",
            "Content-Type": "application/json"
        }

        # 5. 请求大模型
        async with aiohttp.ClientSession() as session:
            async with session.post(DOUBAO_API_URL, headers=headers, json=payload) as resp:
                raw_text = await resp.text()
                if resp.status != 200:
                    await summary_matcher.finish(MessageSegment.text(f"呜...请求失败 (状态码 {resp.status})!"))
                    return

                result = json.loads(raw_text)
                reply_text = ""
                for item in result.get("output", []):
                    if item.get("type") == "message":
                        for content_block in item.get("content", []):
                            if content_block.get("type") == "output_text":
                                reply_text += content_block.get("text", "")

                if not reply_text:
                    reply_text = "看晕了，没总结出来~"

        # 6. 发送最终的总结
        await summary_matcher.finish(MessageSegment.text(reply_text))

    except FinishedException:
        # 遇到 finish 抛出的正常异常，直接向上抛出，让框架去处理
        raise
    except Exception as e:
        # 处理真正的报错
        print(f"总结功能报错: {e}")
        await summary_matcher.finish(MessageSegment.text(f"读取档案失败啦！脑袋转不过来了..."))
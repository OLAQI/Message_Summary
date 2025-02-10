from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Star, Context, register
from astrbot.api.all import EventMessageType, Plain
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from collections import deque
import datetime
import json
import os

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.6", "https://github.com/OLAQI/astrbot_plugin_Message_Summary") # 替换为你自己的信息
class GroupSummaryPlugin(Star):

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self.message_queue = deque(maxlen=200)  # 最大消息队列长度
        self.data_file = os.path.join(os.path.dirname(__file__), "summary_data.json")
        self.load_data()  # 加载历史数据
        self.setup_schedule()  # 设置定时任务
        self.scheduler.start()

    def load_data(self):
         if os.path.exists(self.data_file):
            with open(self.data_file, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    # 兼容旧数据（如果数据是列表）
                    if isinstance(data, list):
                        self.message_queue = deque(data, maxlen=200)
                    elif isinstance(data, dict) and "messages" in data:
                         self.message_queue = deque(data["messages"], maxlen=200)
                except json.JSONDecodeError:
                    self.message_queue = deque(maxlen=200)
         else:
            self.message_queue = deque(maxlen=200)


    def save_data(self):
        with open(self.data_file, "w", encoding="utf-8") as f:
            # 存储消息和计数
            data = {
                "messages": list(self.message_queue),
            }
            json.dump(data, f, ensure_ascii=False, indent=4)


    def setup_schedule(self):
        if self.config.get("summary_time") == "daily":
            try:
                time_str = self.config.get("fixed_send_time", "23:59")
                hour, minute = map(int, time_str.split(":"))
                # 使用 CronTrigger，更可靠
                self.scheduler.add_job(
                    self.send_daily_summary,
                    CronTrigger(hour=hour, minute=minute, timezone="Asia/Shanghai"),  # 显式指定时区
                    id="daily_summary"
                )
            except Exception as e:
                print(f"定时任务设置失败: {e}")

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        # 忽略非目标群聊
        if not event.message_obj.group_id:
            return
        
        self.message_queue.append(
            {
                "time": event.message_obj.timestamp,
                "sender": event.message_obj.sender.nickname,
                "content": event.message_str,
            }
        )

        if (
            len(self.message_queue) >= max(5, self.config.get("message_count", 50))
            and self.config.get("summary_time") == "immediate"
        ):
            await self.send_summary(event)


    @filter.command("${trigger_command}")  # 使用配置中的命令
    async def on_command(self, event: AstrMessageEvent):
        await self.send_summary(event)

    async def send_summary(self, event: AstrMessageEvent):
         if len(self.message_queue) == 0:
            await event.send(Plain("没有足够的消息来生成总结。"))
            return

         messages_to_summarize = list(self.message_queue)
         self.message_queue.clear() # 清空队列
          # 构建消息历史字符串
         history_str = ""
         for msg in messages_to_summarize:
            time_str = datetime.datetime.fromtimestamp(msg["time"]).strftime("%H:%M:%S")
            history_str += f"{time_str} {msg['sender']}: {msg['content']}\\n"

         try:
            provider = self.context.get_using_provider()
            if not provider:
                await event.send(Plain("未配置 LLM 提供商，无法生成总结。"))
                return

            summary_mode = self.config.get("summary_mode", "简介")
            prompt = f"请用{summary_mode}的风格总结以下群聊内容：\n{history_str}"

            response = await provider.text_chat(prompt, session_id=event.session_id)
            summary = response.completion_text

            await event.send([Plain(f"🗣️ 群聊总结 ({summary_mode}风格):\n\n{summary}")])

         except Exception as e:
            await event.send(Plain(f"生成总结时出错：{e}"))
         finally:
            self.save_data()  # 无论成功与否都保存


    async def send_daily_summary(self):
        # 遍历所有群聊（这里假设只有一个，但你可以扩展）
        # 获取所有注册的 session_id (包括群聊和私聊)
        all_session_ids = self.context.get_all_session_ids()

        # 筛选出群聊 session_id
        group_ids = [sid for sid in all_session_ids if "group" in sid]  # 简单的群聊ID过滤
        if not group_ids:
            print("没有活跃的群聊，跳过每日总结。")
            return
        for group_id in group_ids:
            # 使用 send_message 发送消息（更通用）
            await self.context.send_message(group_id, [Plain("🌙 每日群聊总结已生成，正在发送...")])
            # 构造一个虚拟的 AstrMessageEvent
            
            class MockEvent:
                def __init__(self, group_id):
                    self.session_id = group_id
                    self.message_obj=type('obj', (object,), {'group_id': group_id})()

                async def send(self, message_chain):
                    await self.context.send_message(self.session_id, message_chain)

            mock_event = MockEvent(group_id)

            await self.send_summary(mock_event)



    def __del__(self):
        self.save_data()
        if self.scheduler.running:
            self.scheduler.shutdown()

import logging
import json
import os
import datetime
from typing import List

from astrbot.api.star import Context, Star, register
from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.event.filter import command, event_message_type
from astrbot.api.message_components import Plain
from astrbot.api.event.filter import EventMessageType
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# 获取当前模块 logger
logger = logging.getLogger(__name__)

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.0", "https://github.com/OLAQI/Message_Summary/")
class GroupChatSummaryPlugin(Star):

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.message_counts = {}  # 用于存储每个群组的消息计数 {group_id: count}
        # 从配置文件读取配置，如果配置文件不存在，会使用这里的默认值
        self.summary_threshold = config.get("summary_threshold", 50) # 多少条消息触发总结
        self.summary_mode = config.get("summary_mode", "immediate")  # "immediate" 或 "daily"
        self.summary_time = config.get("summary_time", "20:00")  # 每日总结时间，格式为 "HH:MM"

        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()
        
        plugin_dir = os.path.dirname(os.path.abspath(__file__))  # 获取当前文件所在目录
        self.messages_file = os.path.join(plugin_dir, "messages.json")
        if not os.path.exists(self.messages_file):
          with open(self.messages_file, "w", encoding='utf-8') as f:
            f.write("{}")
        with open(self.messages_file, "r", encoding='utf-8') as f:
          self.group_messages = json.load(f) # {group_id: [messages]}
        

        # 如果是每日总结模式，添加定时任务
        if self.summary_mode == "daily":
            self._schedule_daily_summary()

    def _schedule_daily_summary(self):
        """安排每日总结任务"""
        try:
          hour, minute = map(int, self.summary_time.split(':'))
          self.scheduler.add_job(
              self.daily_summary_task,
              'cron',
              hour=hour,
              minute=minute,
              misfire_grace_time=60  # 如果错过触发时间，60秒内仍会执行
          )
        except ValueError:
          logger.error(f"Invalid summary_time format: {self.summary_time}.  Please use HH:MM format.")

    async def daily_summary_task(self):
        """每日总结任务的执行函数"""
        for group_id in list(self.group_messages.keys()):  # 使用 list 避免 RuntimeError
             await self.generate_and_send_summary(group_id,f"这是今天的群聊总结：")


    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> MessageEventResult:
        """监听群聊消息"""
        group_id = event.get_group_id()
        message_str = event.message_str

        # 存储消息
        if group_id not in self.group_messages:
            self.group_messages[group_id] = []
        self.group_messages[group_id].append(f"{event.get_sender_name()}: {message_str}")
        with open(self.messages_file, 'w', encoding='utf-8') as f:
            json.dump(self.group_messages, f, ensure_ascii=False, indent=4)


        # 计数
        if group_id not in self.message_counts:
            self.message_counts[group_id] = 0
        self.message_counts[group_id] += 1

        # 如果达到阈值，并且模式为立即总结，则触发总结
        if self.message_counts[group_id] >= self.summary_threshold and self.summary_mode == "immediate":
            await self.generate_and_send_summary(group_id, f"以下是最近 {self.summary_threshold} 条消息的总结：")
            self.message_counts[group_id] = 0  # 重置计数
            self.group_messages[group_id] = [] # 清空消息记录
            with open(self.messages_file, 'w', encoding='utf-8') as f:
                json.dump(self.group_messages, f, ensure_ascii=False, indent=4)

    @command("总结")
    async def summarize_command(self, event: AstrMessageEvent) -> MessageEventResult:
      """手动触发总结命令"""
      group_id = event.get_group_id()
      if group_id:
        await self.generate_and_send_summary(group_id, "以下是之前的群聊总结：")


    async def generate_and_send_summary(self, group_id: str, prefix_message:str):
        """生成并发送总结"""
        messages = self.group_messages.get(group_id)
        if not messages:
            await self.context.send_message(
                f"group:{group_id}",  # 使用 group:group_id 作为 unified_msg_origin
                MessageChain().plain("当前群组没有消息记录。")
            )
            return

        provider = self.context.get_using_provider()
        if not provider:
            await self.context.send_message(
              f"group:{group_id}",
              MessageChain().plain("未启用 LLM，无法生成总结。")
            )
            return
        try:

          messages_text = "\n".join(messages)
          prompt = f"请总结以下群聊消息：\n\n{messages_text}\n\n总结:"

          response = await provider.text_chat(prompt, session_id=f"group:{group_id}")
          summary_text = response.completion_text
          await self.context.send_message(
              f"group:{group_id}",  # 使用 group:group_id 作为 unified_msg_origin
              MessageChain().plain(prefix_message + summary_text)
          )
          
        except Exception as e:
            await self.context.send_message(
               f"group:{group_id}",
                MessageChain().plain(f"生成总结时发生错误：{e}")
            )


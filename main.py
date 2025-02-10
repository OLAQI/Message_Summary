from astrbot.api.event import AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.event.filter import command, event_message_type, EventMessageType
from astrbot.api.provider import ProviderRequest, LLMResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import json
import os
import datetime
import logging
from typing import List

logger = logging.getLogger("astrbot")

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.1", "https://github.com/OLAQI/Message_Summary/")
class AISummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.message_count = config.get("message_count", 10)
        self.summary_time = config.get("summary_time", "immediate")
        self.trigger_command = config.get("trigger_command", "总结")
        
        # 使用插件目录下的数据文件
        plugin_dir = os.path.dirname(os.path.abspath(__file__))  # 获取当前文件所在目录
        self.data_file = os.path.join(plugin_dir, "chat_data.json")
        
        # 初始化数据存储
        if not os.path.exists(self.data_file):
            with open(self.data_file, "w", encoding='utf-8') as f:
                f.write("{}")
        with open(self.data_file, "r", encoding='utf-8') as f:
            self.chat_data = json.load(f)
        
        # 初始化定时器
        self.scheduler = AsyncIOScheduler()
        self._init_scheduler()

    def _init_scheduler(self):
        if self.summary_time != "immediate":
            try:
                hour, minute = map(int, self.summary_time.split(":"))
                self.scheduler.add_job(
                    self._send_summary,
                    'cron',
                    hour=hour,
                    minute=minute,
                    misfire_grace_time=60
                )
            except ValueError:
                logger.error("Invalid summary time format. Please use HH:MM.")
        self.scheduler.start()

    async def _save_chat_data(self):
        with open(self.data_file, "w", encoding='utf-8') as f:
            json.dump(self.chat_data, f, ensure_ascii=False)

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> MessageEventResult:
        session_id = event.unified_msg_origin
        if session_id not in self.chat_data:
            self.chat_data[session_id] = []
        
        message_str = event.message_str
        self.chat_data[session_id].append(message_str)
        
        if len(self.chat_data[session_id]) >= self.message_count or self.trigger_command in message_str:
            await self._send_summary(session_id)
            self.chat_data[session_id] = []  # 清空已处理的消息
        
        await self._save_chat_data()

    async def _send_summary(self, session_id: str = None):
        if session_id is None:
            for session_id, messages in self.chat_data.items():
                if len(messages) >= self.message_count:
                    await self._generate_and_send_summary(session_id, messages)
                    self.chat_data[session_id] = []  # 清空已处理的消息
        else:
            messages = self.chat_data.get(session_id, [])
            if len(messages) >= self.message_count:
                await self._generate_and_send_summary(session_id, messages)
                self.chat_data[session_id] = []  # 清空已处理的消息

    async def _generate_and_send_summary(self, session_id: str, messages: List[str]):
        provider = self.context.get_using_provider()
        if provider:
            try:
                prompt = f"请对以下聊天记录进行总结：\n{''.join(messages)}"
                response = await provider.text_chat(prompt, session_id=session_id)
                summary = response.completion_text
                await self.context.send_message(session_id, summary)
            except Exception as e:
                logger.error(f"生成总结时出错: {e}")
                await self.context.send_message(session_id, "生成总结时出错，请稍后再试。")
        else:
            await self.context.send_message(session_id, "LLM 未启用，请联系管理员。")

    @command("summary_help")
    async def summary_help(self, event: AstrMessageEvent):
        help_text = """总结插件使用帮助：
1. 自动总结：
   - 当群聊中达到配置的消息条数时，会自动发送总结。
   - 可以通过设置配置项 `message_count` 来调整触发总结的消息条数。

2. 定时总结：
   - 可以设置每天固定时间发送总结，配置项为 `summary_time`，格式为 HH:MM。
   - 如果设置为 `immediate`，则立即发送总结。

3. 触发命令：
   - 监听到配置的命令词时，会立即触发发送总结，配置项为 `trigger_command`。

4. 帮助信息：
   - /summary_help - 显示此帮助信息。
"""
        yield event.plain_result(help_text)

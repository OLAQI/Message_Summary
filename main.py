import logging
from typing import Union
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.event.filter import event_message_type, EventMessageType, command
from astrbot.api.provider import ProviderRequest
import datetime
import json
import os

# 获取当前模块 logger
logger = logging.getLogger(__name__)


@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.1", "https://github.com/OLAQI/astrbot_plugin_Message_Summary")
class GroupSummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.message_count = config.get("message_count", 50)  # 默认50条消息[^1]
        self.summary_time = config.get("summary_time", "immediate")  # 默认立即总结[^1]
        self.fixed_send_time = config.get(
            "fixed_send_time", "23:59"
        )  # 默认23:59[^1]
        self.trigger_command = config.get("trigger_command", "/summary")  # 默认 /summary[^1]
        self.message_counts = {}  # 用于跟踪每个群组的消息计数
        
         # 使用插件目录下的数据文件
        plugin_dir = os.path.dirname(os.path.abspath(__file__))  # 获取当前文件所在目录
        self.data_file = os.path.join(plugin_dir, "summary_data.json")
        
        # 初始化数据存储
        if not os.path.exists(self.data_file):
            with open(self.data_file, "w", encoding='utf-8') as f:
                f.write("{}")
        with open(self.data_file, "r", encoding='utf-8') as f:
            self.summary_data = json.load(f)

        # 启动时检查是否有需要发送的每日总结
        now = datetime.datetime.now()
        if self.summary_time == "daily":
            fixed_time = datetime.datetime.strptime(self.fixed_send_time, "%H:%M").time()
            if now.time() >= fixed_time:
                self.check_daily_summary()

    @event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        group_id = event.group_id # 获取群ID[^5]
        if group_id not in self.message_counts:
            self.message_counts[group_id] = 0
        self.message_counts[group_id] += 1

        if self.message_counts[group_id] >= self.message_count:
            await self.send_summary(event)
            self.message_counts[group_id] = 0  # 重置计数器

        if self.trigger_command in event.message_str:  # 使用 event.message_str 获取消息文本[^5]
            await self.send_summary(event)

    async def send_summary(self, event: AstrMessageEvent):
        """发送总结"""
        group_id = event.group_id
        if self.summary_time == "immediate" or self.trigger_command in event.message_str:
            # 立即发送总结
            messages = await self.get_recent_messages(event, self.message_count)
            summary = await self.generate_summary(messages)
            if summary:
                yield event.plain_result(summary)

    async def check_daily_summary(self):
        """检查是否需要发送每日总结"""
        now = datetime.datetime.now()
        fixed_time = datetime.datetime.strptime(self.fixed_send_time, "%H:%M").time()
        if now.time() >= fixed_time:
            for group_id in self.message_counts:
                 # 检查上一次总结时间
                last_summary_date = self.summary_data.get(group_id, {}).get("last_summary_date", None)
                
                if last_summary_date:
                    last_summary_date = datetime.datetime.strptime(last_summary_date, "%Y-%m-%d").date()
                
                today = now.date()
                if not last_summary_date or last_summary_date < today:
                    # 获取昨天的消息
                    messages = await self.get_daily_messages(group_id)
                    summary = await self.generate_summary(messages)
                    if summary:
                        # 发送消息到群组，因为无法获取原始事件对象，需要创建 Context 对象来发送消息
                        await self.context.send_message(group_id, summary)
                    
                    # 更新数据文件
                    if group_id not in self.summary_data:
                        self.summary_data[group_id] = {}
                    self.summary_data[group_id]["last_summary_date"] = today.strftime("%Y-%m-%d")
                    await self._save_data()

    async def get_recent_messages(self, event: AstrMessageEvent, count: int) -> str:
        """获取最近的消息"""
        messages = []
        message_chain = event.context.get_event_queue().get_events()
        
        # 倒序遍历消息链，获取最近的消息
        for msg_event in reversed(message_chain):
            if len(messages) >= count:
                break
            if msg_event.group_id == event.group_id:
                messages.append(f"{msg_event.get_sender_nickname()}: {msg_event.message_str}")
        
        return "\n".join(reversed(messages))  # 将消息列表反转，恢复正常顺序

    async def get_daily_messages(self, group_id: str) -> str:
        """获取昨天的群聊消息"""
        messages = []
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        start_time = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        message_chain = self.context.get_event_queue().get_events()

        # 遍历消息链，获取昨天的消息
        for msg_event in message_chain:
            msg_time = datetime.datetime.fromtimestamp(msg_event.timestamp)
            if msg_event.group_id == group_id and start_time <= msg_time <= end_time:
                messages.append(f"{msg_event.get_sender_nickname()}: {msg_event.message_str}")

        return "\n".join(messages)

    async def generate_summary(self, messages: str) -> str:
        """生成总结"""
        if not messages:
            return "没有足够的消息来生成总结。"

        provider = self.context.get_using_provider()  # 获取 LLM 提供商[^3]
        if provider:
            try:
                prompt = f"请总结以下群聊消息：\n{messages}\n请用简洁的语言概括主要内容。"
                response = await provider.text_chat(
                    prompt=prompt, session_id="group_summary"
                )  # 使用 text_chat 方法调用 LLM[^3]
                return response.completion_text
            except Exception as e:
                return f"生成总结失败: {e}"
        else:
            return "LLM 未启用，请联系管理员。"
            
    async def _save_data(self):
        '''保存数据'''
        with open(self.data_file, "w", encoding='utf-8') as f:
            json.dump(self.summary_data, f, ensure_ascii=False)

    @command("summary_help")  # 注册指令[^5]
    async def summary_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """群聊总结插件使用帮助：
- 达到设定消息条数时自动总结并发送。
- 每天固定时间自动发送总结。
- 输入 `/summary` 手动触发总结。

配置选项：
- `message_count`: 触发总结的消息条数（默认50条）。
- `summary_time`: 总结信息发送时间（立即 或 每天固定时间）。
- `fixed_send_time`: 每天固定发送时间（格式：HH:MM，默认23:59）。
- `trigger_command`: 触发总结的命令词（默认 `/summary`）。
"""
        yield event.plain_result(help_text) # 发送帮助信息[^3]


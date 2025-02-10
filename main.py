from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api.message_components import Plain
from typing import List, Dict, Any
import asyncio
import logging

# 设置日志记录
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.8", "https://github.com/OLAQI/astrbot_plugin_message_summary")
class MessageSummaryPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.config = config
        self.message_history: Dict[str, List[str]] = {}

        # 确保配置中存在 'summary_time'，如果不存在，设置默认值
        if 'summary_time' not in self.config:
            self.config['summary_time'] = 'immediate'  # 或 'daily'，根据你的需求
        # 确保配置中存在'fixed_send_time'，如果不存在，设置默认值。
        if 'fixed_send_time' not in self.config:
            self.config['fixed_send_time'] = "23:59"
        # 每天 23:59 定时执行 send_daily_summary
        if self.config.get('summary_time') == 'daily':
            self.context.add_schedule(self.send_daily_summary, time=self.config.get("fixed_send_time"))

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def log_message(self, event: AstrMessageEvent):
        """消息存储处理"""
        msg = event.message_obj
        if not msg.group_id:
            return

        group_id = msg.group_id
        if group_id not in self.message_history:
            self.message_history[group_id] = []

        # 仅存储消息的纯文本内容
        self.message_history[group_id].append(event.message_str)

        message_count = self.config.get("message_count", 50)
        if len(self.message_history[group_id]) >= message_count:
            await self.send_summary(event)
            self.message_history[group_id] = []

    async def send_summary(self, event: AstrMessageEvent):
        """发送总结"""
        group_id = event.message_obj.group_id
        if group_id not in self.message_history:
            return

        messages = self.message_history[group_id]
        if not messages:
            return

        # 构建 prompt
        prompt = "以下是群聊消息记录：\n" + "\n".join(messages) + "\n请总结以上内容："

        provider = self.context.get_using_provider()
        if provider:
            # 选择总结风格
            summary_mode = self.config.get("summary_mode", "简洁")
            if summary_mode == "严谨":
                prompt += "以严谨的风格总结"
            elif summary_mode == "幽默":
                prompt += "以幽默的风格总结"
            else:
                prompt += "以简洁的风格总结"

            try:
                response = await provider.text_chat(
                    prompt,
                    session_id=event.session_id,
                )
                summary_text = response.completion_text

                # 发送总结
                await event.send([Plain(f"📝 群聊总结：\n{summary_text}")])
                # 清空消息历史
                self.message_history[group_id] = []
            except Exception as e:
                logger.error(f"生成总结时发生错误: {e}")
                await event.send([Plain("❌ 生成总结时发生错误，请稍后再试。")])
        else:
            await event.send([Plain("❌ 未配置大语言模型，无法生成总结。")])

    @filter.command("summary")
    async def trigger_summary(self, event: AstrMessageEvent):
        """手动触发总结"""
        trigger_command = self.config.get("trigger_command", "/summary")
        if event.message_str.strip() == trigger_command:
            await self.send_summary(event)

    async def send_daily_summary(self):
        # 遍历所有群聊（这里假设只有一个，但你可以扩展）
        # 获取所有注册的 session_id (包括群聊和私聊)
        all_session_ids = self.context.get_all_session_ids()

        # 筛选出群聊 session_id
        group_ids = [sid for sid in all_session_ids if "group" in sid]  # 简单的群聊ID过滤
        if not group_ids:
            logger.info("没有活跃的群聊，跳过每日总结。")
            return

        for group_id in group_ids:
            # 使用 send_message 发送消息（更通用）
            await self.context.send_message(group_id, [Plain("🌙 每日群聊总结已生成，正在发送...")])

            # 构造一个虚拟的 AstrMessageEvent
            class MockEvent:
                def __init__(self, group_id):
                    self.session_id = group_id
                    self.message_obj = type('obj', (object,), {'group_id': group_id})()  # 添加group_id

                async def send(self, message_chain):
                    await self.context.send_message(self.session_id, message_chain)

            mock_event = MockEvent(group_id)
            await self.log_message(mock_event)  # 调用log_message
            await self.send_summary(mock_event)

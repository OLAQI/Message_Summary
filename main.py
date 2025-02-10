from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.event.filter import EventMessageType  # 添加这一行
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import ProviderRequest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from typing import List, Dict, Any
import requests

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.1.0", "https://github.com/OLAQI/astrbot_plugin_message_summary")
class MessageSummaryPlugin(Star):
    def __init__(self, context: Context, config: Dict[str, Any]):
        super().__init__(context)
        self.config = config
        self.message_history: Dict[str, List[str]] = {}
        self.scheduler = AsyncIOScheduler()
        self.scheduler.start()

        # 确保配置中存在 'summary_time' 和 'fixed_send_time'，如果不存在，设置默认值
        if 'summary_time' not in self.config:
            self.config['summary_time'] = 'immediate'
        if 'fixed_send_time' not in self.config:
            self.config['fixed_send_time'] = "23:59"

        if self.config.get('summary_time') == 'daily':
            self.scheduler.add_job(self.send_daily_summary, 'cron', hour=int(self.config["fixed_send_time"].split(":")[0]), minute=int(self.config["fixed_send_time"].split(":")[1]))

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)  # 现在 EventMessageType 已经定义
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

        summary_mode = self.config.get("summary_mode", "简介")
        if summary_mode == "严谨":
            prompt += "以严谨的风格总结"
        elif summary_mode == "幽默":
            prompt += "以幽默的风格总结"
        else:
            prompt += "以简介的风格总结"

        provider = self.context.get_using_provider()
        if provider:
            response = await provider.text_chat(
                prompt,
                session_id=event.session_id,
            )
            summary_text = response.completion_text

            # 获取天气信息
            weather_info = await self.get_weather(self.config.get("weather_location", "北京"))
            summary_text += f"\n当前地区天气：{weather_info}"

            # 发送总结
            await event.send([Plain(f"📝 群聊总结：\n{summary_text}")])
            # 清空消息历史
            self.message_history[group_id] = []
        else:
            await event.send([Plain("❌ 未配置大语言模型，无法生成总结。")])

    @filter.command("summary")
    async def trigger_summary(self, event: AstrMessageEvent):
        """手动触发总结"""
        trigger_command = self.config.get("trigger_command", "/summary")
        if event.message_str.strip() == trigger_command:
            await self.send_summary(event)

    async def send_daily_summary(self):
        all_session_ids = self.context.get_all_session_ids()
        group_ids = [sid for sid in all_session_ids if "group" in sid]

        if not group_ids:
            print("没有活跃的群聊，跳过每日总结。")
            return

        for group_id in group_ids:
            class MockEvent:
                def __init__(self, group_id):
                    self.session_id = group_id
                    self.message_obj = type('obj', (object,), {'group_id': group_id})()

                async def send(self, message_chain):
                    await self.context.send_message(self.session_id, message_chain)

            mock_event = MockEvent(group_id)
            await self.log_message(mock_event)
            await self.send_summary(mock_event)

    async def get_weather(self, location: str) -> str:
        """获取天气信息"""
        api_key = self.config.get("amap_api_key", "")
        if not api_key:
            return "未配置高德天气API，请在管理面板中设置。"

        url = f"https://restapi.amap.com/v3/weather/weatherInfo?city={location}&key={api_key}"
        response = requests.get(url)
        data = response.json()
        if data["status"] == "1":
            weather = data["lives"][0]["weather"]
            temperature = data["lives"][0]["temperature"]
            return f"{weather}，温度：{temperature}℃"
        else:
            return "无法获取天气信息，请检查配置。"

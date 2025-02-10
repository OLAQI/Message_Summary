from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.event.filter import EventMessageType  # 正确的导入路径 [^5]
from astrbot.api.star import Context, Star, register
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import datetime
import json
import os
from typing import Dict, List

message_store: Dict[str, Dict] = {}  # 存储结构: {group_id: {count, messages}}

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.1", "https://github.com/OLAQI/astrbot_plugin_Message_Summary")
class GroupSummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        
        # 初始化数据存储路径 [^6]
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_file = os.path.join(plugin_dir, "message_store.json") 
        self._load_message_store()
        
        # 配置定时任务 [^6]
        if self.config['summary_mode'] == 'daily':
            self._setup_daily_schedule()
        self.scheduler.start()

    def _load_message_store(self):
        """加载历史消息数据"""
        global message_store
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                message_store = json.load(f)

    def _save_message_store(self):
        """保存消息数据 采用增量保存模式 [^6]"""
        with open(self.data_file, 'w') as f:
            json.dump(message_store, f, indent=2)

    def _setup_daily_schedule(self):
        """配置每日定时任务 [^6]"""
        hour, minute = map(int, self.config['fixed_send_time'].split(':'))
        self.scheduler.add_job(
            self._daily_summary_task,
            'cron',
            hour=hour,
            minute=minute,
            misfire_grace_time=60
        )

    async def _daily_summary_task(self):
        """每日定时总结逻辑"""
        for group_id in list(message_store.keys()):
            if message_store[group_id]['count'] > 0:
                await self._generate_summary(group_id, is_daily=True)
                message_store[group_id] = {'count': 0, 'messages': []}
        self._save_message_store()

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)  # 正确的事件类型引用 [^5]
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息并计数"""
        group_id = event.message_obj.group_id
        text = event.message_str.strip()
        
        if group_id not in message_store:
            message_store[group_id] = {
                'count': 0,
                'messages': []
            }
        
        # 维护消息存储 [^6]
        message_store[group_id]['count'] += 1
        message_store[group_id]['messages'].append(text[:100])  # 截断过长的消息
        
        # 自动触发检查 [^4]
        if (self.config['summary_mode'] == 'immediate' and 
            message_store[group_id]['count'] >= self.config['message_count']):
            await self._generate_summary(group_id)
            message_store[group_id] = {'count': 0, 'messages': []}
            self._save_message_store()

    async def _generate_summary(self, group_id: str, is_daily: bool = False):
        """调用LLM生成总结的核心逻辑 [^6]"""
        provider = self.context.get_using_provider()
        if not provider:
            return
        
        history = "\n".join(message_store[group_id]['messages'][-self.config['message_count']:])
        prompt = f"请对{'24小时内' if is_daily else '最近'}的群聊记录生成摘要（{self.config['summary_style']}）：\n{history}"
        
        try:
            response = await provider.text_chat(
                prompt=prompt,
                session_id=group_id
            )
            summary = f"【{'每日' if is_daily else '实时'}群聊总结】\n{response.completion_text}"
            await self.context.send_message(group_id, summary)
        except Exception as e:
            await self.context.send_message(group_id, f"生成总结失败：{str(e)}")

    @filter.command("${trigger_command}")  # 动态命令配置 [^4]
    async def manual_trigger(self, event: AstrMessageEvent):
        """手动触发总结"""
        group_id = event.message_obj.group_id
        if message_store.get(group_id, {}).get('count', 0) > 0:
            await self._generate_summary(group_id)
            message_store[group_id] = {'count': 0, 'messages': []}
            self._save_message_store()
            yield event.plain_result("已生成实时总结")
        else:
            yield event.plain_result("当前没有需要总结的消息")

    @filter.command("summary_help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = f"""群聊总结插件使用说明：

触发方式：
1. 自动触发 - {self.config['message_count']}条新消息后自动总结
2. 定时总结 - {'每天' + self.config['fixed_send_time'] if self.config['summary_mode'] == 'daily' else '未启用'}
3. 手动触发 - 发送 {self.config['trigger_command']}
总结风格：{self.config['summary_style']}
"""
        yield event.plain_result(help_text)

    def __del__(self):
        """插件卸载时保存数据"""
        self._save_message_store()

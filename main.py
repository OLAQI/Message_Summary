from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api.provider import LLMResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import datetime
import json
import os
from typing import Dict, List

# 历史消息存储结构：{group_id: {'count': int, 'messages': List[str]}}
message_store: Dict[str, Dict] = {}

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.1", "https://github.com/OLAQI/Message_Summary")
class GroupSummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        
        # 初始化数据文件路径
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.data_file = os.path.join(plugin_dir, "message_store.json")
        self._load_message_store()
        
        # 初始化定时任务
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
        """保存历史消息数据"""
        with open(self.data_file, 'w') as f:
            json.dump(message_store, f)

    def _setup_daily_schedule(self):
        """设置每天定时任务"""
        hour, minute = map(int, self.config['fixed_send_time'].split(':'))
        self.scheduler.add_job(
            self._daily_summary_task,
            'cron',
            hour=hour,
            minute=minute,
            misfire_grace_time=60
        )

    async def _daily_summary_task(self):
        """每日定时总结任务"""
        for group_id in list(message_store.keys()):
            await self._generate_summary(group_id, is_daily=True)
            message_store[group_id] = {'count': 0, 'messages': []}
        self._save_message_store()

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """群消息监听器"""
        group_id = event.message_obj.group_id
        text = event.message_str.strip()
        
        # 初始化群组存储
        if group_id not in message_store:
            message_store[group_id] = {'count': 0, 'messages': []}
        
        # 存储消息
        message_store[group_id]['count'] += 1
        message_store[group_id]['messages'].append(text)
        
        # 自动触发检查
        if (self.config['summary_mode'] == 'immediate' and 
            message_store[group_id]['count'] >= self.config['message_count']):
            await self._generate_summary(group_id)
            message_store[group_id] = {'count': 0, 'messages': []}
            self._save_message_store()

    async def _generate_summary(self, group_id: str, is_daily: bool = False):
        """调用大模型生成总结
        
        Args:
            group_id: 群组ID
            is_daily: 是否为每日总结模式
        """
        provider = self.context.get_using_provider()
        if not provider:
            return
        
        # 拼接历史消息
        history = "\n".join(message_store[group_id]['messages'][-self.config['message_count']:])
        prompt = f"请对以下群聊记录生成总结{'（每日总结）' if is_daily else ''}：\n{history}"
        
        # 调用LLM
        response: LLMResponse = await provider.text_chat(
            prompt=prompt,
            session_id=group_id
        )
        
        # 发送总结消息
        summary = f"【群聊总结】\n{response.completion_text}"
        await self.context.send_message(group_id, summary)

    @filter.command("${trigger_command}")
    async def manual_trigger(self, event: AstrMessageEvent):
        """手动触发总结命令"""
        group_id = event.message_obj.group_id
        if group_id in message_store and message_store[group_id]['count'] > 0:
            await self._generate_summary(group_id)
            message_store[group_id] = {'count': 0, 'messages': []}
            self._save_message_store()
            yield event.plain_result("已生成实时总结")
        else:
            yield event.plain_result("暂无新消息可总结")

    @filter.command("summary_help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = f"""群聊总结插件使用说明：
        
1. 自动触发：当群消息达到{self.config['message_count']}条时自动总结
2. 手动触发：发送 {self.config['trigger_command']} 立即生成总结
3. 定时模式：{'每天' + self.config['fixed_send_time'] + '发送总结' if self.config['summary_mode'] == 'daily' else '未启用定时模式'}
        
配置管理请使用AstrBot管理面板"""
        yield event.plain_result(help_text)

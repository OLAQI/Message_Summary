from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.event.filter import EventMessageType
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import datetime
import json
import os
import collections

message_store = collections.defaultdict(
    lambda: {
        'count': 0,
        'messages': collections.deque(maxlen=500)  
    }
)

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.3", "https://github.com/OLAQI/astrbot_plugin_Message_Summary")
class GroupSummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        
        # 初始化数据存储路径 
        self.data_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 
            "message_store.json"
        )
        self._load_store()
        
        # 配置定时任务
        if self.config['mode'] == 'daily':
            self._setup_schedule()
        self.scheduler.start()

    def _load_store(self):
        """加载历史消息数据"""
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                data = json.load(f)
                for gid, v in data.items():
                    message_store[gid]['messages'] = collections.deque(
                        v['messages'], maxlen=500
                    )
                    message_store[gid]['count'] = v['count']

    def _save_store(self):
      
        data = {
            gid: {
                'count': v['count'],
                'messages': list(v['messages'])
            } for gid, v in message_store.items()
        }
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _setup_schedule(self):
        """定时任务配置"""
        hour, minute = map(int, self.config['time'].split(':'))
        self.scheduler.add_job(
            self._daily_task,
            'cron',
            hour=hour,
            minute=minute
        )

    async def _daily_task(self):
        """每日定时任务"""
        for gid in list(message_store.keys()):
            await self._process_summary(gid, is_daily=True)
        self._save_store()

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_message(self, event: AstrMessageEvent):
        """消息监听"""
        msg = event.message_obj
        gid = msg.group_id
        
        message_store[gid]['count'] += 1
        message_store[gid]['messages'].append({
            "time": msg.timestamp,
            "text": event.message_str[:100],
            "sender": msg.sender.nickname
        })
        
        # 自动触发检查
        if (self.config['mode'] == 'auto' and 
            message_store[gid]['count'] >= self.config['threshold']):
            await self._process_summary(gid)

    async def _process_summary(self, group_id: str, is_daily=False):
        """总结处理核心"""
        data = message_store[group_id]
        if data['count'] < 5:
            
            index = max(-3, -len(data['messages']))
            recent = data['messages'][index] if data['messages'] else None
            
            if recent:
                time_str = datetime.datetime.fromtimestamp(
                    recent['time']/1000
                ).strftime('%m-%d %H:%M')
                reply = f"当前仅{data['count']}条消息，最新发言({time_str}):\n【{recent['sender']}】{recent['text']}"
                await self.context.send_message(group_id, reply)
            return
        
        # 生成正式总结
        provider = self.context.get_using_provider()
        history = "\n".join([
            f"【{m['sender']}】{m['text']}" 
            for m in list(data['messages'])[-self.config['threshold']:]
        ])
        
        try:
            resp = await provider.text_chat(
                prompt=f"生成{self.config['style']}风格的群聊摘要：\n{history}",
                session_id=group_id
            )
            await self.context.send_message(
                group_id, 
                f"【{'每日' if is_daily else '实时'}总结】\n{resp.completion_text}"
            )
            # 重置计数
            message_store[group_id]['count'] = 0
            self._save_store()
        except Exception as e:
            await self.context.send_message(group_id, f"总结生成失败：{str(e)}")

    @filter.command("${command}")
    async def trigger(self, event: AstrMessageEvent):
        """手动触发命令"""
        await self._process_summary(event.message_obj.group_id)
        yield event.plain_result("总结请求已处理")

    def __del__(self):
        """插件卸载时保存数据"""
        self._save_store()

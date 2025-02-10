from astrbot.api.all import (
    AstrMessageEvent, 
    EventMessageType,
    Star,
    Context,
    filter,
    register
)
from astrbot.api.message_components import Plain
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

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.4", "https://github.com/OLAQI/astrbot_plugin_Message_Summary")
class GroupSummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self.data_file = os.path.join(os.path.dirname(__file__), "message_store.json")
        self._load_store()

        if self.config['mode'] == 'daily':
            self._setup_schedule()
        self.scheduler.start()

    def _load_store(self):
        if os.path.exists(self.data_file):
            with open(self.data_file, 'r') as f:
                data = json.load(f)
                for gid, v in data.items():
                    message_store[gid]['messages'] = collections.deque(v['messages'], maxlen=500)
                    message_store[gid]['count'] = v['count']

    def _save_store(self):
        data = {gid: {'count': v['count'], 'messages': list(v['messages'])} 
               for gid, v in message_store.items()}
        with open(self.data_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _setup_schedule(self):
        hour, minute = map(int, self.config['time'].split(':'))
        self.scheduler.add_job(self._daily_task, 'cron', hour=hour, minute=minute)

    async def _daily_task(self):
        for gid in list(message_store.keys()):
            await self._process_summary(gid, is_daily=True)
        self._save_store()

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)  # [^5]
    async def on_message(self, event: AstrMessageEvent):        # [^2]
        msg = event.message_obj
        gid = msg.group_id
        
        message_store[gid]['count'] += 1
        message_store[gid]['messages'].append({
            "time": datetime.datetime.now().isoformat(),
            "text": event.message_str[:200],
            "sender": msg.sender.nickname
        })
        
        if self.config['mode'] == 'auto' and message_store[gid]['count'] >= self.config['threshold']:
            await self._process_summary(gid)
            self._save_store()

    async def _process_summary(self, group_id: str, is_daily=False):
        data = message_store[group_id]
        if data['count'] < 5:
            return
        
        try:
            provider = self.context.get_using_provider()
            history = "\n".join(
                [f"{m['sender']}: {m['text']}" 
                for m in list(data['messages'])[-self.config['threshold']:]]
            )
            
            resp = await provider.text_chat(
                prompt=f"生成{self.config['style']}风格的群聊摘要：\n{history}",
                session_id=group_id
            )
            
            summary_type = "每日" if is_daily else "实时"
            await self.context.send_message(
                group_id,
                [Plain(f"【{summary_type}群聊总结】\n"),
                 Plain(resp.completion_text)]
            )
            message_store[group_id]['count'] = 0
            
        except Exception as e:
            await self.context.send_message(group_id, f"总结生成失败：{str(e)}")

    @filter.command("${command}")  # [^6]
    async def trigger_summary(self, event: AstrMessageEvent):
        await self._process_summary(event.message_obj.group_id)
        yield event.message("总结请求已处理")

    def __del__(self):
        self._save_store()

from astrbot.api.event import filter
from astrbot.api.star import Star, Context, register
from astrbot.api.all import AstrMessageEvent, EventMessageType, Plain
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from collections import defaultdict, deque
import datetime
import json
import os

message_store = defaultdict(
    lambda: {
        "count": 0,
        "messages": deque(maxlen=1000),
        "last_summary": None
    }
)

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.5", "https://github.com/OLAQI/astrbot_plugin_Message_Summary")
class GroupAISummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self.data_file = os.path.join(
            os.path.dirname(__file__), 
            "summary_data.json"
        )
        
        if self.config["mode"] == "daily":
            self._setup_schedule()
        
        self._load_store()
        self.scheduler.start()

    def _setup_schedule(self):
        hour, minute = map(int, self.config["time"].split(":"))
        self.scheduler.add_job(
            self._daily_summary,
            "cron",
            hour=hour,
            minute=minute
        )

    @filter.command("${command}")
    async def manual_trigger(self, event: AstrMessageEvent):
        """手动触发总结"""
        group_id = event.message_obj.group_id
        await self._process_summary(group_id, is_manual=True)
        yield event.plain_result("[总结请求已排队处理]")

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def log_message(self, event: AstrMessageEvent):
        """消息存储处理"""
        msg = event.message_obj
        if not msg.group_id:
            return
        
        store = message_store[msg.group_id]
        store["count"] += 1
        store["messages"].append({
            "time": datetime.datetime.now().isoformat(),
            "content": event.message_str[:200],
            "sender": msg.sender.nickname
        })

        if self.config["mode"] == "auto" and store["count"] >= self.config["threshold"]:
            await self._process_summary(msg.group_id)

    async def _process_summary(self, group_id: str, is_manual=False):
        """核心处理逻辑"""
        store = message_store[group_id]
        try:
            provider = self.context.get_using_provider()  # [^4]
            if not provider:
                raise ValueError("未找到可用的语言模型")
            
            history = "\n".join(
                f"[{m['sender']}]: {m['content']}"
                for m in list(store["messages"])[-self.config["threshold"]:]
            )
            
            prompt = f"请用{self.config['style']}风格总结以下群聊内容:\n{history}"
            resp = await provider.text_chat(
                prompt=prompt,
                session_id=group_id
            )
            
            summary_type = "手动" if is_manual else "自动"
            await self.context.send_message(
                group_id,
                [
                    Plain(f"【AI群聊总结-{summary_type}模式】\n"),
                    Plain(resp.completion_text),
                    Plain("\n\n💡小结基于大模型生成，仅供参考")
                ]
            )
            store["count"] = 0
            store["last_summary"] = datetime.datetime.now().isoformat()
        except Exception as e:
            await self.context.send_message(
                group_id,
                f"⚠️总结生成失败: {str(e)}"
            )
        finally:
            self._save_store()

    def _daily_summary(self):
        """每日定时任务"""
        for gid in list(message_store.keys()):
            self._process_summary(gid)

    def _load_store(self):
        """加载历史数据"""
        if os.path.exists(self.data_file):
            with open(self.data_file, "r") as f:
                data = json.load(f)
                for gid, v in data.items():
                    message_store[gid].update(v)

    def _save_store(self):
        """保存数据"""
        data = {gid: dict(v) for gid, v in message_store.items()}
        with open(self.data_file, "w") as f:
            json.dump(data, f, indent=2)

    def __del__(self):
        self._save_store()

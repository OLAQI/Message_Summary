from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.event.filter import EventMessageType
from astrbot.api.star import Context, Star, register
from typing import Union

@register("Message_Summary", "OLAQI", "群聊消息总结插件", "1.0.1", "https://github.com/OLAQI/Message_Summary/")
class MessageSummaryPlugin(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config
        self.message_buffer = {}


    @filter.command("summary")
    async def summary_command(self, event: AstrMessageEvent):
        '''总结群聊消息'''
        await self.handle_summary(event)

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE, priority=-1) # 修正此行
    async def on_message(self, event: AstrMessageEvent):

        if event.message_type != EventMessageType.GROUP_MESSAGE:  #这行不需要了
            return

        group_id = event.group_id
        if group_id not in self.message_buffer:
            self.message_buffer[group_id] = []

        self.message_buffer[group_id].append(event.raw_message)

        if self.config.get("summary_mode", "immediate") == "immediate":
            if len(self.message_buffer[group_id]) >= self.config.get("summary_threshold", 50):
                await self.handle_summary(event)
                return

        trigger_phrase = self.config.get("trigger_phrase", "阿辉总结")
        if trigger_phrase in event.message_str:
            await self.handle_summary(event)


    async def handle_summary(self, event: AstrMessageEvent):
        group_id = event.group_id
        if group_id not in self.message_buffer:
            self.message_buffer[group_id] = []

        messages = self.message_buffer[group_id]
        if not messages:
            yield event.plain_result("没有需要总结的消息。")
            return

        # 构造消息链, 提取消息内容 (这里需要根据你的raw_message结构调整)
        message_texts = []
        for msg in messages:

            if isinstance(msg, dict) :
                if msg.get('message'):
                    for m in msg.get('message'):
                        if m.get('type') == 'Plain':
                            message_texts.append( m.get('text'))
            elif isinstance(msg, list):
                 for m in msg:
                        if m.get('type') == 'Plain':
                            message_texts.append( m.get('text'))


        # 调用 LLM 进行总结 (这里只是一个示例，你需要根据你的 LLM provider 实现)
        provider = self.context.get_using_provider()
        if provider:
             prompt = "请总结以下聊天记录：\n" + "\n".join(message_texts)
             response = await provider.text_chat(prompt,session_id=event.session_id)
             summary_text = response.completion_text
             yield event.plain_result(summary_text)



        else:
            yield event.plain_result("未配置大语言模型，无法进行总结。")

        # 清空消息缓冲区
        self.message_buffer[group_id] = []


    @filter.on_cron_timer("summary_timer", rule="0 0 20 * * *")  # 默认的 20:00, 会被用户配置覆盖
    async def daily_summary_timer(self):
        # 获取所有群组ID
        group_ids = self.message_buffer.keys()

        for group_id in group_ids:
            # 构造一个假的event
            class MockEvent:
                def __init__(self, group_id):
                    self.group_id = group_id
                    self.session_id = f"group_{group_id}"  # 构造session_id

                def plain_result(self, text):
                    return text

            mock_event = MockEvent(group_id)

            if self.config.get("summary_mode", "immediate") == "daily":
                await self.handle_summary(mock_event)


import asyncio
import logging

from . import plugins
from biliup.config import config
from biliup.engine import Plugin, invert_dict
from biliup.engine.event import EventManager
from .common.timer import Timer
logger = logging.getLogger('biliup')

def create_event_manager():
    pool1_size = config.get('pool1_size', 3)
    pool2_size = config.get('pool2_size', 3)
    # 初始化事件管理器
    app = EventManager(config, pool1_size=pool1_size, pool2_size=pool2_size)
    app.context['url_upload_count'] = {}
    # 正在上传的文件 用于同时上传一个url的时候过滤掉正在上传的
    app.context['upload_filename'] = []
    return app

event_manager = create_event_manager()
context = event_manager.context


async def shot(event):
    from biliup.engine.event import Event
    from biliup.handler import CHECK
    index = 0
    while True:
        if not len(event.url_list):
            logger.info(f"{event}没有任务，退出")
            return
        if index >= len(event.url_list):
            index = 0
            continue
        cur = event.url_list[index]
        event_manager.send_event(Event(CHECK, (event, context['PluginInfo'].inverted_index[cur], cur)))
        index += 1
        await asyncio.sleep(30)


@event_manager.server()
class PluginInfo:
    def __init__(self, streamers):
        streamer_url = {k: v['url'] for k, v in streamers.items()}
        self.inverted_index = invert_dict(streamer_url)
        urls = list(self.inverted_index.keys())
        self.checker = Plugin(plugins).sorted_checker(urls)
        self.url_status = dict.fromkeys(self.inverted_index, 0)
        self.coroutines = dict.fromkeys(self.checker)
        self.init_tasks()

    def add(self, name, url):
        temp = Plugin(plugins).inspect_checker(url)
        key = temp.__name__
        if key in self.checker:
            self.checker[key].url_list.append(url)
        else:
            temp.url_list = [url]
            self.checker[key] = temp
            from .plugins.twitch import Twitch
            if temp == Twitch:
                # 如果支持批量检测，目前只有一个支持，第一版先写死按照特例处理
                self.batch_check_task()
            else:
                self.coroutines[key] = asyncio.create_task(shot(temp))
        self.inverted_index[url] = name
        self.url_status[url] = 0

    def delete(self, url):
        if not url in self.inverted_index:
            return
        del self.inverted_index[url]
        exec_del = False
        for key, value in self.checker.items():
            if url in value.url_list:
                if len(value.url_list) == 1:
                    exec_del = key
                else:
                    value.url_list.remove(url)
        if exec_del:
            del self.checker[exec_del]
            self.coroutines[exec_del].cancel()
            del self.coroutines[exec_del]

    def init_tasks(self):
        from .engine.download import DownloadBase
        from .plugins.twitch import Twitch

        for key, plugin in self.checker.items():
            if plugin == Twitch:
                # 如果支持批量检测，目前只有一个支持，第一版先写死按照特例处理
                self.batch_check_task()
                continue
            self.coroutines[key] = asyncio.create_task(shot(plugin))

    def batch_check_task(self):
        from biliup.engine.event import Event
        from biliup.handler import CHECK
        from .plugins.twitch import Twitch

        async def check_timer():
            event_manager.send_event(Event(CHECK, (Twitch, None, None)))
        timer = Timer(func=check_timer, interval=30)
        self.coroutines[Twitch.__name__] = asyncio.create_task(timer.astart())
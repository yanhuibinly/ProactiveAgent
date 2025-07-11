'''
This file provide three distinct components:
2. Trigger: How we will actually execute the command.
3. Agent: Get the observation from the environment and generate the action.
'''
import os
import json
import asyncio
import logging
import threading
import subprocess
from typing import Iterable, Literal, Optional, Dict, List

import colorlog
from codelinker import CodeLinker, CodeLinkerConfig, EventProcessor, EventSink
from codelinker.models import SEvent, ChannelTag


from channels import sc
from agentmodule import ActionListener, Executor
from prompt import SYSTEM_PROMPT
from constant import AgentResponse

from register import ToolRegister
toolreg = ToolRegister()

# Set the logger format.
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)

formatter = colorlog.ColoredFormatter(
    fmt='%(log_color)s%(levelname)s - %(name)s - %(message)s',
                            log_colors={
                                'DEBUG':    'white',
                                'INFO':     'green',
                                'WARNING':  'yellow',
                                'ERROR':    'red',
                                'CRITICAL': 'red,bg_white',
                            })
# formatter = logging.Formatter('%(levelname)s - %(name)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# load information.
default_cfg_file = os.path.join(os.path.dirname(__file__), '..', 'private.toml')
if not os.path.exists(default_cfg_file):
    default_cfg_file = os.path.join(os.path.dirname(__file__), 'private.toml')

CL_CFGFILE = os.getenv(key = 'CODELINKER_CFG',
                    default = default_cfg_file)

codelinker_config = CodeLinkerConfig.from_toml(CL_CFGFILE)
codelinker_config.request.default_completions_model = "activeagent"
codelinker_config.request.use_cache = False
codelinker_config.request.save_completions = True

clinker = CodeLinker(config = codelinker_config)
eventSink = EventSink(sinkChannels=sc,logger=logger)

class BasicComponent(EventProcessor):
    def __init__(self,name:str):
        super().__init__(name = name,
                        sink = eventSink)
        self.listen(sc.setup)(self.setup)
        self.cl = clinker

    def gather(self,
            tags: ChannelTag | Iterable[ChannelTag] | None = None,
            return_dumper:Literal['identity','json'] = 'identity') -> str | Iterable[dict]:
        messages = super().gather(tags = tags,return_dumper = 'identity')
        match return_dumper:
            case 'identity':
                return messages
            case 'json':
                for msg in messages:
                    o = msg['content']
                    if isinstance(o,SEvent):
                        msg['content'] = json.dumps({
                            "Time": o.time,
                            "Source": o.source,
                            "Tags": o.tags,
                            "Event": o.content
                        },ensure_ascii=False)
                return messages
            case __:
                raise ValueError(f"return_dumper should be 'identity' or 'json', but got {return_dumper}")


class DemoEnv(BasicComponent):
    def __init__(self, *,
                interval_seconds:int = 15,
                watched_path:List[str] = [],
                name:str = 'DemoEnv',
                ):
        """
        Args:
            interval_seconds (int, optional): The pause time between two interactions. Defaults to 15 [seconds].
            name (str, optional): the name of the environment. Defaults to 'DemoEnv'.
        """
        super().__init__(name)
        self.interval_seconds = interval_seconds

        self.action_listener = ActionListener(
            interval_seconds = interval_seconds,
            watched_path=watched_path)

        self.executor = Executor()

        complete_tools = toolreg.get_all_tools_dict()
        self.tools = [t for t in complete_tools if 'android' not in t["name"]]
        self.logger.debug(f"tools: {self.tools}")

    async def setup(self):
        self.logger.info("Initializing Demo Environment...")

        def start_local_server():
            try:
                subprocess.run(['python', 'main.py'])
            except:
                subprocess.run(['python3', 'main.py'])

        # We set up the uvicorn in another thread, so we don't have to open to terminal.
        self.thread = threading.Thread(target = start_local_server, daemon=True)
        self.thread.start()
        self.logger.info("Local server established.")

        self.add(sc.agent.operations, content = json.dumps(self.tools), silent = True)
        self.listen(sc.demo.notify)(self.execute)
        self.action_listener.start()
        read_task = asyncio.create_task(self.read_data())
        self.logger.info("Demo Environment Initialized. Action Listener running...")

        await asyncio.gather(read_task)

    async def read_data(self):

        await asyncio.sleep(self.interval_seconds)

        while True:
            data:Dict = self.action_listener.send_data()
            async with self.get_tag_lock(sc.activity):
                self.add(sc.observation, content = json.dumps(data,ensure_ascii=False))
            await asyncio.sleep(self.interval_seconds)

    async def execute(self):
        operation:str = self.get(sc.agent.execute).content

        if operation == 'nop':
            return

        current_event:str = self.get(sc.observation).content
        proposal:str = self.get(sc.agent.propose).content
        proposal_json:Dict = json.loads(proposal)

        exec_args = {"events": current_event, "func_call": operation}
        self.executor.receive(proposal_json, exec_args)
        self.executor.send()

class DemoAgent(BasicComponent):
    def __init__(self,*,
                name:str = "ActiveAgent"):
        """
        Args:
            name (str, optional): The name of the agent. Defaults to "ActiveAgent".
        """
        super().__init__(name)

    @property
    def memory(self):
        return [{"role": "system", "content": SYSTEM_PROMPT}]

    async def setup(self):
        logger.info("Initializing Agent...")
        self.listen(sc.observation)(self.propose)
        logger.info("Agent setup done.")

    async def propose(self):

        if self.get_tag_lock(sc.agent.propose).locked():
            logger.error("Another agent is proposing.")
            return

        async with self.get_tag_lock(sc.agent.propose):
            async with self.get_tag_lock(sc.activity):

                ops_event:SEvent = self.get(sc.agent.operations)
                ops:str = ops_event.content

                obs:Dict = self.gather([sc.observation],return_dumper='json')

                history = obs

                # TODO: Can we add user feedback ?

                user_content:str = json.dumps({
                    "Instructions": "Now analyze the history events and provide a task if you think the user needs your help using the given format. If the user is in an email application and there are no mails, you could first refresh the mail by swipe down using `swipe` tool.",
                    "operations": ops
                })

                logger.debug('Start Proposing....')

                res: AgentResponse = await self.cl.exec(
                    prompt = user_content,
                    return_type = AgentResponse,
                    messages = self.memory + history,
                )

                self.logger.info(res)
                self.add(sc.agent.propose, content = res.model_dump_json())

                if res.Operation is not None and res.Operation != 'null':
                    self.add(sc.agent.execute, res.Operation)
                else:
                    self.add(sc.agent.execute, "nop")

class Trigger(BasicComponent):
    def __init__(self,*,
                name:str = "Trigger",
                ):
        """
        Args:
            name (str, optional): The name of the agent. Defaults to "Trigger".
        """
        super().__init__(name)

    async def setup(self):
        logger.info("Initializing Trigger...")
        self.listen(sc.agent.execute)(self.execute)
        logger.info("Trigger setup done.")

    async def execute(self):

        operation:str = self.get(sc.agent.execute).content

        self.add(sc.demo.notify, content = operation)

import os
import json
import logging
import asyncio
import requests
from typing import List, Dict
from datetime import datetime, timezone
from abc import ABC, abstractmethod

logger = logging.getLogger('ActiveAgent')

Dialogue = List[Dict[str, str]]
sem = asyncio.Semaphore(16)

from watchdog.observers import Observer
import tenacity
from codelinker import CodeLinker, CodeLinkerConfig
# Load the codelinker.
default_cfg_file = os.path.join(os.path.dirname(__file__), '..', 'private.toml')
if not os.path.exists(default_cfg_file):
    default_cfg_file = os.path.join(os.path.dirname(__file__), 'private.toml')

CL_CFGFILE = os.getenv(key = 'CODELINKER_CFG',
                    default = default_cfg_file)

logger.info(f'Using config file: {CL_CFGFILE}')

if not os.path.exists(CL_CFGFILE):
    raise FileNotFoundError("No Config File Found. Please first set your configuration file by either through environment variable CODELINKER_CFG or refer to readme.")
codelinker_config = CodeLinkerConfig.from_toml(CL_CFGFILE)
codelinker_config.request.default_completions_model = "activeagent"
codelinker_config.request.use_cache = False
codelinker_config.request.save_completions = False

cl = CodeLinker(config = codelinker_config)
model_name = 'activeagent'
from prompt import SYSTEM_PROMPT


class AgentCore(object):
    def __init__(self,
                cl: CodeLinker,
                model_name:str):

        self.cl        : CodeLinker          = cl
        self.model_name: str                 = model_name
        self.contexts  : List[Dict[str,str]] = []

    def add_new_event(self, event:str):
        new_turn = {
            'event'        : event,
            'response'     : None,
            'user_feedback': None
        }
        self.contexts.append(new_turn)

    def update_response(self, response:str):
        if len(self.contexts) == 0:
            raise Exception('No event has been added.')

        if self.contexts[-1]['response'] is not None:
            raise Exception('The last event has already been updated.')
        self.contexts[-1]['response'] = response

    def update_feedback(self, feedback:str):
        if len(self.contexts) == 0:
            raise Exception('No event has been added.')

        if self.contexts[-1]['user_feedback'] is not None:
            raise Exception('The last event has already been updated.')
        self.contexts[-1]['user_feedback'] = feedback

    async def reflect(self, operations:List[Dict], screen_shot:List[bytes] = None, remain_content:int = -1) -> str:

        print('start reflecting')
        # Format the contexts into a dialogue.
        messages:Dialogue = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        # Add the histories.
        for idx in range(len(self.contexts) - 1):
            if idx > 0:
                # Add additional user feedback.
                user_content = {
                    "Observation": self.contexts[idx]["event"],
                    "user_feedback": self.contexts[idx - 1]["user_feedback"]
                }
            else:
                user_content = {
                    "Observation": self.contexts[idx]["event"],
                }

            messages.append({"role": "user", "content": json.dumps(user_content)})
            messages.append({"role": "assistant", "content": self.contexts[idx]["response"]})

        user_content = {
            "Observation": self.contexts[-1]["event"],
            "Instructions": "Now analyze the history events and provide a task if you think the user needs your help using the given format.",
            "Operations": operations
        }
        if len(self.contexts) >= 2:
            user_content["user_feedback"] = self.contexts[-2]["user_feedback"]

        messages.append(
            {"role": "user",
            "content": json.dumps(user_content)
            })

        # remove the previous user input.
        if remain_content > 0:
            for index in range(len(messages) - 1, -1, -1):
                if messages[index]['role'] == 'assistant':
                    continue
                if remain_content > 0:
                    remain_content -= 1
                    continue
                messages[index]['content'] = 'The user is interacting with the android.'
            pass


        with open('reflect.json','a',encoding='utf-8') as f:
            f.write(json.dumps(messages, ensure_ascii=False, indent=4,separators=(',', ':')) + '\n')

        async for attemp in tenacity.AsyncRetrying(stop=tenacity.stop_after_attempt(5),reraise=True):
            with attemp:
                async with sem:
                    res = await self.cl.exec(
                        model = self.model_name,
                        messages = messages,
                        completions_kwargs={"temperature":0.0 if attemp.retry_state.attempt_number < 1 else 0.5}
                        )

        return res

    async def generate_response(self, prompt:str) -> str:
        # For some reasons I have to reserve this.
        async with sem:
            res = await self.cl.exec(
                model              = self.model_name,
                return_type        = str,
                messages           = [{"role" : "user", "content": prompt}],
                completions_kwargs = {"temperature": 0.0})
        return res

    async def summary_context(self) -> None:
        # TODO: Add a summary to shorten the contexts for long time use.
        pass
        # Collect the user's preference.
        # concat the event observations.
        # Let the agent summarize the context. Adding it in the new turn.

def read_text_from_file(filepath:str, pages:int = 3) -> str:
    full_path = filepath
    import time
    time.sleep(1)

    # if not os.path.isfile(full_path):
    #     raise FileNotFoundError(f"File {filepath} not found in workspace.")
    # if not os.path.exists(full_path):
    #     raise FileNotFoundError(f"File {filepath} not found in workspace.")


    if filepath.endswith(".pdf"):
        from PyPDF2 import PdfReader

        reader = PdfReader(full_path)
        content = ''
        for page in reader.pages[:pages]:
            content += page.extract_text()
        return content

    # if filepath.endswith(".pdf"):
    #     import fitz
    #     doc = fitz.open(filepath)
    #     content = ''
    #     for page_num in range(min(pages, len(doc))):
    #         page = doc.load_page(page_num)
    #         text = page.get_text()
    #         content += text
    #     return content

    if filepath.endswith(".docx"):
        import docx
        doc = docx.Document(full_path)
        content = ''
        for para in doc.paragraphs[:pages]:
            content += para.text
        return content

    if filepath.endswith((".txt",".md")):
        with open(full_path, 'r') as f:
            content = f.read()
        return content

class Trigger(ABC):
    """
    A Trigger will be able to receive the content from the agent and pass it to the user.
    There are two main methods:
    - receive(): receive the content from the agent and store it.
    - send(): send the content to the user in a proper way.
    """

    @abstractmethod
    def receive(self, *args, **kwargs) -> None:
        """
        receive the content from the agent and store it.
        Args:
            infos (Any): information needed in any possible form.
        """
        pass

    @abstractmethod
    def send(self):
        """
        The action to send something from the agent to the user.
        """
        pass


class ActionListener(object):
    def __init__(self,
                interval_seconds: int = 10,
                watched_path:List[str] = []
                ):

        # data storages:
        # Contain the raw events. These events will be filtered and recorded in event_data
        self.raw_events      :List[Dict] = []
        self.event_data      :List[Dict] = []
        # Record the whole string that user typed.(alphabet only now)
        self.text_content    :str = ""
        self.interval_seconds:int        = interval_seconds
        # Record the time period.
        self.last_post_time:datetime = None
        self.observer = Observer()


    def __exit__(self):
        self.keyboard_listener.stop()
        self.mouse_listener.stop()
        self.observer.stop()

    def reset_data(self):
        """
        reset all the stored data.
        """
        self.event_data.clear()
        self.text_content = ""

    def send_data(self) -> dict:
        """
        Returns:
            Dict: a event dict containing:
            {
                "timestamp": (float),
                "duration": (int),
                "user_input": (str),
                "hot-keys": List[dict],
                "status": Literal ['afk'/'not-afk'],
                "app": (str),
                "info": None/Dict
            },
        """
        current_time = datetime.now(timezone.utc)
        start_time = self.last_post_time

        # Other apps are now not supported and being ignored.
        result_event = {
            "timestamp": start_time.timestamp(),
            "duration": self.interval_seconds,
            "user_input": self.text_content,
            "hot-keys": list(filter(lambda x:"hot_key" in x["data"].keys(), self.event_data)), # add those hot keys.
            "status": None,
            "apps": None,
            "info": None
        }

        print(result_event)

        self.last_post_time = current_time
        self.reset_data()

        # info_str = json.dumps(,ensure_ascii=False)
        return result_event

    def push_event(self, event:Dict):
        """
        Push an filtered event into the event_data bucket.
        Args:
            event (Dict): The filtered event.
        """
        self.event_data.append(event)

    def start(self):
        """
        Start the listener.
        Note the timezone of our data is UTC.
        """
        self.observer.start()
        self.last_post_time = datetime.now(timezone.utc)

class Executor(Trigger):
    '''
    This compoment will execute some actions based on the agent's result.
    '''
    def __init__(self):

        config = codelinker_config.get_apiconfig_by_model('activeagent')

        self.model = config.model
        self.api_key = config.api_key
        try:
            self.base_url = config.base_url
        except:
            self.base_url = None

    def receive(self, response:Dict, exec_args:Dict):

        self.response = response
        self.exec_args = exec_args

    def send(self):
        # The original response from the agent.
        response = self.response
        # The args including the event and the tool call format.
        action_labels = self.exec_args

 
        def activated_callback():
            print('>' * 80)
            global status
            status = 'The user accepts the last proposal from you.'
            infos = self.exec_args
            func_call_str = infos['func_call']
            # parsing the arguments into function name and parameters
            func_infos = func_call_str.split('&')
            # Get the function name.
            func_name = func_infos[0]
            # Get the parameters as a dictionary.
            func_params = {k:v for k,v in (param.split('=') for param in func_infos[1:])}
            # For the chat (completion) function, add the api_key and base_url.

            logger.debug(f'Function name {func_name}; Function origin params {func_params}.')

            match func_name:
                case 'search':
                    response = requests.get(f'http://127.0.0.1:8000/{func_name}',params=func_params)
                    response = response.json()
                # For chat we will update the api config and the backgrounds to the params.
                case 'chat':
                    func_params.update({
                        'api_key' : self.api_key,
                        'base_url': self.base_url,
                        'messages': json.dumps(infos["events"])})

                    response = requests.get(f'http://127.0.0.1:8000/{func_name}',params=func_params)
                    response = response.json()
                # For read, we simply pass it.
                case 'read':
                    response = requests.get(f'http://127.0.0.1:8000/{func_name}',params=func_params)
                    response = response.json()
                    # TODO: Need a update.
                    if response['status'] == 'success':
                        prompt = \
"""You are a helpful assistant, currently you are dealing with contents in a file.
Here is the background {target}.
Here is the content of the file: {content}
Please accomplish the proposal raised by the agent.""".format(target = infos, content = response['content'])

                        new_params = {'api_key':self.api_key, 'base_url':self.base_url, 'messages':prompt}
                        __ = requests.get('http://127.0.0.1:8000/chat',params = new_params)

        # Propose the candidates to choose.
        print('What wrong for proposal?')

'''
The main agent file.
'''
import os
from time import sleep
import asyncio
import logging
from typing import Literal
import fire
from components import DemoAgent, DemoEnv, Trigger, eventSink, logger
from channels import sc

# Get rid of other logging information.
logging.getLogger("httpx").setLevel(logging.CRITICAL)
logging.getLogger("httpcore").setLevel(logging.CRITICAL)
logging.getLogger("filelock").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("openai").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)


async def main(interval: int = 15,
                port    : int = 5600):

    CONFIG_INFO = \
f'''
Socket Configuration:
- Activity port: {port}.
- Assistance Interval: {interval} seconds.
- Reading buckets from:
'''
    logger.info(CONFIG_INFO)

    agent = DemoAgent(name = 'Demo Agent')
    env = DemoEnv(
                interval_seconds = interval,
                watched_path=[os.path.abspath('.')])
    trigger = Trigger()
    eventSink.init()

    eventSink.add(tags = sc.setup, content = 'Set up.')
    await eventSink.wait(sc.setup)
    logger.info("*** Components setup completed. ***")

    while True:
        await asyncio.sleep(1)


if __name__ == "__main__":
    fire.Fire(main)


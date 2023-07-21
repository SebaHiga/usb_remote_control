import time
import asyncio

class SessionHandler:
    def __init__(self, context):
        self.context = context

    async def reset_session(self):
        self.context.session_time = time.time()

    async def run(self):
        while True:
            delta = time.time() - self.context.session_time
            print(f'delta time is {delta}')
            continue
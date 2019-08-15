import logging
from typing import Optional

from smppy.app import Application
from smppy.client import Client

logging.basicConfig(level=logging.DEBUG)


class MySmppApp(Application):
    async def on_connection_attempt(self,
                                    client: Client,
                                    password: str) -> Optional[Client]:
        return client

    async def on_sms_received(self,
                              client: Client,
                              source: str,
                              dest: str,
                              sms: str) -> None:
        self.logger.debug(f'Received {sms} from {source}...')


app = MySmppApp()

app.run_app(port=10000)

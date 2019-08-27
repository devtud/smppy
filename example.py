import asyncio
import logging
from typing import Union, List

from smppy.server import SmppClient, Application

logging.basicConfig(level=logging.DEBUG)


class MySmppApp(Application):
    def __init__(self, name: str, logger):
        self.clients: List[SmppClient] = []
        super(MySmppApp, self).__init__(name=name, logger=logger)

    async def handle_bound_client(self, client: SmppClient) -> Union[SmppClient, None]:
        self.clients.append(client)
        return client

    async def handle_unbound_client(self, client: SmppClient):
        self.clients.remove(client)

    async def handle_sms_received(self, client: SmppClient, source_number: str,
                                  dest_number: str, text: str):
        self.logger.debug(f'Received {text} from {source_number}')


loop = asyncio.get_event_loop()

app = MySmppApp(name='smpp-server', logger=logging.getLogger('ess'))

app.run(loop=loop)

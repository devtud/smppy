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
        self.logger.debug(f'Client {client.system_id} connected.')
        return client

    async def handle_unbound_client(self, client: SmppClient):
        self.clients.remove(client)

    async def handle_sms_received(self, client: SmppClient, source_number: str,
                                  dest_number: str, text: str):
        self.logger.debug(f'Received {text} from {source_number}')
        await client.send_sms(source=dest_number, dest=source_number,
                              text=f'You have sent {text} to me...')


loop = asyncio.get_event_loop()

app = MySmppApp(name='smppy', logger=logging.getLogger('smppy'))

app.run(loop=loop, host='localhost', port=2775)

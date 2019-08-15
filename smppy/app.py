import asyncio
import logging
from typing import Optional, List, ClassVar

from smppy.client import Client
from smppy.server import SmppProtocol


class Application:
    _clients: ClassVar[List[Client]] = []

    def __init__(self, logger=None):
        if not logger:
            logger = logging.getLogger('smppy')
        self.logger = logger

    async def on_connection_attempt(self,
                                    client: Client,
                                    password: str) -> Optional[Client]:
        """
        Called when a SMPP client tries to connect to server (the client sends a
        bind-transceiver request).

        It should be implemented when client's authentication is needed

        Args:
            client: the Client object
            password: client's password

        Returns:
            the client instance if all good, otherwise None - in which case the client
            won't be accepted and the connection will be closed

        """
        return client

    async def on_sms_received(self,
                              client: Client,
                              source: str,
                              dest: str,
                              sms: str) -> None:
        """
        Called when a client sends an SMS to server (submit-sm request).

        Args:
            client: the Client object
            source: the sender's address
            dest: the recipient's address
            sms: the text of the SMS

        Returns: None

        """
        pass

    def _get_client(self, smpp_protocol: SmppProtocol) -> Optional[Client]:
        for c in self._clients:
            if c.protocol == smpp_protocol:
                return c
        return None

    def _remove_client(self, client: Client) -> None:
        self._clients.remove(client)

    async def _new_client_cb(self,
                             smpp_protocol: SmppProtocol,
                             system_id: str,
                             password: str) -> None:
        client = Client(system_id=system_id, protocol=smpp_protocol)
        client = await self.on_connection_attempt(client, password)

        if not client:
            raise Exception('Auth error')

        self._clients.append(client)

    async def _unbound_client_cb(self, smpp_protocol: SmppProtocol):
        client = self._get_client(smpp_protocol=smpp_protocol)
        self._remove_client(client=client)

    async def _sms_received_cb(self,
                               smpp_protocol: SmppProtocol,
                               source: str,
                               dest: str,
                               sms: str):
        client = self._get_client(smpp_protocol)
        if not client:
            raise Exception(f'Client does not exist for protocol {smpp_protocol}')

        return await self.on_sms_received(
            client=client, source=source, dest=dest, sms=sms
        )

    def create_protocol(self) -> SmppProtocol:
        return SmppProtocol(new_client_cb=self._new_client_cb,
                            unbound_client_cb=self._unbound_client_cb,
                            sms_received_cb=self._sms_received_cb)

    def run_app(self, host: str = 'localhost', port: int = 2775):
        loop = asyncio.get_event_loop()

        factory = loop.create_server(self.create_protocol, *(host, port))

        server = loop.run_until_complete(factory)

        self.logger.info(f'Starting server on {host}:{port} ...')

        try:
            loop.run_forever()
        finally:
            self.logger.info('closing server...')
            server.close()
            loop.run_until_complete(server.wait_closed())
            self.logger.info('closing event loop')
            loop.close()

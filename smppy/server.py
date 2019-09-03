from __future__ import annotations

import abc
import asyncio
import io
import logging
from typing import Optional, List
from typing import Union

from smpp.pdu.operations import (
    BindTransceiver, SubmitSM, EnquireLink, Unbind, DeliverSM, DeliverSMResp,
    BindTransceiverResp, UnbindResp
)
from smpp.pdu.pdu_encoding import PDUEncoder
from smpp.pdu.pdu_types import (
    CommandId, PDUResponse, PDU, AddrTon, AddrNpi, EsmClassMode, EsmClass, EsmClassType,
    RegisteredDelivery, PriorityFlag, RegisteredDeliveryReceipt, ReplaceIfPresentFlag,
    DataCodingDefault, DataCoding, PDURequest, MoreMessagesToSend, CommandStatus
)
from smpp.pdu.sm_encoding import SMStringEncoder

__all__ = ['SmppClient', 'Application']


class Application(abc.ABC):
    def __init__(self, name: str, logger=None):
        self.name = name

        if not logger:
            logger = logging.getLogger('smpp-app')
        self.logger = logger

    @abc.abstractmethod
    async def handle_bound_client(self, client: SmppClient) -> Union[SmppClient, None]:
        """
        Here is the right place for authenticating the client. If this method does not
        return the (same) client instance, the client will be disconnected

        Args:
            client: a newly connected client which sent a bind_transceiver request

        Returns:
            the received `Client` instance to accept the client, or `None` to refuse it

        """

    @abc.abstractmethod
    async def handle_unbound_client(self, client: SmppClient):
        """
        Handle an unbound client.

        Args:
            client: the smpp client who sent the unbound command

        Returns:
            the returned value of this handler method is ignored

        """

    @abc.abstractmethod
    async def handle_sms_received(self, client: SmppClient, source_number: str,
                                  dest_number: str, text: str):
        """
        This is triggered when a submit_sm is received from the smpp client.

        Args:
            client: the smpp client from which the sms has been received
            source_number: the phone number of the sender
            dest_number: the phone number of the recipient
            text: the content of the sms

        Returns:
            the returned value of this handler method is ignored

        """

    def create_server(self,
                      loop: asyncio.AbstractEventLoop = None,
                      host='0.0.0.0',
                      port=2775):
        factory = loop.create_server(lambda: SmppProtocol(app=self), host=host, port=port)
        server = loop.run_until_complete(factory)
        return server

    def run(self, loop: asyncio.AbstractEventLoop = None, host='0.0.0.0', port=2775):
        if loop is None:
            loop = asyncio.get_event_loop()

        server = self.create_server(loop=loop, host=host, port=port)

        self.logger.info(f'Starting server on {host}:{port} ...')

        try:
            loop.run_forever()
        finally:
            self.logger.info('closing server...')
            server.close()
            loop.run_until_complete(server.wait_closed())
            self.logger.info('closing event loop')
            loop.close()


class SmppClient:
    def __init__(self,
                 protocol: SmppProtocol,
                 system_id: str,
                 password: str,
                 system_type: str,
                 interface_version: int,
                 addr_ton: AddrTon,
                 addr_npi: AddrNpi):
        self._protocol: SmppProtocol = protocol
        self.system_id: str = system_id
        self.password = password
        self.system_type = system_type
        self.interface_version = interface_version
        self.addr_ton = addr_ton
        self.addr_npi = addr_npi

    async def send_sms(self, source: str, dest: str, text: str) -> None:
        await self._protocol.send_deliver_sm(
            source_addr=source,
            destination_addr=dest,
            text=text,
            source_addr_npi=self.addr_npi,
            dest_addr_npi=self.addr_npi,
            source_addr_ton=self.addr_ton,
            dest_addr_ton=self.addr_ton,
        )


class SmppProtocol(asyncio.Protocol, abc.ABC):

    def __init__(self, app: Application):
        self.app = app
        # self.logger = logger
        self._client: Optional[SmppClient] = None

        # there is a 1:1 relationship between transport and protocol
        self._transport: Optional[asyncio.Transport] = None

        # Whether bind_transceiver has been done
        self.is_bound = False

        # Used as reference number for concatenated deliver_sms. Do not use it directly,
        # but with `self.next_ref_num()`
        self._ref_num: int = 0

        self._sequence_number = 0

    def next_ref_num(self):
        self._ref_num += 1
        return self._ref_num

    def next_sequence_number(self):
        self._sequence_number += 1
        return self._sequence_number

    def connection_made(self, transport: asyncio.Transport) -> None:
        self._transport = transport
        self._ref_num = 0

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            self.app.logger.error(f'ERROR: {exc}')
        else:
            self.app.logger.warning('Closing connection')
        self._transport = None
        self.is_bound = False
        super(SmppProtocol, self).connection_lost(exc)

    def _send_PDU(self, pdu: PDU):
        self._transport.write(PDUEncoder().encode(pdu))

    def _send_requests(self, requests: List[PDURequest], merge=True):
        if not self.is_bound:
            raise Exception('Cannot send request to unbound client')

        encoder = PDUEncoder()

        if merge:
            buffer_list = [encoder.encode(pdu) for pdu in requests]
            self._transport.write(b''.join(buffer_list))
        else:
            for pdu in requests:
                self._transport.write(encoder.encode(pdu))

    def _send_response(self, response: PDUResponse):
        self._send_PDU(response)

    async def send_deliver_sm(self,
                              source_addr: str,
                              destination_addr: str,
                              text: str,
                              source_addr_npi: AddrNpi,
                              dest_addr_npi: AddrNpi,
                              source_addr_ton: AddrTon,
                              dest_addr_ton: AddrTon,
                              priority_flag: PriorityFlag = PriorityFlag.LEVEL_0,
                              registered_delivery: RegisteredDelivery = None):

        if registered_delivery is None:
            registered_delivery = RegisteredDelivery(
                RegisteredDeliveryReceipt.NO_SMSC_DELIVERY_RECEIPT_REQUESTED)

        def try_to_encode(text: str, codec: str) -> Optional[bytes]:
            try:
                return text.encode(codec)
            except UnicodeEncodeError:
                return None

        codec_data_coding_map = [
            ('ascii', DataCodingDefault.SMSC_DEFAULT_ALPHABET),
            ('latin-1', DataCodingDefault.LATIN_1),
            ('cyrillic', DataCodingDefault.CYRILLIC),
            ('utf-16be', DataCodingDefault.UCS2)
        ]

        short_message = None
        data_coding = None
        for pair in codec_data_coding_map:
            short_message = try_to_encode(text=text, codec=pair[0])
            if short_message:
                data_coding = pair[1]
                break

        if data_coding is None or short_message is None:
            raise Exception(f'Text {text} could not be encoded')

        deliver_sm_dict = {
            'sequence_number': self.next_sequence_number(),
            'service_type': None,
            'source_addr_ton': source_addr_ton,
            'source_addr_npi': source_addr_npi,
            'source_addr': source_addr,
            'dest_addr_ton': dest_addr_ton,
            'dest_addr_npi': dest_addr_npi,
            'destination_addr': destination_addr,
            'esm_class': EsmClass(EsmClassMode.DEFAULT, EsmClassType.DEFAULT),
            'protocol_id': None,
            'priority_flag': priority_flag,
            'registered_delivery': registered_delivery,
            'replace_if_present_flag': ReplaceIfPresentFlag.DO_NOT_REPLACE,
            'data_coding': DataCoding(scheme_data=data_coding),
            'short_message': short_message,
        }

        max_size = 100

        if len(short_message) <= max_size:
            deliver_sm = DeliverSM(**deliver_sm_dict)
            requests = [deliver_sm]
        else:
            requests = []

            short_messages = [
                short_message[x:x + max_size] for x in
                range(0, len(short_message), max_size)
            ]

            ref_num = self.next_ref_num()
            total_segments = len(short_messages)

            if total_segments > 255:
                raise Exception('Text is too long and it cannot be sent')

            for seq_num, trunk_short_message in enumerate(short_messages):
                deliver_sm_dict['short_message'] = trunk_short_message
                deliver_sm_dict['sequence_number'] = self.next_sequence_number()
                deliver_sm = DeliverSM(
                    **deliver_sm_dict,
                    sar_msg_ref_num=ref_num,
                    sar_total_segments=total_segments,
                    sar_segment_seqnum=seq_num + 1
                )

                requests.append(deliver_sm)

        self._send_requests(requests=requests, merge=True)

    async def on_enquire_link(self, request: EnquireLink):
        pass

    async def on_deliver_sm_resp(self, request: DeliverSMResp):
        pass

    async def request_handler(self, pdu: PDU):
        if pdu.command_id == CommandId.enquire_link:
            request = EnquireLink(sequence_number=pdu.sequence_number, **pdu.params)
            coro = self.on_enquire_link(request)
        elif pdu.command_id == CommandId.deliver_sm_resp:
            request = DeliverSMResp(sequence_number=pdu.sequence_number, **pdu.params)
            coro = self.on_deliver_sm_resp(request)
        else:
            raise Exception(f'Command {pdu.command_id} not implemented')

        self.app.logger.debug(f'Request received: {request}')

        await coro

        if hasattr(request, 'require_ack'):
            self.app.logger.debug(f'Request {request.command_id} has require_ack')
            self._send_response(
                request.require_ack(sequence_number=request.sequence_number))
            self.app.logger.debug(f'Sent response for request {request.command_id}')
        else:
            self.app.logger.debug(
                f'Request {request.command_id} DOES NOT HAVE require_ack')

    def data_received(self, data: bytes) -> None:
        asyncio.create_task(self.handle_data_received(data))

    async def handle_data_received(self, data: bytes):
        self.app.logger.debug(f'Received {len(data)} bytes.')
        self.app.logger.debug(data)

        file = io.BytesIO(data)

        pdu = PDUEncoder().decode(file)

        self.app.logger.debug(f'Command received: {pdu.command_id}')

        if pdu.command_id == CommandId.submit_sm:
            submit_sm = SubmitSM(sequence_number=pdu.sequence_number, **pdu.params)
            sms = SMStringEncoder().decode_SM(submit_sm).str

            self._send_response(
                submit_sm.require_ack(sequence_number=submit_sm.sequence_number)
            )

            # Check if the message is only a part from a series
            while (submit_sm.params.get('more_messages_to_send') ==
                   MoreMessagesToSend.MORE_MESSAGES):

                pdu = PDUEncoder().decode(file)
                if pdu.command_id != CommandId.submit_sm:
                    raise Exception(f'Received {pdu.command_id} instead '
                                    f'of {CommandId.submit_sm} for concatenated messages')
                submit_sm = SubmitSM(sequence_number=pdu.sequence_number, **pdu.params)
                sms += SMStringEncoder().decode_SM(submit_sm).str

                self._send_response(
                    submit_sm.require_ack(sequence_number=submit_sm.sequence_number)
                )

            await self.app.handle_sms_received(
                client=self._client,
                source_number=submit_sm.params['source_addr'],
                dest_number=submit_sm.params['destination_addr'],
                text=sms
            )
        elif pdu.command_id == CommandId.bind_transceiver:
            request = BindTransceiver(sequence_number=pdu.sequence_number, **pdu.params)

            if self.is_bound:
                smpp_status = CommandStatus.ESME_RALYBND
            else:
                client = SmppClient(
                    protocol=self,
                    system_id=request.params['system_id'],
                    password=request.params['password'],
                    system_type=request.params['system_type'],
                    interface_version=request.params['interface_version'],
                    addr_ton=request.params['addr_ton'],
                    addr_npi=request.params['addr_npi']
                )

                self._client = client
                try:
                    client = await self.app.handle_bound_client(client=client)
                except Exception as e:
                    self.app.logger.error(f'Exception in handle_bound_client: {e}')
                    smpp_status = CommandStatus.ESME_RBINDFAIL
                else:
                    if client:
                        self.is_bound = True
                        smpp_status = CommandStatus.ESME_ROK
                    else:
                        self.is_bound = False
                        # Generic bind error
                        smpp_status = CommandStatus.ESME_RBINDFAIL

            resp = BindTransceiverResp(sequence_number=request.sequence_number,
                                       status=smpp_status,
                                       system_id=self.app.name)
            self._send_response(resp)

        elif pdu.command_id == CommandId.unbind:
            self.is_bound = False

            request = Unbind(sequence_number=pdu.sequence_number, **pdu.params)
            resp = UnbindResp(sequence_number=request.sequence_number)

            self._send_response(resp)

            await self.app.handle_unbound_client(self._client)

        else:
            await self.request_handler(pdu)

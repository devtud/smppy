from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional, Callable, Coroutine, List

from smpp.pdu.operations import BindTransceiver, SubmitSM, EnquireLink, Unbind, DeliverSM, \
    DeliverSMResp
from smpp.pdu.pdu_encoding import PDUEncoder
from smpp.pdu.pdu_types import (
    CommandId, PDUResponse, PDU, AddrTon, AddrNpi, EsmClassMode, EsmClass, EsmClassType,
    RegisteredDelivery, PriorityFlag, RegisteredDeliveryReceipt, ReplaceIfPresentFlag,
    DataCodingDefault, DataCoding, PDURequest, MoreMessagesToSend
)
from smpp.pdu.sm_encoding import SMStringEncoder


class SmppProtocol(asyncio.Protocol):
    def __init__(self,
                 new_client_cb: Callable[[SmppProtocol, str, str], Coroutine],
                 unbound_client_cb: Callable[[SmppProtocol], Coroutine],
                 sms_received_cb: Callable[[SmppProtocol, str, str, str], Coroutine],
                 logger=None):
        """

        Args:
            new_client_cb: called when a bind_transceiver request is received
            unbound_client_cb: called when unbind request is received
            sms_received_cb: called when a submit_sm request is received
        """
        if not asyncio.iscoroutinefunction(new_client_cb):
            raise Exception('new_client_cb must be an async callable')

        # there is a 1:1 relationship between transport and protocol
        self.transport: Optional[asyncio.Transport] = None

        self.new_client_cb: Callable[[SmppProtocol, str, str], Coroutine] = new_client_cb
        self.unbound_client_cb: Callable[[SmppProtocol], Coroutine] = unbound_client_cb
        self.sms_received_cb: Callable[
            [SmppProtocol, str, str, str], Coroutine] = sms_received_cb

        # Whether bind_transceiver has been done
        self.is_bound = False

        # Used as reference number for concatenated deliver_sms. Do not use it directly,
        # but with `self.next_ref_num()`
        self._ref_num: int = 0

        self._sequence_number = 0

        if not logger:
            logger = logging.getLogger('server')
        self.logger = logger

    def next_ref_num(self):
        self._ref_num += 1
        return self._ref_num

    def next_sequence_number(self):
        self._sequence_number += 1
        return self._sequence_number

    def connection_made(self, transport: asyncio.Transport) -> None:
        self.transport = transport
        self._ref_num = 0

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            self.logger.error(f'ERROR: {exc}')
        else:
            self.logger.warning('Closing connection')
        self.transport = None
        self.is_bound = False
        super(SmppProtocol, self).connection_lost(exc)

    def _send_PDU(self, pdu: PDU):
        self.transport.write(PDUEncoder().encode(pdu))

    def _send_requests(self, requests: List[PDURequest], merge=True):
        if not self.is_bound:
            raise Exception('Cannot send request to unbound client')

        encoder = PDUEncoder()

        if merge:
            buffer_list = [encoder.encode(pdu) for pdu in requests]
            self.transport.write(b''.join(buffer_list))
        else:
            for pdu in requests:
                self.transport.write(encoder.encode(pdu))

    def _send_response(self, response: PDUResponse):
        self._send_PDU(response)

    async def send_deliver_sm(self, source_addr: str, destination_addr: str, text: str):

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
            'source_addr_ton': AddrTon.INTERNATIONAL,
            'source_addr_npi': AddrNpi.UNKNOWN,
            'source_addr': source_addr,
            'dest_addr_ton': AddrTon.INTERNATIONAL,
            'dest_addr_npi': AddrNpi.UNKNOWN,
            'destination_addr': destination_addr,
            'esm_class': EsmClass(EsmClassMode.DEFAULT, EsmClassType.DEFAULT),
            'protocol_id': 0,
            'priority_flag': PriorityFlag.LEVEL_0,
            'registered_delivery': RegisteredDelivery(
                RegisteredDeliveryReceipt.NO_SMSC_DELIVERY_RECEIPT_REQUESTED),
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

    async def on_bind_transceiver(self, request: BindTransceiver):
        self.is_bound = True
        await self.new_client_cb(self,
                                 request.params['system_id'],
                                 request.params['password'])

    async def on_submit_sm(self, request: SubmitSM):
        smstring = SMStringEncoder().decode_SM(request)
        await self.sms_received_cb(self,
                                   request.params['source_addr'],
                                   request.params['destination_addr'],
                                   smstring.str)

    async def on_enquire_link(self, request: EnquireLink):
        pass

    async def on_unbind(self, request: Unbind):
        self.is_bound = False
        await self.unbound_client_cb(self)

    async def on_deliver_sm_resp(self, request: DeliverSMResp):
        pass

    async def request_handler(self, pdu: PDU):
        if pdu.command_id == CommandId.bind_transceiver:
            request = BindTransceiver(sequence_number=pdu.sequence_number,
                                      **pdu.params)
            coro = self.on_bind_transceiver(request)
        elif pdu.command_id == CommandId.submit_sm:
            request = SubmitSM(sequence_number=pdu.sequence_number, **pdu.params)
            coro = self.on_submit_sm(request)
        elif pdu.command_id == CommandId.enquire_link:
            request = EnquireLink(sequence_number=pdu.sequence_number, **pdu.params)
            coro = self.on_enquire_link(request)
        elif pdu.command_id == CommandId.unbind:
            request = Unbind(sequence_number=pdu.sequence_number, **pdu.params)
            coro = self.on_unbind(request)
        elif pdu.command_id == CommandId.deliver_sm_resp:
            request = DeliverSMResp(sequence_number=pdu.sequence_number, **pdu.params)
            coro = self.on_deliver_sm_resp(request)
        else:
            raise Exception(f'Command {pdu.command_id} not implemented')

        self.logger.debug(f'Request received: {request}')

        await coro

        if hasattr(request, 'require_ack'):
            self.logger.debug(f'Request {request.command_id} has require_ack')
            self._send_response(
                request.require_ack(sequence_number=request.sequence_number))
            self.logger.debug(f'Sent response for request {request.command_id}')
        else:
            self.logger.debug(f'Request {request.command_id} DOES NOT HAVE require_ack')

    def data_received(self, data: bytes) -> None:
        self.logger.debug(f'Received {len(data)} bytes.')
        self.logger.debug(data)

        file = io.BytesIO(data)

        pdu = PDUEncoder().decode(file)

        self.logger.debug(f'Command received: {pdu.command_id}')

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

            asyncio.create_task(
                self.sms_received_cb(
                    self,
                    submit_sm.params['source_addr'],
                    submit_sm.params['destination_addr'],
                    sms
                )
            )
        else:
            asyncio.create_task(self.request_handler(pdu))

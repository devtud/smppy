from smppy.server import SmppProtocol


class Client:
    def __init__(self, system_id: str, protocol: SmppProtocol):
        self.system_id: str = system_id
        self.protocol: SmppProtocol = protocol

    async def send_sms(self, source: str, dest: str, text: str) -> None:
        await self.protocol.send_deliver_sm(
            source_addr=source,
            destination_addr=dest,
            text=text
        )

# SMPPY
## Simple tool for building SMPP servers

---

## How to install

```bash
pip install smppy
```

## How to build a SMPP server app with `smppy`

Creating a SMPP server app with `smppy` is simple as inheriting from
`smppy.Application` and implementing a few methods. Below is the example
app you can find in [scripts/example_server](scripts/example_server.py) file.

```python
from smppy import Application, SmppClient
from typing import List, Union


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
```

## Run

### 1. Run the server app

Source code: [example_server.py](scripts/example_server.py)

In a terminal tab run:

    python -m scripts.example_server

### 2. Run the test client

**smppy** has also a SMPP client which can be manipulated through a command
line:

Source code: [test_client.py](scripts/test_client.py):

In a second terminal tab run:

    python -m scripts.test_client

A SMPP client connects to the server and a Python shell opens.

Available functions:
- `send_message(message: str, sender: str, receiver: str)`

### 3. Watch logs

In a third terminal tab run:

    tail -f client.log

to view all client events.

## Contributing

Feel free to open issues and pull requests.

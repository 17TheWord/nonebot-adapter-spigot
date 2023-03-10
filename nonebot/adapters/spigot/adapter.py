import json
import asyncio
import inspect
import contextlib
from typing import Any, Dict, Optional, Generator, Type, List

from nonebot.adapters import Adapter as BaseAdapter
from nonebot.drivers import (
    URL,
    Driver,
    WebSocket,
    ReverseDriver,
    WebSocketServerSetup,
)
from nonebot.exception import WebSocketClosed
from nonebot.typing import overrides
from nonebot.utils import escape_tag, logger_wrapper

from . import event
from .bot import Bot
from .event import Event
from .collator import Collator
from .utils import get_connections

log = logger_wrapper("Spigot")

DEFAULT_MODELS: List[Type[Event]] = []
for model_name in dir(event):
    model = getattr(event, model_name)
    if not inspect.isclass(model) or not issubclass(model, Event):
        continue
    DEFAULT_MODELS.append(model)


class Adapter(BaseAdapter):
    event_models = Collator(
        "Spigot",
        DEFAULT_MODELS,
        (
            "post_type",
            "event_name",
        ),
    )

    @overrides(BaseAdapter)
    def __init__(self, driver: Driver, **kwargs: Any):
        super().__init__(driver, **kwargs)
        self.connections: Dict[str, WebSocket] = {}
        self._setup()

    @classmethod
    @overrides(BaseAdapter)
    def get_name(cls) -> str:
        return "Spigot"

    def _setup(self) -> None:
        if isinstance(self.driver, ReverseDriver):
            ws_setup = WebSocketServerSetup(
                URL("/spigot/ws"), self.get_name(), self._handle_ws
            )
            self.setup_websocket_server(ws_setup)

    @overrides(BaseAdapter)
    async def _call_api(self, bot: Bot, api: str, **data: Any) -> Any:
        pass

    async def _handle_ws(self, websocket: WebSocket) -> None:
        self_name = websocket.request.headers.get("x-self-name").encode('utf-8').decode('unicode_escape')

        # check self_name
        if not self_name:
            log("WARNING", "Missing X-Self-ID Header")
            await websocket.close(1008, "Missing X-Self-Name Header")
            return
        elif self_name in self.bots:
            log("WARNING", f"There's already a bot {self_name}, ignored")
            await websocket.close(1008, "Duplicate X-Self-Name")
            return

        await websocket.accept()
        bot = Bot(self, self_name)
        self.connections[self_name] = websocket
        get_connections[self_name] = websocket
        self.bot_connect(bot)

        log("INFO", f"<y>Bot {escape_tag(self_name)}</y> connected")

        try:
            while True:
                data = await websocket.receive()
                json_data = json.loads(data)
                if event := self.json_to_event(json_data, self_name):
                    asyncio.create_task(bot.handle_event(event))
        except WebSocketClosed as e:
            log("WARNING", f"WebSocket for Bot {escape_tag(self_name)} closed by peer")
        except Exception as e:
            log(
                "ERROR",
                "<r><bg #f8bbd0>Error while process data from websocket "
                f"for bot {escape_tag(self_name)}.</bg #f8bbd0></r>",
                e,
            )
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()
            self.connections.pop(self_name, None)
            self.bot_disconnect(bot)

    @classmethod
    def get_event_model(
            cls, data: Dict[str, Any]
    ) -> Generator[Type[Event], None, None]:
        """???????????????????????? `Event Model` ??? `FallBack Event Model` ?????????"""
        yield from cls.event_models.get_model(data)

    @classmethod
    def json_to_event(cls, json_data: Any, self_name: Optional[str] = None) -> Optional[Event]:
        """??? json ??????????????? Event ?????????

        ????????? API ?????????????????????????????? Event ?????? Bot????????????????????? ResultStore???

        ??????:
            json_data: json ??????
            self_name: ?????? Event ????????? Bot

        ??????:
            Event ????????????????????????????????? API ?????????????????????????????? None
        """
        if not isinstance(json_data, dict):
            return None

        try:
            for model in cls.get_event_model(json_data):
                try:
                    event = model.parse_obj(json_data)
                    break
                except Exception as e:
                    log("DEBUG", "Event Parser Error", e)
            else:
                event = Event.parse_obj(json_data)

            return event
        except Exception as e:
            log(
                "ERROR",
                "<r><bg #f8bbd0>Failed to parse event. "
                f"Raw: {escape_tag(str(json_data))}</bg #f8bbd0></r>",
                e,
            )

    def get_connections(self):
        return self.connections

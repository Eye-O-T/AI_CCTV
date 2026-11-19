"""인증·카메라·글로벌 인물·이벤트 테이블을 등록한다."""

from .camera import Camera
from .server_run import ServerRun
from .user import User
from .global_person import GlobalPerson
from .event import Event

__all__ = ["Camera", "ServerRun", "User", "GlobalPerson", "Event"]

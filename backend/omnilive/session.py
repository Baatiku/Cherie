import hashlib,hmac,secrets,threading,time
from dataclasses import dataclass
from .reference_image import ValidatedReferenceImage
from .config import settings
from .models import EffectProfile,QualityProfile,SessionResponse

@dataclass
class SessionState:
    session_id:str
    token_digest:str
    profile:QualityProfile
    effect:EffectProfile
    region_hint:str|None
    expires_at:int
    last_sequence:int=-1
    worker_key:str=""
    reference_image:ValidatedReferenceImage|None=None

class SessionRegistry:
    def __init__(self, max_sessions: int | None = None):
        self._items={}
        self._lock=threading.Lock()
        configured = max_sessions if max_sessions is not None else settings.max_sessions
        self._max_sessions = max(1, int(configured))

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def sweep_expired(self) -> int:
        now=int(time.time())
        with self._lock:
            expired=[sid for sid, state in self._items.items() if state.expires_at <= now]
            for sid in expired:
                self._items.pop(sid, None)
            return len(expired)

    def _digest(self,token:str)->str:
        return hmac.new(settings.session_secret.encode(),token.encode(),hashlib.sha256).hexdigest()

    def create(self,profile:QualityProfile,effect:EffectProfile,region_hint:str|None)->SessionResponse:
        now=int(time.time())
        with self._lock:
            # Drop expired entries opportunistically so a leaked registry
            # cannot grow unbounded on a long-lived GPU container.
            expired=[sid for sid, state in self._items.items() if state.expires_at <= now]
            for sid in expired:
                self._items.pop(sid, None)
            if len(self._items) >= self._max_sessions:
                raise RuntimeError("too many active sessions")
        sid=secrets.token_urlsafe(18)
        token=secrets.token_urlsafe(32)
        state=SessionState(
            session_id=sid,
            token_digest=self._digest(token),
            profile=profile,
            effect=effect,
            region_hint=region_hint,
            expires_at=now+settings.session_ttl_seconds,
            worker_key=secrets.token_hex(8),
        )
        with self._lock:
            self._items[sid]=state
        return SessionResponse(
            session_id=sid,
            token=token,
            profile=profile,
            effect=effect,
            expires_at=state.expires_at,
            region_hint=region_hint,
        )

    def authenticate(self,session_id:str,token:str):
        now=int(time.time())
        with self._lock:
            state=self._items.get(session_id)
            if state is None:
                return None
            if state.expires_at<=now:
                self._items.pop(session_id,None)
                return None
            if not hmac.compare_digest(state.token_digest,self._digest(token)):
                return None
            return state

    def set_reference_image(self,session_id:str,image:ValidatedReferenceImage)->bool:
        with self._lock:
            state=self._items.get(session_id)
            if state is None:
                return False
            state.reference_image=image
            return True

    def get_reference_image(self,session_id:str)->ValidatedReferenceImage|None:
        with self._lock:
            state=self._items.get(session_id)
            return None if state is None else state.reference_image

    def discard(self,session_id:str)->bool:
        with self._lock:
            return self._items.pop(session_id,None) is not None

    def update_profile(self,session_id:str,profile:QualityProfile)->bool:
        with self._lock:
            state=self._items.get(session_id)
            if state is None:
                return False
            state.profile=profile
            return True

registry=SessionRegistry()

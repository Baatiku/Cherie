import hashlib,hmac,secrets,threading,time
from dataclasses import dataclass
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

class SessionRegistry:
    def __init__(self):
        self._items={}
        self._lock=threading.Lock()

    def _digest(self,token:str)->str:
        return hmac.new(settings.session_secret.encode(),token.encode(),hashlib.sha256).hexdigest()

    def create(self,profile:QualityProfile,effect:EffectProfile,region_hint:str|None)->SessionResponse:
        now=int(time.time())
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

registry=SessionRegistry()

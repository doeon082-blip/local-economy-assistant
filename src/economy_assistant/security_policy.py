"""Fail-closed action checks used by local API routes."""
from dataclasses import dataclass
from enum import Enum

class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"

@dataclass(frozen=True)
class ActionRequest:
    tool: str
    sends_data_outside: bool = False
    changes_data: bool = False

@dataclass(frozen=True)
class SecurityPolicy:
    allowed_tools: frozenset[str]
    approval_required_tools: frozenset[str] = frozenset()
    allow_external_sends: bool = False
    allow_data_changes: bool = False

def require_allowed(request: ActionRequest, policy: SecurityPolicy, *, trusted_user_approved: bool = False) -> None:
    if request.tool not in policy.allowed_tools:
        raise PermissionError("도구가 허용 목록에 없습니다.")
    if request.sends_data_outside and not policy.allow_external_sends:
        raise PermissionError("외부 전송이 정책으로 차단되었습니다.")
    if request.changes_data and not policy.allow_data_changes:
        raise PermissionError("데이터 변경이 정책으로 차단되었습니다.")
    if request.tool in policy.approval_required_tools and not trusted_user_approved:
        raise PermissionError("사용자의 명시적 승인(요청 버튼)이 필요합니다.")

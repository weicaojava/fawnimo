"""Guard durable task data; provider credentials must resolve from trusted references."""
import re
from fastapi import HTTPException

SECRET_FIELDS={'apikey','authorization','password','accesstoken','refreshtoken','secretkey','clientsecret','bearertoken'}


def validate_task_payload(payload: object) -> None:
    """Reject credential fields before a task is stored, without echoing their values.

    Prompt strings remain inert data. This validation does not parse strings as
    commands or let an imported prompt change endpoints, credentials or accounts.
    Bounds also prevent malicious deeply nested metadata from consuming the stack.
    """
    pending=[(payload,0)];count=0
    while pending:
        item,depth=pending.pop();count+=1
        if depth>24 or count>10000:raise HTTPException(400,'任务参数层级或数量超限')
        if isinstance(item,dict):
            for key,value in item.items():
                normalized=re.sub(r'[^a-z0-9]','',str(key).lower())
                if normalized in SECRET_FIELDS and value not in (None,''):
                    raise HTTPException(400,'任务参数不得保存凭据；请使用受信的服务配置引用')
                pending.append((value,depth+1))
        elif isinstance(item,(list,tuple)):
            pending.extend((value,depth+1) for value in item)


def reject_legacy_video_submission() -> None:
    """Fail closed until the durable, version-aware submission chain passes M3A.

    This retires the unsafe entry point rather than silently treating its lack of
    crash recovery as a normal retry. It is not a claim that generation is ready.
    """
    raise HTTPException(409,'旧视频提交链已停用：可靠提交与版本化生成链正在开发验收')

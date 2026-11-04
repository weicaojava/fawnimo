"""Small bounded native-video response helpers without provider prose, credentials or side effects."""
from email.utils import parsedate_to_datetime
import json
import math
import re


def finite_video_json(raw):
    """Decode one64KiB object with unique keys, finite values and bounded nesting/node count, retaining no failed input."""
    def unique(pairs):
        """Duplicate fields cannot replace an already checked task/account identity."""
        result={}
        for key,value in pairs:
            if key in result:raise ValueError()
            result[key]=value
        return result
    def invalid(_value):
        """Nonfinite JSON numbers cannot become usage or control data."""
        raise ValueError()
    try:
        if not isinstance(raw,bytes) or len(raw)>65536:raise ValueError()
        value=json.loads(raw.decode('utf8'),object_pairs_hook=unique,parse_constant=invalid)
        if not isinstance(value,dict):raise ValueError()
        pending=[(value,0)];count=0
        while pending:
            item,depth=pending.pop();count+=1
            if depth>24 or count>10000:raise ValueError()
            if isinstance(item,dict):
                for key,child in item.items():key.encode('utf8');pending.append((child,depth+1))
            elif isinstance(item,list):pending.extend((child,depth+1) for child in item)
            elif isinstance(item,str):item.encode('utf8')
            elif isinstance(item,float) and not math.isfinite(item):raise ValueError()
        return value
    except (ValueError,TypeError,UnicodeError,RecursionError,OverflowError):
        raise ValueError('Invalid provider JSON') from None


def video_retry_delay(headers,now):
    """Use one bounded Retry-After value for reads only, with a five-second floor and conservative malformed fallback."""
    if not isinstance(headers,dict) or any(not isinstance(key,str) for key in headers):return 30
    values=[value for key,value in headers.items() if key.lower()=='retry-after']
    if len(values)!=1 or not isinstance(values[0],str):return 30
    value=values[0].strip()
    try:
        if re.fullmatch(r'[0-9]+',value,re.ASCII):seconds=int(value)
        else:
            parsed=parsedate_to_datetime(value)
            if parsed.tzinfo is None:return 30
            seconds=math.ceil(parsed.timestamp()-now)
        if seconds>9007199254740991:return 30
        return max(5,seconds)
    except (ValueError,TypeError,OverflowError,OSError):return 30

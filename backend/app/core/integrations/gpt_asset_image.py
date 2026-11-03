"""Explicit bounded GPT Image JSON single-PNG generation/edits; exact owned reference bytes only, no URL or legacy-field fallback."""
import base64
from dataclasses import replace
import hashlib
import json
from urllib.parse import urlsplit
from app.core.contracts.asset_image_protocols import GPT_IMAGE_PROTOCOL,GPT_IMAGE_PROFILE,GPT_IMAGE_MODELS,GPT_IMAGE_SIZES,AssetImageProtocol
from app.core.integrations.asset_image import AssetImageModelError
from app.core.integrations.seedream_asset_image import PURPOSES,owned_request,record_response,request_identity,strict_json
from app.core.integrations.verified_image import verify_image_bytes


async def invoke_gpt_image(*,base_url,api_key,model,prompt,size,protocol:AssetImageProtocol,references=(),response_format='b64_json',
        trusted_local_host=None,trusted_local_ports=frozenset(),on_response=None):
    """Send one fixed medium-quality opaque PNG request through owned transport; preserve response identity before decode and never fetch supplied result URLs."""
    parsed=urlsplit(base_url)
    if (not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password
            or protocol.protocol!=GPT_IMAGE_PROTOCOL or protocol.profile!=GPT_IMAGE_PROFILE or model not in GPT_IMAGE_MODELS
            or protocol.sizes!=GPT_IMAGE_SIZES or protocol.result_hosts or protocol.max_references!=3):
        raise AssetImageModelError('图片模型或明确协议配置无效，未发起请求')
    if size not in protocol.sizes or response_format!='b64_json' or len(references)>3 or not isinstance(prompt,str) or not prompt.strip() or len(prompt)>8000:
        raise AssetImageModelError('此图片协议不支持这些输入、尺寸或返回方式，未发起请求')
    images=[];instructions=[];total=0
    for index,(proof,purpose) in enumerate(references):
        total+=len(proof.content)
        if (purpose not in PURPOSES or not 1<=len(proof.content)<=4*1024*1024 or hashlib.sha256(proof.content).hexdigest()!=proof.sha256
                or proof.mime not in {'image/png','image/jpeg'} or min(proof.width,proof.height)<15 or max(proof.width,proof.height)>4096
                or proof.width*proof.height>16000000 or not 1/16<=proof.width/proof.height<=16):
            raise AssetImageModelError('参考图片证明或用途无效，未发起请求')
        images.append({'image_url':'data:'+proof.mime+';base64,'+base64.b64encode(proof.content).decode('ascii')})
        instructions.append(f'参考图{index+1}用于{PURPOSES[purpose]}。')
    if total>12*1024*1024:raise AssetImageModelError('参考图片超过本协议资源上限，未发起请求')
    payload={'model':model,'prompt':prompt+('\n\n参考图用途说明：'+''.join(instructions) if instructions else ''),
        'size':size,'n':1,'quality':'medium','output_format':'png','background':'opaque','stream':False}
    if images:payload['images']=images
    content=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8')
    if len(content)>18*1024*1024:raise AssetImageModelError('图片请求超过本协议资源上限，未发起请求')
    endpoint='/images/edits' if images else '/images/generations'
    try:
        response=await owned_request(base_url.rstrip('/')+endpoint,allowed_hosts={parsed.hostname.lower()},
            trusted_local_host=trusted_local_host,trusted_local_ports=set(trusted_local_ports),method='POST',data=content,
            headers={'Authorization':'Bearer '+api_key,'Content-Type':'application/json','Accept':'application/json'},
            timeout=60,max_bytes=12*1024*1024,max_redirects=0)
    except Exception:
        raise AssetImageModelError('图片请求失败或结果未知；不会自动重发，原项目费用仍需核对') from None
    request_id=request_identity(response,api_key)
    await record_response(on_response,request_id)
    if not 200<=response.status<300:
        raise AssetImageModelError(f'图片模型返回 HTTP {response.status}；未创建候选，原项目费用仍需核对',provider_request_id=request_id)
    try:
        if response.headers.get('content-type','').split(';',1)[0].strip().lower()!='application/json':raise ValueError()
        data=strict_json(response.body)
        if (not isinstance(data,dict) or data.get('error') or 'model' in data and data['model']!=model
                or 'size' in data and data['size']!=size or 'output_format' in data and data['output_format']!='png'
                or 'quality' in data and data['quality']!='medium'):
            raise ValueError()
        items=data['data']
        if not isinstance(items,list) or len(items)!=1 or not isinstance(items[0],dict):raise ValueError()
        item=items[0]
        if item.get('error') or item.get('url') is not None or not isinstance(item.get('b64_json'),str) or len(item['b64_json'])>12*1024*1024:raise ValueError()
        decoded=base64.b64decode(item['b64_json'],validate=True)
        proof=await verify_image_bytes(decoded)
        if proof.mime!='image/png' or (proof.width,proof.height)!=tuple(map(int,size.split('x'))):raise ValueError()
        return replace(proof,provider_request_id=request_id)
    except Exception:
        raise AssetImageModelError('返回内容不是本协议声明的单张准确尺寸PNG；未下载网址或替换返回方式，原项目费用仍需核对',provider_request_id=request_id) from None

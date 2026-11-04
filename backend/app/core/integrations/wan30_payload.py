"""Pure bounded Wan3.0 request construction from original project-owned text/image proofs; no URLs from callers or file uploads."""
import base64
import hashlib
import io
import json

from PIL import Image

from app.core.contracts.wan30 import MODEL_ID,TEXT_PROTOCOL,REFERENCE_PROTOCOL,SUPPORTED_DURATIONS,MAX_IMAGE_BYTES,MAX_REQUEST_BYTES
from app.core.contracts.seedance_references import PreparedReference,snapshot_reference_selection


def checked_image_url(proof,*,frames):
    """Serialize the unchanged verified image, rejecting unsupported dimensions, alpha/animation and mismatched decoded headers."""
    if (not isinstance(proof,PreparedReference) or proof.mime not in {'image/png','image/jpeg'}
            or not isinstance(proof.content,bytes) or proof.delivery_url is not None
            or any(value is not None for value in (proof.duration_ms,proof.fps,proof.frame_clock_sha256,proof.has_audio))
            or type(proof.byte_size) is not int or not 0<proof.byte_size<=MAX_IMAGE_BYTES
            or len(proof.content)!=proof.byte_size or hashlib.sha256(proof.content).hexdigest()!=proof.sha256
            or type(proof.width) is not int or type(proof.height) is not int
            or not 300<=proof.width<=4096 or not 300<=proof.height<=4096
            or proof.width*proof.height>16000000 or not 0.4<=proof.width/proof.height<=2.5):
        raise ValueError('Wan3.0 image proof does not match the supported profile')
    if frames and proof.width*16!=proof.height*9:
        raise ValueError('Wan3.0 frame profile requires an actual9:16 image')
    try:
        with Image.open(io.BytesIO(proof.content)) as image:
            if (image.format!=('PNG' if proof.mime=='image/png' else 'JPEG') or image.size!=(proof.width,proof.height)
                    or image.mode not in {'RGB','L','P'} or 'transparency' in image.info
                    or getattr(image,'is_animated',False) or image.getexif().get(274) not in (None,1)):
                raise ValueError()
            image.load()
    except (OSError,ValueError,SyntaxError,Image.DecompressionBombError):
        raise ValueError('Wan3.0 image header or transparency is unsupported') from None
    return 'data:'+proof.mime+';base64,'+base64.b64encode(proof.content).decode('ascii')


def build_wan30_body(snapshot,*,protocol=TEXT_PROTOCOL,project_id=None,prepared=()):
    """Keep the original frozen text/duration/order, accepting only the explicitly configured text or bounded image-reference profile."""
    if not isinstance(protocol,str) or protocol not in {TEXT_PROTOCOL,REFERENCE_PROTOCOL}:
        raise ValueError('Unsupported Wan3.0 protocol')
    fields={'model_id','duration','references','prompt','prompt_revision'}
    if protocol==REFERENCE_PROTOCOL:fields.add('video_reference')
    if not isinstance(snapshot,dict) or set(snapshot)-fields:
        raise ValueError('Unsupported Wan3.0 request fields or protocol')
    if snapshot.get('model_id')!=MODEL_ID:
        raise ValueError('Wan3.0 requires the exact configured model')
    duration=snapshot.get('duration');prompt=snapshot.get('prompt');revision=snapshot.get('prompt_revision')
    if type(duration) is not int or duration not in SUPPORTED_DURATIONS:
        raise ValueError('Wan3.0 duration must be an integer from4 to30')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>20000:
        raise ValueError('Wan3.0 requires1 to20000 prompt characters')
    if revision is not None and (not isinstance(revision,str) or not revision or len(revision)>128):
        raise ValueError('Invalid Wan3.0 original prompt revision')
    try:
        prompt.encode('utf8')
        if revision is not None:revision.encode('utf8')
    except UnicodeError:
        raise ValueError('Invalid Wan3.0 prompt encoding') from None
    body={'model':MODEL_ID,'input':{'prompt':prompt},'parameters':{'resolution':'480P','ratio':'9:16',
        'duration':duration,'audio':False,'prompt_extend':False,'watermark':True}}
    if protocol==TEXT_PROTOCOL:
        if snapshot.get('references',[])!=[] or not isinstance(prepared,tuple) or prepared:
            raise ValueError('Wan3.0 text profile cannot receive media')
    else:
        selection=snapshot_reference_selection(snapshot)
        if (not isinstance(project_id,str) or not project_id or not isinstance(prepared,tuple)
                or len(prepared)!=len(selection.references) or any(chosen.kind!='image' for chosen in selection.references)):
            raise ValueError('Wan3.0 image-reference profile cannot receive video, audio, files or links')
        frames=selection.mode!='reference';media=[]
        for chosen,proof in zip(selection.references,prepared):
            if (not isinstance(proof,PreparedReference) or proof.project_id!=project_id
                    or proof.media_id!=chosen.media_id or proof.sha256!=chosen.expected_sha256):
                raise ValueError('Wan3.0 input differs from the original project, order or SHA')
            media.append({'type':chosen.role,'url':checked_image_url(proof,frames=frames)})
        body['input']['media']=media
        if frames:body['parameters']['ratio']='adaptive'
    if len(json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf8'))>MAX_REQUEST_BYTES:
        raise ValueError('Wan3.0 request exceeds the bounded profile')
    return body

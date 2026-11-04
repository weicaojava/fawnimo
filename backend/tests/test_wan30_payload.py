"""Pure original-text/image wire checks for the declared Wan3.0 subset, without vendor calls or AI-quality claims."""
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import io

from PIL import Image
import pytest

from app.core.contracts.wan30 import MODEL_ID,REFERENCE_PROTOCOL
from app.core.contracts.seedance_references import PreparedReference
from app.core.integrations.wan30_payload import build_wan30_body


def text_snapshot(**changes):
    """Produce an authored exact current snapshot, with no supplier-selected parameters or live identity."""
    return {'model_id':MODEL_ID,'duration':4,'references':[],'prompt':'  原始测试提示词。  ','prompt_revision':'authored-revision',**changes}


def image_proof(identifier='authored-image',*,width=900,height=1600,mode='RGB'):
    """Encode only an original test image and bind its actual immutable bytes/header to one authored project."""
    stream=io.BytesIO();Image.new(mode,(width,height)).save(stream,format='PNG');raw=stream.getvalue()
    return PreparedReference('authored-project',identifier,hashlib.sha256(raw).hexdigest(),'image/png',len(raw),width,height,raw)


def reference_snapshot(proofs,roles,mode):
    """Keep generic index, explicit roles and content hashes in the same selected order."""
    chosen=[{'media_id':proof.media_id,'expected_sha256':proof.sha256,'role':role} for proof,role in zip(proofs,roles)]
    return text_snapshot(references=[{'media_id':item['media_id'],'kind':'image'} for item in chosen],
        video_reference={'schema_version':1,'mode':mode,'references':chosen})


def test_text_omits_media_and_preserves_original_prompt_with_fixed_explicit_parameters():
    """Avoid implicit adaptive duration, audio or prompt rewriting while retaining the original prompt exactly."""
    source=text_snapshot();before=deepcopy(source);body=build_wan30_body(source)
    assert source==before and body=={'model':MODEL_ID,'input':{'prompt':source['prompt']},
        'parameters':{'resolution':'480P','ratio':'9:16','duration':4,'audio':False,'prompt_extend':False,'watermark':True}}


@pytest.mark.parametrize('changes',[
    {'model_id':'doubao-seedance-2-5-260628'},{'duration':True},{'duration':3},{'duration':31},
    {'duration':-1},{'prompt':' '},{'prompt':'x'*20001},{'seed':1},{'audio':True},
    {'references':[{'media_id':'authored-image','kind':'image'}]},{'prompt_revision':False},{'prompt':'\ud800'},
])
def test_unsupported_text_choices_fail_without_silent_dropping(changes):
    """The selected subset cannot borrow another model, smart duration, unsupported fields or unbound references."""
    with pytest.raises(ValueError):build_wan30_body(text_snapshot(**changes))


def test_first_and_last_frames_keep_both_original_entries_even_when_same_image():
    """Explicit same-image first/last choices are preserved and the existing exact9:16 frame profile is retained."""
    proof=image_proof();snapshot=reference_snapshot((proof,proof),('first_frame','last_frame'),'first_last_frames')
    before=deepcopy(snapshot)
    body=build_wan30_body(snapshot,protocol=REFERENCE_PROTOCOL,project_id='authored-project',prepared=(proof,proof))
    assert snapshot==before and body['parameters']['ratio']=='adaptive'
    assert [row['type'] for row in body['input']['media']]==['first_frame','last_frame']
    for row in body['input']['media']:
        assert base64.b64decode(row['url'].split(',',1)[1])==proof.content


def test_reference_images_preserve_the_original_order_bytes_and_project_binding():
    """Each ordered reference has its own project/media/hash witness instead of a caller-controlled URL."""
    proofs=tuple(image_proof('authored-image-'+str(index),width=900,height=900) for index in range(3))
    snapshot=reference_snapshot(proofs,('reference_image',)*3,'reference')
    body=build_wan30_body(snapshot,protocol=REFERENCE_PROTOCOL,project_id='authored-project',prepared=proofs)
    assert len(body['input']['media'])==3 and body['parameters']['ratio']=='9:16'
    assert [base64.b64decode(row['url'].split(',',1)[1]) for row in body['input']['media']]==[p.content for p in proofs]
    with pytest.raises(ValueError):build_wan30_body(snapshot,protocol=REFERENCE_PROTOCOL,project_id='foreign-project',prepared=proofs)
    with pytest.raises(ValueError):build_wan30_body(snapshot,protocol=REFERENCE_PROTOCOL,project_id='authored-project',prepared=tuple(reversed(proofs)))


@pytest.mark.parametrize('change',['content','dimensions','delivery_url','sha'])
def test_mutated_image_proofs_are_rejected(change):
    """Trusted proof objects cannot smuggle changed bytes, fake headers or an external delivery URL into this image-only profile."""
    proof=image_proof();snapshot=reference_snapshot((proof,),('first_frame',),'first_frame')
    mutation={'content':{'content':proof.content+b'x'},'dimensions':{'width':proof.width+1},
        'delivery_url':{'delivery_url':'https://untrusted.example/private'},'sha':{'sha256':'a'*64}}[change]
    with pytest.raises(ValueError):build_wan30_body(snapshot,protocol=REFERENCE_PROTOCOL,project_id='authored-project',prepared=(replace(proof,**mutation),))


def test_alpha_images_and_unsupported_video_roles_are_explicitly_refused():
    """No silent alpha flattening or unsupported video forwarding changes the original selected content."""
    proof=image_proof(mode='RGBA');snapshot=reference_snapshot((proof,),('first_frame',),'first_frame')
    with pytest.raises(ValueError,match='transparency'):
        build_wan30_body(snapshot,protocol=REFERENCE_PROTOCOL,project_id='authored-project',prepared=(proof,))
    video=text_snapshot(references=[{'media_id':'authored-video','kind':'video'}],
        video_reference={'schema_version':1,'mode':'reference','references':[{'media_id':'authored-video','expected_sha256':'b'*64,'role':'reference_video'}]})
    with pytest.raises(ValueError,match='cannot receive video'):
        build_wan30_body(video,protocol=REFERENCE_PROTOCOL,project_id='authored-project',prepared=(proof,))

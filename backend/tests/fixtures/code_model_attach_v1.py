"""Exact published eceb8c4 v1 attach-method counterexample; test data only, never a production linker."""
from contextlib import closing
from app.services.community.advice_store import canonical,digest
from app.services.community.change_bundles import verify_change_bundle,original_change_hash
from app.services.community.development_model_protocol import change_input
from app.services.community.development_model_store import MAX_LINK_BYTES

def legacy_attach_bundle(self,request,artifact):
    """Link only the exact already-saved inert original bundle, without executing files or granting test/PR/publication authority."""
    bundle=verify_change_bundle(artifact)
    with closing(self.connect()) as db:
        db.execute('BEGIN IMMEDIATE');self.validate(db)
        previous=self.view(db,request)
        if previous is None or previous['model_result'] is None or previous['model_result']['state']!='generated':raise ValueError('Original generated model text is required')
        body=change_input(previous['intent'],previous['model_result'])
        if bundle['input']!=body.model_dump() or bundle['scope']['work_id']!=request.work_id:raise ValueError('Inert bundle differs from the original model result and scope')
        link={'request_key':request.request_key,'bundle_id':artifact['bundle_id'],'input_hash':original_change_hash(body),
            'model_result_sha256':digest(previous['model_result']),'execution_authorized':False,'external_fee_fen':0,
            'model_quality_qualified':False,'independent_review_completed':False}
        payload=canonical(link)
        if len(payload.encode('utf8'))>MAX_LINK_BYTES:raise ValueError('Original model/bundle link exceeds its reservation')
        if previous['bundle'] is not None:
            if digest(previous['bundle'])!=digest(link):raise ValueError('Original model result is already linked to another bundle')
            return previous
        db.execute('INSERT INTO code_model_bundles VALUES(?,?,?,?)',(request.request_key,artifact['bundle_id'],payload,digest(link)))
        db.commit()
        return self.view(db,request)

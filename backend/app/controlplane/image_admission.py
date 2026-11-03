"""Default-closed fixed-file operator image admission, private worker binding and append-only admission witnesses; no model secrets or money ceilings."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from app.core.contracts.workspace_image_admission import WorkspaceImageAdmissionConfiguration,WorkspaceImageAdmissionGrant,image_submission_path


def canonical(value):
    """Use one finite deterministic policy representation for private-worker and durable audit comparisons."""
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def unique_fields(pairs):
    """Reject competing meanings of a private enabled/scope field."""
    value={}
    for name,item in pairs:
        if name in value:raise ValueError('Duplicate operator image-policy field')
        value[name]=item
    return value


def invalid_number(_value):
    """Nonfinite JSON values never select or enable an operator policy."""
    raise ValueError('Invalid operator image-policy number')


@dataclass(frozen=True)
class ImageAdmissionTicket:
    """Keep the complete non-secret immutable operator policy with one exact workspace grant for an atomic audit append."""
    configuration:WorkspaceImageAdmissionConfiguration
    grant:WorkspaceImageAdmissionGrant

    def checked_payload(self,workspace_id):
        """Validate the trusted object again before it accompanies an authenticated original request audit."""
        payload=canonical(self.configuration.model_dump(mode='json'))
        if (not self.configuration.enabled or workspace_id not in self.configuration.workspace_ids or self.grant.workspace_id!=workspace_id
                or hashlib.sha256(payload.encode('utf8')).hexdigest()!=self.grant.policy_sha256):
            raise ValueError('Image admission ticket differs from its original policy')
        return payload


class ImageAdmissionPolicy:
    """Read only one fixed private file at gateway startup; later byte changes close new admission until an explicit gateway restart."""
    def __init__(self,root):
        """Malformed/missing/disabled files stay closed with bounded fixed diagnostics, never reflecting private input or creating configuration."""
        self.root=Path(root).resolve();self.path=self.root/'private'/'asset-image-admission.json'
        self.configuration=None;self.raw_sha256=None;self.issue='not_configured'
        try:
            raw=self._read()
            if raw is None:return
            self.raw_sha256=hashlib.sha256(raw).hexdigest()
            decoded=json.loads(raw,object_pairs_hook=unique_fields,parse_constant=invalid_number)
            self.configuration=WorkspaceImageAdmissionConfiguration.model_validate(decoded)
            self.issue='configured' if self.configuration.enabled else 'disabled'
        except (ValueError,TypeError,OSError,RecursionError):self.issue='invalid_configuration';self.configuration=None

    def _read(self):
        """Bound bytes and require the exact private regular file without symlink/ancestor redirection."""
        if self.path.parent.resolve()!=self.path.parent or self.path.resolve()!=self.path or self.path.is_symlink():raise ValueError('Redirected image policy')
        if not self.path.exists():return None
        if not self.path.is_file():raise ValueError('Image policy is not a file')
        with self.path.open('rb') as stream:raw=stream.read(16385)
        if len(raw)>16384:raise ValueError('Image policy exceeds its bounded profile')
        return raw

    def current(self):
        """A changed/removed/replaced policy cannot keep authorizing new public submissions through a cached worker."""
        try:
            raw=self._read()
            return (raw is None and self.raw_sha256 is None or raw is not None and hashlib.sha256(raw).hexdigest()==self.raw_sha256)
        except (ValueError,OSError):return False

    def startup_ticket(self,workspace_id):
        """Keep the startup grant stable for historical reads; this alone is never a current public-submission permit."""
        config=self.configuration
        if config is None or not config.enabled or workspace_id not in config.workspace_ids:return None
        payload=canonical(config.model_dump(mode='json'));grant=WorkspaceImageAdmissionGrant(workspace_id=workspace_id,policy_sha256=hashlib.sha256(payload.encode('utf8')).hexdigest())
        return ImageAdmissionTicket(config,grant)

    def current_ticket(self,workspace_id):
        """Require unchanged explicit operator scope for a fresh image submission; history remains available when new admission closes."""
        return self.startup_ticket(workspace_id) if self.current() else None


def initialize_image_admission_audit(db):
    """Add one immutable WITHOUT ROWID companion table without rewriting original request audits or any user/project data."""
    db.execute('''CREATE TABLE IF NOT EXISTS image_admission_audit(
        request_id TEXT PRIMARY KEY REFERENCES request_audit(id),workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        submitted_by TEXT NOT NULL REFERENCES users(id),policy_sha256 TEXT NOT NULL,
        policy_payload TEXT NOT NULL CHECK(json_valid(policy_payload) AND length(CAST(policy_payload AS BLOB))<=16384),
        CHECK(length(policy_sha256)=64 AND policy_sha256 NOT GLOB '*[^0-9a-f]*')) WITHOUT ROWID''')
    guards={
        'image_admission_original_request':'''BEFORE INSERT ON image_admission_audit WHEN NOT EXISTS(
            SELECT 1 FROM request_audit r WHERE r.id=NEW.request_id AND r.workspace_id=NEW.workspace_id AND r.submitted_by=NEW.submitted_by)
            BEGIN SELECT RAISE(ABORT,'Image admission requires original actor audit'); END''',
        'image_admission_no_replace':'''BEFORE INSERT ON image_admission_audit WHEN EXISTS(SELECT 1 FROM image_admission_audit WHERE request_id=NEW.request_id)
            BEGIN SELECT RAISE(ABORT,'Image admission audit is immutable'); END''',
        'image_admission_no_update':'''BEFORE UPDATE ON image_admission_audit BEGIN SELECT RAISE(ABORT,'Image admission audit is immutable'); END''',
        'image_admission_no_delete':'''BEFORE DELETE ON image_admission_audit BEGIN SELECT RAISE(ABORT,'Image admission audit is retained'); END''',
    }
    for name,body in guards.items():
        expected=f'CREATE TRIGGER {name} {body}';old=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
        if old is None:db.execute(expected)
        elif ' '.join(old[0].strip().rstrip(';').split())!=' '.join(expected.strip().rstrip(';').split()):raise ValueError('Image admission audit guard changed')


def record_image_admission(db,request_id,identity,ticket):
    """Append the exact policy/actor association in the original audit transaction before a gateway can send the image request."""
    if not isinstance(ticket,ImageAdmissionTicket):raise ValueError('Trusted image admission ticket required')
    payload=ticket.checked_payload(identity['workspace_id'])
    original=db.execute('SELECT method,path,workspace_id,submitted_by FROM request_audit WHERE id=?',(request_id,)).fetchone()
    if (original is None or not image_submission_path(original['method'],original['path'])
            or original['workspace_id']!=identity['workspace_id'] or original['submitted_by']!=identity['user']['id']):
        raise ValueError('Original image request/actor required before policy audit')
    db.execute('INSERT INTO image_admission_audit VALUES(?,?,?,?,?)',(request_id,identity['workspace_id'],identity['user']['id'],ticket.grant.policy_sha256,payload))

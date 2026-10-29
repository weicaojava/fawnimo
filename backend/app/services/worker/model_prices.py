"""Read explicit operator prices without loading credentials or enabling any transport."""
from pathlib import Path

from fastapi import HTTPException

from app.core.contracts.model_accounting import ModelPriceConfiguration


class ModelPriceRegistry:
    """Resolve immutable envelopes supplied by trusted setup, never public request fields."""

    def __init__(self,configuration=None,*,issue='not_configured'):
        """Keep invalid/disabled setup inert and expose only fixed non-secret explanations."""
        self.configuration=configuration or ModelPriceConfiguration()
        self.issue=issue
        self.rules={(kind,rule.quote.model_id):rule.quote for rule in self.configuration.rules for kind in rule.kinds}

    def required(self,kind,model_id):
        """Reject unpriced, disabled or wrong-protocol execution before job admission."""
        quote=self.rules.get((kind,model_id)) if self.configuration.enabled else None
        if quote is None or quote.model_name is None:
            raise HTTPException(503,'此模型尚未登记本调用协议的受信费用上界，未创建收费任务')
        return quote

    def confirmed(self,kind,model_id,expected_fingerprint):
        """Require the exact displayed server envelope; a caller fingerprint never supplies an amount."""
        quote=self.required(kind,model_id)
        if quote.fingerprint!=expected_fingerprint:
            raise HTTPException(409,'模型费用配置已变化，请刷新并重新确认本次完整预留')
        return quote


def load_model_prices(filename):
    """Load a bounded server file; no endpoint, executable or credential value is accepted."""
    if not filename:
        return ModelPriceRegistry()
    try:
        path=Path(filename).expanduser()
        if path.is_symlink() or path.resolve()!=path.absolute() or not path.is_file() or path.stat().st_size>524288:
            raise ValueError()
        with path.open('rb') as stream:
            content=stream.read(524289)
        if len(content)>524288:
            raise ValueError()
        configuration=ModelPriceConfiguration.model_validate_json(content)
        return ModelPriceRegistry(configuration,issue='configured' if configuration.enabled else 'disabled')
    except Exception:
        # Parse failures can contain operator file contents in validation errors.
        # They must never flow into API errors, logs or configuration projections.
        return ModelPriceRegistry(issue='invalid_configuration')

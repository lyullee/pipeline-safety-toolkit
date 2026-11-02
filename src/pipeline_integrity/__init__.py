"""Composable pipeline engineering interfaces."""

__version__ = "0.1.0a1"

__all__ = ("create_registry", "evaluate", "adapter_specs", "describe_adapter",
           "run_adapter", "run_adapter_job", "run_campaign", "run_feedback",
           "verify_adapter_job", "verify_campaign", "verify_feedback", "bind_adapter_context",
           "code_implementation_progress", "engineering_method_specs",
           "get_engineering_method_callable", "describe_engineering_method",
           "engineering_method_implementation_progress")


def engineering_method_implementation_progress():
    from .engineering_methods import engineering_method_implementation_progress as inspect
    return inspect()


def engineering_method_specs():
    from .engineering_methods import engineering_method_specs as describe
    return describe()


def get_engineering_method_callable(method_id, api=None):
    from .engineering_methods import get_engineering_method_callable as resolve
    return resolve(method_id, api)


def describe_engineering_method(method_id):
    from .engineering_methods import describe_engineering_method as describe
    return describe(method_id)


def create_registry(*, property_provider=None, provider_id=None, provider_version=None):
    from .builtins import default_registry

    return default_registry(
        property_provider=property_provider, provider_id=provider_id, provider_version=provider_version
    )


def evaluate(model_id, parameters, context):
    return create_registry().evaluate(model_id, parameters, context)


def adapter_specs():
    """Describe allowlisted adapter APIs without loading optional model engines."""
    from .adapter_jobs import adapter_specs as describe
    return describe()


def describe_adapter(adapter_id):
    from .adapter_jobs import describe_adapter as describe
    return describe(adapter_id)


def run_adapter_job(job, *, providers=None, maximum_job_bytes=64_000_000):
    from .adapter_jobs import run_adapter_job as dispatch
    return dispatch(job, providers=providers, maximum_job_bytes=maximum_job_bytes)


def run_adapter(adapter_id, parameters, context, *, providers=None, bindings=None, payloads=None,
                job_id=None, maximum_job_bytes=64_000_000):
    """Evaluate one model with explicit parameter, context and provider boundaries."""
    from .contracts import json_value, digest
    job=dict(adapter_id=adapter_id,parameters=json_value(parameters),context=json_value(context),
             bindings={} if bindings is None else bindings,payloads={} if payloads is None else json_value(payloads))
    job["id"]=job_id if job_id is not None else str(adapter_id)+"/"+digest(job)[:16]
    return run_adapter_job(job,providers=providers,maximum_job_bytes=maximum_job_bytes)


def run_campaign(request, *, providers=None):
    """Run a reviewed feedforward graph using the same model APIs as single jobs."""
    import json
    from .adapter_campaign import AdapterCampaign, run_adapter_campaign
    from .contracts import json_value
    prepared=AdapterCampaign.model_validate_json(json.dumps(json_value(request),allow_nan=False))
    return run_adapter_campaign(prepared,providers=providers)


def run_feedback(request, context, *, providers=None):
    """Run a bounded reviewed fixed-point coupling, retaining every model receipt."""
    import json
    from .adapter_feedback import AdapterFeedbackRequest, evaluate_adapter_feedback
    from .contracts import RunContext, json_value
    prepared=AdapterFeedbackRequest.model_validate_json(json.dumps(json_value(request),allow_nan=False))
    bound=RunContext.model_validate_json(json.dumps(json_value(context),allow_nan=False))
    return evaluate_adapter_feedback(prepared,bound,providers=providers)


def verify_adapter_job(record, *, job=None, providers=None, maximum_job_bytes=64_000_000):
    from .adapter_jobs import verify_adapter_job_record
    return verify_adapter_job_record(record,job=job,providers=providers,maximum_job_bytes=maximum_job_bytes)


def verify_campaign(record, *, request=None, providers=None):
    import json
    from .adapter_campaign import AdapterCampaign, verify_adapter_campaign_record
    from .contracts import json_value
    prepared=None if request is None else AdapterCampaign.model_validate_json(json.dumps(json_value(request),allow_nan=False))
    return verify_adapter_campaign_record(record,request=prepared,providers=providers)


def verify_feedback(record, *, request=None, context=None, providers=None):
    import json
    from .adapter_feedback import AdapterFeedbackRequest, verify_adapter_feedback_record
    from .contracts import RunContext, json_value
    prepared=None if request is None else AdapterFeedbackRequest.model_validate_json(json.dumps(json_value(request),allow_nan=False))
    bound=None if context is None else RunContext.model_validate_json(json.dumps(json_value(context),allow_nan=False))
    return verify_adapter_feedback_record(record,request=prepared,context=bound,providers=providers)


def bind_adapter_context(job, *, review_evidence_id):
    """Prepare a declared model context and return its explicit binding receipt."""
    import json
    from .adapter_jobs import AdapterJob
    from .adapter_campaign import bind_adapter_job_context
    from .contracts import json_value
    prepared=AdapterJob.model_validate_json(json.dumps(json_value(job),allow_nan=False))
    return bind_adapter_job_context(prepared,review_evidence_id=review_evidence_id)


def code_implementation_progress(*, scope="engineering_methods"):
    """Report the fixed engineering-method goal by default.

    The historical capability inventory remains available under an explicit
    scope because its entries have a different granularity and ownership.
    """
    if scope == "engineering_methods":
        return engineering_method_implementation_progress()
    if scope == "capabilities":
        from .code_inventory import code_implementation_progress as inspect
        return inspect()
    raise ValueError("scope must be engineering_methods or capabilities")

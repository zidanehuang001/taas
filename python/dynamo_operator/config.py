from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    nats_url: str = "nats://localhost:4222"
    nats_stream: str = "TAAS_EVENTS"
    operator_k8s_enabled: bool = False
    operator_crd_mode: str = "nvidia_dgd"

    dynamo_namespace: str = "dynamo"
    mock_inference_base_url: str = ""
    dynamo_frontend_service_port: int = 8000

    # NVIDIA DGD CRD coordinates
    nvidia_dgd_group: str = "nvidia.com"
    nvidia_dgd_version: str = "v1alpha1"
    nvidia_dgd_plural: str = "dynamographdeployments"
    nvidia_dgd_runtime_image: str = "nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.0.1"
    nvidia_dgd_planner_image: str = ""
    nvidia_dgd_image_pull_secret_name: str = "ngc-regcred"
    nvidia_dgd_hf_secret_name: str = "hf-token-secret"
    nvidia_hf_model_default: str = "Qwen/Qwen3-0.6B"
    nvidia_frontend_url_template: str = "http://{dgd_name}-frontend.{namespace}.svc.cluster.local:{port}"
    nvidia_resolve_frontend_via_k8s: bool = True
    global_planner_namespace: str = "dynamo-system-gp-ctrl"
    metric_pulling_prometheus_endpoint: str = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"

    postgres_dsn: str = "postgresql://taas:taas@localhost:5432/taas"
    redis_url: str = "redis://localhost:6379"
    log_level: str = "INFO"
    otlp_endpoint: str = ""
    # S3 / object storage for model artifacts
    model_storage_bucket: str = "taas-models"
    model_storage_region: str = "us-east-1"

    class Config:
        env_prefix = "TAAS_"
        env_file = ".env"

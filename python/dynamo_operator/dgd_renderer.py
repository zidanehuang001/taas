"""DynamoGraphDeployment rendering helpers.

This module is intentionally Kubernetes-client-free so UI/operator payloads can
be validated with fast unit tests before the operator applies the CR.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from typing import Any

# NVIDIA validating webhook (vdynamographdeployment): len(metadata.name) + len(serviceKey) <= 45
# for pod naming. Longest key in this stage is "VllmDecodeWorker" (17), so keep DGD names <= 28.
_DGD_NAME_MAX_LEN = 28


def dgd_name_for_deployment(deployment_id: str) -> str:
    """Return a stable short DGD name while preserving the full ID in labels."""
    dep = (deployment_id or "").strip().lower()
    hex_only = dep.replace("-", "")
    if len(hex_only) == 32 and re.fullmatch(r"[0-9a-f]{32}", hex_only):
        suffix = hex_only[:24]
    else:
        suffix = hashlib.sha256(dep.encode()).hexdigest()[:24]
    name = f"dgd-{suffix}"
    if len(name) > _DGD_NAME_MAX_LEN:
        name = name[:_DGD_NAME_MAX_LEN]
    return name


def _hf_model(payload: dict[str, Any], settings: Any) -> str:
    hf_model = (payload.get("hf_model") or payload.get("hf_model_id") or "").strip()
    if hf_model:
        return hf_model

    storage_uri = (payload.get("storage_uri") or "").strip()
    if storage_uri and "://" not in storage_uri and "/" in storage_uri:
        return storage_uri
    return settings.nvidia_hf_model_default


def _int_at_least(payload: dict[str, Any], key: str, default: int, minimum: int = 1) -> int:
    return max(minimum, int(payload.get(key) or default))


def _normalize_extra_arg_key(key: str) -> str:
    key = key.strip()
    if not key:
        return ""
    if key.startswith("--"):
        return key
    return "--" + key.replace("_", "-")


def _append_extra_args(args: list[str], extra_args: Any) -> None:
    if not isinstance(extra_args, dict):
        return
    for raw_key, raw_value in extra_args.items():
        key = _normalize_extra_arg_key(str(raw_key))
        if not key:
            continue
        if isinstance(raw_value, bool):
            if raw_value:
                args.append(key)
            continue
        if raw_value is None or raw_value == "":
            args.append(key)
            continue
        args.extend([key, str(raw_value)])


def _env_list(payload: dict[str, Any]) -> list[dict[str, str]]:
    # On Kubernetes, use native discovery (EndpointSlice + DynamoWorkerMetadata). Without this,
    # vllm-runtime defaults to etcd and fails lease creation against secured platform etcd.
    env: list[dict[str, str]] = [{"name": "DYN_DISCOVERY_BACKEND", "value": "kubernetes"}]
    env_vars = payload.get("env_vars")
    if isinstance(env_vars, dict):
        for key, value in env_vars.items():
            env.append({"name": str(key), "value": str(value)})
    return env


def _runtime_image(payload: dict[str, Any], settings: Any) -> str:
    image = (payload.get("backend_image") or "").strip()
    if image:
        return image
    image = settings.nvidia_dgd_runtime_image.strip()
    if image:
        return image
    return "nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.0.1"


def _setting(settings: Any, name: str, default: Any) -> Any:
    value = getattr(settings, name, default)
    if value is None:
        return default
    return value


def _image_pull_secrets(settings: Any) -> list[dict[str, str]]:
    secret = str(_setting(settings, "nvidia_dgd_image_pull_secret_name", "ngc-regcred")).strip()
    if not secret:
        return []
    return [{"name": secret}]


def _cache_volume() -> dict[str, Any]:
    return {
        "name": "hf-model-cache",
        "hostPath": {"path": "/data/hf-model-cache", "type": "DirectoryOrCreate"},
    }


def _cache_volume_mount() -> dict[str, str]:
    return {
        "name": "hf-model-cache",
        "mountPath": "/home/dynamo/.cache/huggingface/hub",
    }


def _profile_config_map_name(deployment_id: str) -> str:
    return f"planner-profile-data-{dgd_name_for_deployment(deployment_id)}"


def _profile_data(payload: dict[str, Any]) -> dict[str, str]:
    # Synthetic but regression-safe defaults for L20/vLLM smoke validation. Real
    # customer validation should replace these with DGDR/AIPerf profile output.
    prefill = payload.get("profile_prefill_raw_data")
    if not isinstance(prefill, dict):
        prefill = {
            "prefill_isl": [128, 256, 512, 1024, 2048],
            "prefill_ttft": [30, 55, 110, 260, 700],
            "prefill_thpt_per_gpu": [1800, 1450, 980, 520, 240],
        }
    decode = payload.get("profile_decode_raw_data")
    if not isinstance(decode, dict):
        decode = {
            "x_kv_usage": [0.1, 0.1, 0.1, 0.1, 0.3, 0.3, 0.3, 0.3, 0.5, 0.5, 0.5, 0.5, 0.7, 0.7, 0.7, 0.7, 0.9, 0.9, 0.9, 0.9],
            "y_context_length": [128, 512, 1024, 2048, 128, 512, 1024, 2048, 128, 512, 1024, 2048, 128, 512, 1024, 2048, 128, 512, 1024, 2048],
            "z_itl": [28, 24, 22, 20, 34, 30, 27, 24, 43, 37, 33, 29, 55, 47, 41, 36, 72, 61, 52, 45],
            "z_thpt_per_gpu": [720, 650, 560, 420, 660, 580, 500, 360, 560, 500, 420, 300, 460, 400, 320, 230, 340, 290, 230, 160],
            "max_kv_tokens": 32768,
        }
    return {
        "prefill_raw_data.json": json.dumps(prefill, indent=2),
        "decode_raw_data.json": json.dumps(decode, indent=2),
    }


def render_profile_config_map(payload: dict[str, Any], namespace: str) -> dict[str, Any]:
    deployment_id = payload["deployment_id"]
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": _profile_config_map_name(deployment_id),
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "taas-dynamo-operator",
                "taas.io/deployment-id": deployment_id,
            },
        },
        "data": _profile_data(payload),
    }


def _vllm_worker_args(
    *,
    hf_model: str,
    tensor_parallel: int,
    payload: dict[str, Any],
    disaggregation_mode: str | None = None,
    kv_transfer: bool = False,
) -> list[str]:
    args = ["--model", hf_model]
    if tensor_parallel > 1:
        args.extend(["--tensor-parallel-size", str(tensor_parallel)])
    if disaggregation_mode:
        args.extend(["--disaggregation-mode", disaggregation_mode])
    if kv_transfer:
        args.extend(["--kv-transfer-config", '{"kv_connector":"NixlConnector","kv_role":"kv_both"}'])
    dtype = (payload.get("dtype") or "").strip()
    if dtype and dtype != "auto":
        args.extend(["--dtype", dtype])
    max_len = int(payload.get("max_sequence_length") or 0)
    if max_len > 0:
        args.extend(["--max-model-len", str(max_len)])
    if not payload.get("extra_args"):
        args.extend(["--gpu-memory-utilization", "0.80"])
    _append_extra_args(args, payload.get("extra_args"))
    return args


def render_vllm_disagg_dgd_spec(payload: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Render a single-model vLLM disaggregated DGD with Planner + profile ConfigMap."""
    hf_model = _hf_model(payload, settings)
    tensor_parallel = _int_at_least(payload, "tensor_parallel_size", 1)
    prefill_replicas = _int_at_least(payload, "prefill_replicas", 1)
    decode_replicas = _int_at_least(payload, "decode_replicas", 1)
    frontend_replicas = _int_at_least(payload, "frontend_replicas", 1)
    gpu_count = max(_int_at_least(payload, "gpu_count_per_replica", tensor_parallel), tensor_parallel)
    image = _runtime_image(payload, settings)
    env = _env_list(payload)
    secret = (settings.nvidia_dgd_hf_secret_name or "").strip()
    image_pull_secrets = _image_pull_secrets(settings)
    deployment_id = payload["deployment_id"]
    profile_cm = _profile_config_map_name(deployment_id)

    frontend: dict[str, Any] = {
        "componentType": "frontend",
        "replicas": frontend_replicas,
        "envs": [{"name": "DYN_ROUTER_MODE", "value": str(payload.get("router_mode") or "kv")}],
        "extraPodSpec": {
            "imagePullSecrets": image_pull_secrets,
            "mainContainer": {
                "image": image,
                "env": env,
                "workingDir": "/workspace",
                "command": ["python3", "-m", "dynamo.frontend"],
                "args": ["--model-name", hf_model],
            },
        },
    }

    def worker(sub_component: str, replicas: int) -> dict[str, Any]:
        disagg_mode = "prefill" if sub_component == "prefill" else None
        return {
            "componentType": "worker",
            "subComponentType": sub_component,
            "replicas": replicas,
            "resources": {"limits": {"gpu": str(gpu_count)}},
            "extraPodSpec": {
                "imagePullSecrets": image_pull_secrets,
                "volumes": [_cache_volume()],
                "mainContainer": {
                    "image": image,
                    "env": env,
                    "workingDir": "/workspace/examples/backends/vllm",
                    "command": ["python3", "-m", "dynamo.vllm"],
                    "args": _vllm_worker_args(
                        hf_model=hf_model,
                        tensor_parallel=tensor_parallel,
                        payload=payload,
                        disaggregation_mode=disagg_mode,
                        kv_transfer=True,
                    ),
                },
                "volumeMounts": [_cache_volume_mount()],
            },
        }

    planner_config = {
        "environment": "global-planner",
        "global_planner_namespace": str(_setting(settings, "global_planner_namespace", "dynamo-system-gp-ctrl")),
        "backend": "vllm",
        "mode": "disagg",
        "optimization_target": "sla",
        "enable_load_scaling": False,
        "enable_throughput_scaling": True,
        "throughput_metrics_source": "frontend",
        "throughput_adjustment_interval": int(payload.get("throughput_adjustment_interval") or 60),
        "ttft": float(payload.get("target_ttft_ms") or 2000),
        "itl": float(payload.get("target_itl_ms") or 200),
        "max_gpu_budget": -1,
        "prefill_engine_num_gpu": gpu_count,
        "decode_engine_num_gpu": gpu_count,
        "model_name": hf_model,
        "profile_results_dir": "/workspace/profiling_results",
        "metric_pulling_prometheus_endpoint": str(
            _setting(
                settings,
                "metric_pulling_prometheus_endpoint",
                "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090",
            )
        ),
    }
    planner: dict[str, Any] = {
        "componentType": "planner",
        "replicas": 1,
        "extraPodSpec": {
            "imagePullSecrets": image_pull_secrets,
            "volumes": [{"name": profile_cm, "configMap": {"name": profile_cm}}],
            "mainContainer": {
                "image": str(_setting(settings, "nvidia_dgd_planner_image", image)),
                "command": ["python3", "-m", "dynamo.planner"],
                "args": ["--config", json.dumps(planner_config, separators=(",", ":"))],
            },
            "volumeMounts": [
                {
                    "name": profile_cm,
                    "mountPath": "/workspace/profiling_results",
                    "readOnly": True,
                }
            ],
        },
    }

    if secret:
        frontend["envFromSecret"] = secret
    prefill = worker("prefill", prefill_replicas)
    decode = worker("decode", decode_replicas)
    if secret:
        prefill["envFromSecret"] = secret
        decode["envFromSecret"] = secret

    return {
        "backendFramework": "vllm",
        "services": {
            "Frontend": frontend,
            "VllmPrefillWorker": prefill,
            "VllmDecodeWorker": decode,
            "Planner": planner,
        },
    }


def render_vllm_agg_dgd_spec(payload: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Render the current Phase 1 target: one aggregated vLLM Frontend + Worker DGD."""
    deploy_mode = (payload.get("deploy_mode") or "dgd").strip().lower()
    if deploy_mode != "dgd":
        raise ValueError(f"nvidia_dgd renderer currently supports deploy_mode='dgd' only, got {deploy_mode!r}")
    backend = (payload.get("backend") or "vllm").strip().lower()
    if backend != "vllm":
        raise ValueError(f"nvidia_dgd renderer currently supports backend='vllm' only, got {backend!r}")
    if bool(payload.get("disagg_enabled")):
        return render_vllm_disagg_dgd_spec(payload, settings)

    hf_model = _hf_model(payload, settings)
    worker_replicas = _int_at_least(payload, "replicas_min", 1)
    frontend_replicas = _int_at_least(payload, "frontend_replicas", worker_replicas)
    tensor_parallel = _int_at_least(payload, "tensor_parallel_size", 1)
    gpu_count = max(_int_at_least(payload, "gpu_count_per_replica", tensor_parallel), tensor_parallel)
    image = _runtime_image(payload, settings)
    env = _env_list(payload)

    raw_worker_command = payload.get("worker_command")
    has_custom_worker_command = isinstance(raw_worker_command, str) and bool(raw_worker_command.strip())
    if has_custom_worker_command:
        command = shlex.split(raw_worker_command)
        args: list[str] = []
    else:
        command = ["python3", "-m", "dynamo.vllm"]
        args = ["--model", hf_model]

    if not has_custom_worker_command:
        if tensor_parallel > 1:
            args.extend(["--tensor-parallel-size", str(tensor_parallel)])
        dtype = (payload.get("dtype") or "").strip()
        if dtype and dtype != "auto":
            args.extend(["--dtype", dtype])
        max_len = int(payload.get("max_sequence_length") or 0)
        if max_len > 0:
            args.extend(["--max-model-len", str(max_len)])
        _append_extra_args(args, payload.get("extra_args"))

    frontend: dict[str, Any] = {
        "componentType": "frontend",
        "replicas": frontend_replicas,
        "extraPodSpec": {
            "mainContainer": {"image": image, "env": env},
        },
    }
    worker: dict[str, Any] = {
        "componentType": "worker",
        "replicas": worker_replicas,
        "resources": {"limits": {"gpu": str(gpu_count)}},
        "extraPodSpec": {
            "tolerations": [
                {
                    "key": "nvidia.com/gpu",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                }
            ],
            "mainContainer": {
                "image": image,
                "env": env,
                "workingDir": "/workspace/examples/backends/vllm",
                "command": command,
                "args": args,
            },
        },
    }
    secret = (settings.nvidia_dgd_hf_secret_name or "").strip()
    if secret:
        frontend["envFromSecret"] = secret
        worker["envFromSecret"] = secret

    return {
        "backendFramework": "vllm",
        "services": {
            "Frontend": frontend,
            "VllmDecodeWorker": worker,
        },
    }


def render_vllm_agg_dgd(payload: dict[str, Any], settings: Any, namespace: str) -> dict[str, Any]:
    """Render a complete DynamoGraphDeployment object for the operator to apply."""
    deployment_id = payload["deployment_id"]
    name = dgd_name_for_deployment(deployment_id)
    return {
        "apiVersion": f"{settings.nvidia_dgd_group}/{settings.nvidia_dgd_version}",
        "kind": "DynamoGraphDeployment",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "taas-dynamo-operator",
                "taas.io/deployment-id": deployment_id,
            },
        },
        "spec": render_vllm_agg_dgd_spec(payload, settings),
    }

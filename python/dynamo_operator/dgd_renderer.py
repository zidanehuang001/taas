"""DynamoGraphDeployment rendering helpers.

This module is intentionally Kubernetes-client-free so UI/operator payloads can
be validated with fast unit tests before the operator applies the CR.
"""

from __future__ import annotations

import hashlib
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


def render_vllm_agg_dgd_spec(payload: dict[str, Any], settings: Any) -> dict[str, Any]:
    """Render the current Phase 1 target: one aggregated vLLM Frontend + Worker DGD."""
    deploy_mode = (payload.get("deploy_mode") or "dgd").strip().lower()
    if deploy_mode != "dgd":
        raise ValueError(f"nvidia_dgd renderer currently supports deploy_mode='dgd' only, got {deploy_mode!r}")
    backend = (payload.get("backend") or "vllm").strip().lower()
    if backend != "vllm":
        raise ValueError(f"nvidia_dgd renderer currently supports backend='vllm' only, got {backend!r}")
    if bool(payload.get("disagg_enabled")):
        raise ValueError("nvidia_dgd renderer currently supports aggregated single-model DGD only")

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

"""TaaS Dynamo Operator.

Consumes `model.deploy.requested` and either:
- creates NVIDIA DynamoGraphDeployment CRDs (operator_k8s_enabled=true), or
- publishes immediate running status with a mock endpoint (operator_k8s_enabled=false).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

import nats
from fastapi import FastAPI
from nats.aio.msg import Msg
from prometheus_client import Counter, Gauge, make_asgi_app

from .config import Settings
from .k8s_nvidia_dgd import NvidiaDgdClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = Settings()

deployments_total = Counter(
    "taas_operator_deployments_total",
    "Total deployment operations",
    ["operation", "status"],
)
active_deployments = Gauge(
    "taas_operator_active_deployments",
    "Current number of active deployments",
)


def _normalize_inference_endpoint(base_url: str) -> str:
    base = (base_url or "").rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    nc = await nats.connect(settings.nats_url)

    dgd_client: Optional[NvidiaDgdClient] = None
    if settings.operator_k8s_enabled:
        if settings.operator_crd_mode != "nvidia_dgd":
            raise RuntimeError(f"unsupported TAAS_OPERATOR_CRD_MODE={settings.operator_crd_mode}")
        dgd_client = NvidiaDgdClient(settings)

    running_published: set[str] = set()

    async def publish_status(payload: dict) -> None:
        await nc.publish("deployment.status.updated", json.dumps(payload).encode())

    async def on_deploy_requested(msg: Msg) -> None:
        try:
            payload = json.loads(msg.data.decode())
            deployment_id = payload["deployment_id"]
            logger.info("deploy requested", extra={"deployment_id": deployment_id, "payload": payload})

            if not settings.operator_k8s_enabled:
                endpoint = _normalize_inference_endpoint(settings.mock_inference_base_url)
                if not endpoint:
                    endpoint = "http://mock-dynamo:9090/v1"
                await publish_status(
                    {
                        "deployment_id": deployment_id,
                        "status": "running",
                        "endpoint_url": endpoint,
                        "replicas": max(1, int(payload.get("replicas_min") or 1)),
                    }
                )
                deployments_total.labels(operation="deploy", status="running").inc()
                active_deployments.inc()
                return

            assert dgd_client is not None
            await dgd_client.create_from_payload(payload)
            deployments_total.labels(operation="deploy", status="accepted").inc()
        except Exception as exc:
            logger.exception("failed to process model.deploy.requested")
            dep_id = ""
            try:
                dep_id = json.loads(msg.data.decode()).get("deployment_id", "")
            except Exception:
                pass
            if dep_id:
                await publish_status(
                    {
                        "deployment_id": dep_id,
                        "status": "failed",
                        "error_message": str(exc),
                    }
                )
                deployments_total.labels(operation="deploy", status="failed").inc()

    sub = await nc.subscribe("model.deploy.requested", cb=on_deploy_requested)
    watch_task: Optional[asyncio.Task] = None

    if dgd_client is not None:
        async def on_dgd_event(event_type: str, obj: dict) -> None:
            if event_type not in ("ADDED", "MODIFIED"):
                return
            metadata = obj.get("metadata") or {}
            labels = metadata.get("labels") or {}
            status = obj.get("status") or {}
            deployment_id = labels.get("taas.io/deployment-id", "")
            dgd_name = metadata.get("name", "")
            if not deployment_id:
                return

            state = str(status.get("state", "")).lower()
            ready_condition = next(
                (c for c in (status.get("conditions") or []) if c.get("type") == "Ready"),
                None,
            )
            ready = bool(ready_condition and str(ready_condition.get("status", "")).lower() == "true")
            ready_message = (ready_condition or {}).get("message", "")

            is_running = state == "successful" or (state == "pending" and ready)
            is_failed = state == "failed" or (
                ready_condition
                and str(ready_condition.get("status", "")).lower() == "false"
                and ready_condition.get("reason") == "deploy_failed"
            )

            if is_running:
                if deployment_id in running_published:
                    return
                services = (obj.get("spec") or {}).get("services") or {}
                if "VllmPrefillWorker" not in services:
                    try:
                        await dgd_client._ensure_vllm_worker_discovery_service(
                            deployment_id=deployment_id, dgd_name=dgd_name,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to ensure worker discovery Service for %s (Frontend may list 0 backends)",
                            dgd_name,
                        )
                base = await dgd_client.resolve_frontend_base_url(
                    deployment_id=deployment_id,
                    dgd_name=dgd_name,
                )
                endpoint = _normalize_inference_endpoint(base)
                await publish_status(
                    {
                        "deployment_id": deployment_id,
                        "status": "running",
                        "endpoint_url": endpoint,
                        "replicas": 1,
                    }
                )
                running_published.add(deployment_id)
                deployments_total.labels(operation="deploy", status="running").inc()
                active_deployments.inc()
                logger.info(
                    "deployment running",
                    extra={"deployment_id": deployment_id, "dgd_name": dgd_name, "endpoint": endpoint},
                )
                return

            if is_failed:
                await publish_status(
                    {
                        "deployment_id": deployment_id,
                        "status": "failed",
                        "error_message": ready_message or status.get("message", "deployment failed"),
                    }
                )
                deployments_total.labels(operation="deploy", status="failed").inc()
                logger.warning(
                    "deployment failed",
                    extra={"deployment_id": deployment_id, "dgd_name": dgd_name, "message": ready_message},
                )

        watch_task = asyncio.create_task(dgd_client.watch_dgd(on_dgd_event))

    logger.info(
        "Dynamo Operator started",
        extra={
            "namespace": settings.dynamo_namespace,
            "operator_k8s_enabled": settings.operator_k8s_enabled,
            "operator_crd_mode": settings.operator_crd_mode,
        },
    )
    try:
        yield
    finally:
        await sub.unsubscribe()
        if watch_task is not None:
            watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watch_task
        await nc.drain()
        logger.info("Dynamo Operator stopped")


app = FastAPI(title="TaaS Dynamo Operator", version="0.1.0", lifespan=lifespan)
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    return {
        "status": "ready",
        "operator_k8s_enabled": settings.operator_k8s_enabled,
        "namespace": settings.dynamo_namespace,
    }

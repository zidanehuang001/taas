"""
Async Kubernetes client for NVIDIA DynamoGraphDeployment (nvidia.com/v1alpha1).
Aggregated vLLM pattern — no DGDR / no profiling.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Optional

from kubernetes_asyncio import client, config, watch
from kubernetes_asyncio.client.exceptions import ApiException

from .config import Settings
from .dgd_renderer import dgd_name_for_deployment, render_vllm_agg_dgd, render_vllm_agg_dgd_spec

logger = logging.getLogger(__name__)


class NvidiaDgdClient:
    """Creates and watches DynamoGraphDeployment CRs (vLLM agg graph)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._namespace = settings.dynamo_namespace
        self._api: Optional[client.CustomObjectsApi] = None
        self._core_v1: Optional[client.CoreV1Api] = None

    async def _ensure_client(self) -> client.CustomObjectsApi:
        if self._api is None:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                await config.load_kube_config()
            self._api = client.CustomObjectsApi()
        return self._api

    async def _ensure_core_v1(self) -> client.CoreV1Api:
        if self._core_v1 is None:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                await config.load_kube_config()
            self._core_v1 = client.CoreV1Api()
        return self._core_v1

    @staticmethod
    def _pick_http_port(svc: Any, preferred: int) -> int:
        ports = svc.spec.ports or []
        for p in ports:
            if p.port == preferred:
                return int(p.port)
        for p in ports:
            if p.name and "http" in p.name.lower():
                return int(p.port)
        if ports:
            return int(ports[0].port)
        return preferred

    @staticmethod
    def _service_cluster_url(svc_name: str, namespace: str, port: int) -> str:
        return f"http://{svc_name}.{namespace}.svc.cluster.local:{port}"

    async def resolve_frontend_base_url(
        self,
        *,
        deployment_id: str,
        dgd_name: str,
    ) -> str:
        ns = self._namespace
        preferred = self._settings.dynamo_frontend_service_port

        if self._settings.nvidia_resolve_frontend_via_k8s:
            try:
                core = await self._ensure_core_v1()
                lst = await core.list_namespaced_service(
                    namespace=ns,
                    label_selector=f"taas.io/deployment-id={deployment_id}",
                )
                # Only frontend Services are valid endpoints. Worker discovery Services we create
                # also carry taas.io/deployment-id but expose port 9090 (system), not the OpenAI API.
                for svc in lst.items or []:
                    name = svc.metadata.name or ""
                    labels = (svc.metadata.labels or {})
                    if labels.get("taas.io/dgd-worker-discovery") == "true":
                        continue
                    if "frontend" not in name.lower():
                        continue
                    port = self._pick_http_port(svc, preferred)
                    return self._service_cluster_url(name, ns, port)
            except Exception:
                logger.warning(
                    "DGD frontend: service list by label failed, falling back to name/template",
                    exc_info=True,
                )

            cand = f"{dgd_name}-frontend"
            try:
                core = await self._ensure_core_v1()
                svc = await core.read_namespaced_service(name=cand, namespace=ns)
                port = self._pick_http_port(svc, preferred)
                return self._service_cluster_url(cand, ns, port)
            except Exception:
                pass

        base = (dgd_name or "dynamo").strip()
        return self._settings.nvidia_frontend_url_template.format(
            dgd_name=base,
            namespace=ns,
            port=preferred,
        )

    def _build_spec(self, payload: dict) -> dict[str, Any]:
        return render_vllm_agg_dgd_spec(payload, self._settings)

    async def create_from_payload(self, payload: dict) -> dict:
        deployment_id = payload["deployment_id"]
        name = dgd_name_for_deployment(deployment_id)
        api = await self._ensure_client()
        body = render_vllm_agg_dgd(payload, self._settings, self._namespace)
        result = await api.create_namespaced_custom_object(
            group=self._settings.nvidia_dgd_group,
            version=self._settings.nvidia_dgd_version,
            namespace=self._namespace,
            plural=self._settings.nvidia_dgd_plural,
            body=body,
        )
        logger.info("Created DynamoGraphDeployment %s", name)
        try:
            await self._ensure_vllm_worker_discovery_service(deployment_id=deployment_id, dgd_name=name)
        except Exception:
            logger.exception(
                "Failed to ensure worker discovery Service for %s (Frontend may list 0 backends)",
                name,
            )
        return result

    async def _wait_dgd_worker_hash(self, dgd_name: str, timeout_s: float = 120.0) -> str:
        """dynamo-platform sets nvidia.com/current-worker-hash once the worker Deployment is reconciled."""
        api = await self._ensure_client()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            obj = await api.get_namespaced_custom_object(
                group=self._settings.nvidia_dgd_group,
                version=self._settings.nvidia_dgd_version,
                namespace=self._namespace,
                plural=self._settings.nvidia_dgd_plural,
                name=dgd_name,
            )
            ann = (obj.get("metadata") or {}).get("annotations") or {}
            h = (ann.get("nvidia.com/current-worker-hash") or "").strip()
            if h:
                return h
            await asyncio.sleep(2)
        raise TimeoutError(f"timeout waiting for worker hash on DGD {dgd_name}")

    async def _ensure_vllm_worker_discovery_service(self, deployment_id: str, dgd_name: str) -> None:
        """
        dynamo-platform 1.0.x may not create a Service for VllmDecodeWorker. Kubernetes discovery
        requires a ready EndpointSlice plus DynamoWorkerMetadata; without a Service, Frontend sees
        0 backends and /v1/chat/completions returns 404.
        """
        worker_hash = await self._wait_dgd_worker_hash(dgd_name)
        svc_name = f"{dgd_name}-vllmdecodeworker-{worker_hash}"
        dynamo_ns = f"dynamo-{dgd_name}"
        core = await self._ensure_core_v1()
        # ownerReference ties the Service lifecycle to the DGD CR, so when the DGD is deleted
        # the Service is GC'd automatically (and dynamo-platform's namespace sweeper won't kill it).
        api = await self._ensure_client()
        try:
            owner_dgd = await api.get_namespaced_custom_object(
                group=self._settings.nvidia_dgd_group,
                version=self._settings.nvidia_dgd_version,
                namespace=self._namespace,
                plural=self._settings.nvidia_dgd_plural,
                name=dgd_name,
            )
            owner_uid = ((owner_dgd or {}).get("metadata") or {}).get("uid", "")
        except ApiException:
            owner_uid = ""
        metadata: dict[str, Any] = {
            "name": svc_name,
            "namespace": self._namespace,
            "labels": {
                "app.kubernetes.io/managed-by": "taas-dynamo-operator",
                "taas.io/deployment-id": deployment_id,
                "taas.io/dgd-worker-discovery": "true",
                "nvidia.com/dynamo-component": "VllmDecodeWorker",
                "nvidia.com/dynamo-component-type": "worker",
                "nvidia.com/dynamo-graph-deployment-name": dgd_name,
                "nvidia.com/dynamo-namespace": dynamo_ns,
                "nvidia.com/dynamo-discovery-backend": "kubernetes",
                "nvidia.com/dynamo-discovery-enabled": "true",
            },
        }
        if owner_uid:
            metadata["ownerReferences"] = [
                {
                    "apiVersion": f"{self._settings.nvidia_dgd_group}/{self._settings.nvidia_dgd_version}",
                    "kind": "DynamoGraphDeployment",
                    "name": dgd_name,
                    "uid": owner_uid,
                    "controller": False,
                    "blockOwnerDeletion": False,
                }
            ]
        body: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": metadata,
            "spec": {
                "selector": {
                    "nvidia.com/dynamo-component": "VllmDecodeWorker",
                    "nvidia.com/dynamo-component-type": "worker",
                    "nvidia.com/dynamo-namespace": dynamo_ns,
                },
                "ports": [
                    {
                        "name": "system",
                        "port": 9090,
                        "targetPort": "system",
                    }
                ],
            },
        }
        try:
            await core.create_namespaced_service(namespace=self._namespace, body=body)
            logger.info("Created worker discovery Service %s", svc_name)
        except ApiException as e:
            if getattr(e, "status", None) == 409:
                logger.info("Worker discovery Service %s already exists", svc_name)
                return
            raise

    async def _delete_worker_discovery_services(self, deployment_id: str) -> None:
        core = await self._ensure_core_v1()
        try:
            lst = await core.list_namespaced_service(
                namespace=self._namespace,
                label_selector=f"taas.io/deployment-id={deployment_id},taas.io/dgd-worker-discovery=true",
            )
        except ApiException:
            return
        for svc in lst.items or []:
            n = svc.metadata.name
            if not n:
                continue
            try:
                await core.delete_namespaced_service(name=n, namespace=self._namespace)
                logger.info("Deleted worker discovery Service %s", n)
            except ApiException:
                logger.exception("delete worker discovery Service %s", n)

    async def delete_for_deployment_id(self, deployment_id: str) -> None:
        name = dgd_name_for_deployment(deployment_id)
        await self._delete_worker_discovery_services(deployment_id)
        api = await self._ensure_client()
        await api.delete_namespaced_custom_object(
            group=self._settings.nvidia_dgd_group,
            version=self._settings.nvidia_dgd_version,
            namespace=self._namespace,
            plural=self._settings.nvidia_dgd_plural,
            name=name,
        )
        logger.info("Deleted DynamoGraphDeployment %s", name)

    async def watch_dgd(
        self,
        callback: Callable[[str, dict], Coroutine[Any, Any, None]],
    ) -> None:
        api = await self._ensure_client()
        w = watch.Watch()
        while True:
            try:
                async for event in w.stream(
                    api.list_namespaced_custom_object,
                    group=self._settings.nvidia_dgd_group,
                    version=self._settings.nvidia_dgd_version,
                    namespace=self._namespace,
                    plural=self._settings.nvidia_dgd_plural,
                ):
                    event_type: str = event["type"]
                    obj: dict = event["object"]
                    try:
                        await callback(event_type, obj)
                    except Exception:
                        logger.exception(
                            "Error in DGD watch callback for %s",
                            obj.get("metadata", {}).get("name", "?"),
                        )
            except asyncio.CancelledError:
                logger.info("DynamoGraphDeployment watch cancelled")
                raise
            except Exception:
                logger.exception("DGD watch stream error, reconnecting in 5s")
                await asyncio.sleep(5)

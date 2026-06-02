from __future__ import annotations

import unittest
from types import SimpleNamespace

from .dgd_renderer import dgd_name_for_deployment, render_vllm_agg_dgd


class DgdRendererTest(unittest.TestCase):
    def settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            nvidia_dgd_group="nvidia.com",
            nvidia_dgd_version="v1alpha1",
            nvidia_dgd_runtime_image="nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.1.1",
            nvidia_dgd_hf_secret_name="hf-token-secret",
            nvidia_hf_model_default="Qwen/Qwen3-0.6B",
        )

    def test_dgd_name_is_stable_and_short(self) -> None:
        name = dgd_name_for_deployment("7b8c3a9a-1e37-4a44-9f1b-a2c4e8d3f111")

        self.assertEqual(name, "dgd-7b8c3a9a1e374a449f1ba2c4")
        self.assertLessEqual(len(name), 28)

    def test_render_vllm_aggregated_dgd_uses_payload_fields(self) -> None:
        body = render_vllm_agg_dgd(
            {
                "deployment_id": "7b8c3a9a-1e37-4a44-9f1b-a2c4e8d3f111",
                "deploy_mode": "dgd",
                "backend": "vllm",
                "backend_image": "custom/vllm:dev",
                "storage_uri": "Qwen/Qwen3-8B",
                "replicas_min": 2,
                "frontend_replicas": 1,
                "gpu_count_per_replica": 1,
                "tensor_parallel_size": 2,
                "dtype": "float16",
                "max_sequence_length": 8192,
                "env_vars": {"HF_HOME": "/models/cache"},
                "extra_args": {"enable-prefix-caching": True, "gpu_memory_utilization": "0.90"},
            },
            self.settings(),
            "dynamo-system",
        )

        self.assertEqual(body["apiVersion"], "nvidia.com/v1alpha1")
        self.assertEqual(body["kind"], "DynamoGraphDeployment")
        self.assertEqual(body["metadata"]["namespace"], "dynamo-system")
        self.assertEqual(body["metadata"]["labels"]["taas.io/deployment-id"], "7b8c3a9a-1e37-4a44-9f1b-a2c4e8d3f111")

        services = body["spec"]["services"]
        frontend = services["Frontend"]
        worker = services["VllmDecodeWorker"]
        self.assertEqual(frontend["replicas"], 1)
        self.assertEqual(worker["replicas"], 2)
        self.assertEqual(worker["resources"]["limits"]["gpu"], "2")
        self.assertEqual(frontend["envFromSecret"], "hf-token-secret")
        self.assertEqual(worker["envFromSecret"], "hf-token-secret")

        main = worker["extraPodSpec"]["mainContainer"]
        self.assertEqual(main["image"], "custom/vllm:dev")
        self.assertEqual(main["command"], ["python3", "-m", "dynamo.vllm"])
        self.assertEqual(
            main["args"],
            [
                "--model",
                "Qwen/Qwen3-8B",
                "--tensor-parallel-size",
                "2",
                "--dtype",
                "float16",
                "--max-model-len",
                "8192",
                "--enable-prefix-caching",
                "--gpu-memory-utilization",
                "0.90",
            ],
        )
        self.assertIn({"name": "DYN_DISCOVERY_BACKEND", "value": "kubernetes"}, main["env"])
        self.assertIn({"name": "HF_HOME", "value": "/models/cache"}, main["env"])

    def test_rejects_unsupported_backend_for_phase_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "supports backend='vllm' only"):
            render_vllm_agg_dgd(
                {"deployment_id": "dep-1", "deploy_mode": "dgd", "backend": "sglang"},
                self.settings(),
                "dynamo-system",
            )

    def test_rejects_dgdr_for_phase_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "supports deploy_mode='dgd' only"):
            render_vllm_agg_dgd(
                {"deployment_id": "dep-1", "deploy_mode": "dgdr", "backend": "vllm"},
                self.settings(),
                "dynamo-system",
            )

    def test_rejects_disaggregated_dgd_for_phase_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "aggregated single-model DGD only"):
            render_vllm_agg_dgd(
                {"deployment_id": "dep-1", "deploy_mode": "dgd", "backend": "vllm", "disagg_enabled": True},
                self.settings(),
                "dynamo-system",
            )


if __name__ == "__main__":
    unittest.main()

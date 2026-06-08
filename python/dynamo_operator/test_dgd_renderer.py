from __future__ import annotations

import unittest
from types import SimpleNamespace

import json

from .dgd_renderer import dgd_name_for_deployment, render_profile_config_map, render_vllm_agg_dgd


class DgdRendererTest(unittest.TestCase):
    def settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            nvidia_dgd_group="nvidia.com",
            nvidia_dgd_version="v1alpha1",
            nvidia_dgd_runtime_image="nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.1.1",
            nvidia_dgd_hf_secret_name="hf-token-secret",
            nvidia_hf_model_default="Qwen/Qwen3-0.6B",
            global_planner_namespace="dynamo-system-gp-ctrl",
            metric_pulling_prometheus_endpoint="http://prometheus:9090",
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

    def test_render_vllm_disaggregated_dgd_with_planner(self) -> None:
        body = render_vllm_agg_dgd(
            {
                "deployment_id": "7b8c3a9a-1e37-4a44-9f1b-a2c4e8d3f111",
                "deploy_mode": "dgd",
                "backend": "vllm",
                "storage_uri": "Qwen/Qwen3-8B",
                "disagg_enabled": True,
                "prefill_replicas": 1,
                "decode_replicas": 2,
                "frontend_replicas": 1,
                "gpu_count_per_replica": 1,
                "tensor_parallel_size": 1,
                "target_ttft_ms": 2000,
                "target_itl_ms": 200,
            },
            self.settings(),
            "dynamo-system",
        )

        services = body["spec"]["services"]
        self.assertEqual(set(services), {"Frontend", "VllmPrefillWorker", "VllmDecodeWorker", "Planner"})
        self.assertEqual(services["VllmPrefillWorker"]["subComponentType"], "prefill")
        self.assertEqual(services["VllmDecodeWorker"]["subComponentType"], "decode")
        self.assertEqual(services["VllmDecodeWorker"]["replicas"], 2)

        prefill_args = services["VllmPrefillWorker"]["extraPodSpec"]["mainContainer"]["args"]
        decode_args = services["VllmDecodeWorker"]["extraPodSpec"]["mainContainer"]["args"]
        self.assertIn("--disaggregation-mode", prefill_args)
        self.assertIn("prefill", prefill_args)
        self.assertNotIn("--disaggregation-mode", decode_args)
        self.assertIn("--kv-transfer-config", decode_args)

        planner = services["Planner"]["extraPodSpec"]
        planner_args = planner["mainContainer"]["args"]
        self.assertEqual(planner_args[0], "--config")
        cfg = json.loads(planner_args[1])
        self.assertEqual(cfg["optimization_target"], "sla")
        self.assertEqual(cfg["backend"], "vllm")
        self.assertEqual(cfg["mode"], "disagg")
        self.assertEqual(cfg["throughput_metrics_source"], "frontend")
        self.assertEqual(cfg["profile_results_dir"], "/workspace/profiling_results")
        self.assertEqual(cfg["global_planner_namespace"], "dynamo-system-gp-ctrl")
        self.assertEqual(planner["volumes"][0]["configMap"]["name"], "planner-profile-data-dgd-7b8c3a9a1e374a449f1ba2c4")

    def test_render_profile_config_map_for_disaggregated_planner(self) -> None:
        cm = render_profile_config_map(
            {
                "deployment_id": "7b8c3a9a-1e37-4a44-9f1b-a2c4e8d3f111",
                "profile_prefill_raw_data": {"prefill_isl": [128], "prefill_ttft": [10], "prefill_thpt_per_gpu": [100]},
                "profile_decode_raw_data": {
                    "x_kv_usage": [0.1],
                    "y_context_length": [128],
                    "z_itl": [20],
                    "z_thpt_per_gpu": [100],
                    "max_kv_tokens": 32768,
                },
            },
            "dynamo-system",
        )

        self.assertEqual(cm["kind"], "ConfigMap")
        self.assertEqual(cm["metadata"]["namespace"], "dynamo-system")
        self.assertEqual(cm["metadata"]["name"], "planner-profile-data-dgd-7b8c3a9a1e374a449f1ba2c4")
        self.assertIn("prefill_raw_data.json", cm["data"])
        self.assertIn("decode_raw_data.json", cm["data"])
        self.assertEqual(json.loads(cm["data"]["decode_raw_data.json"])["z_itl"], [20])


if __name__ == "__main__":
    unittest.main()

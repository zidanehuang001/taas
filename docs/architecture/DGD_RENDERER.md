# Dynamo DGD Renderer

This branch starts with the smallest useful Dynamo integration surface:

UI deploy form -> Gateway deploy event -> Python operator -> one NVIDIA
`DynamoGraphDeployment` for a single vLLM model.

## Phase 1 Scope

Supported now:

- `deploy_mode: "dgd"`
- `backend: "vllm"`
- aggregated serving: `Frontend` + `VllmDecodeWorker`
- single-model disaggregated serving: `Frontend` + `VllmPrefillWorker` +
  `VllmDecodeWorker` + `Planner`
- planner profile ConfigMap creation for disaggregated serving
- Hugging Face model source from `hf_model`, `hf_model_id`, or `storage_uri`
- runtime image override via `backend_image`
- worker and frontend replica counts
- GPU count and tensor parallel size
- dtype, max model length, environment variables, and extra vLLM args
- Kubernetes discovery via `DYN_DISCOVERY_BACKEND=kubernetes`

Intentionally not in Phase 1:

- DGDR / AIConfigurator profiling
- SGLang or TensorRT-LLM rendering
- shared GlobalPlanner across multiple models
- multi-pool GlobalRouter / LocalRouter topology
- Grove / KAI scheduling hints
- token economics dashboards

Those are separate phases after the single-model DGD path is proven end to end.

## Code Path

- `web/src/components/DeployForm.tsx` collects deployment fields.
- `internal/model/deployment_events.go` publishes `model.deploy.requested`.
- `python/dynamo_operator/dgd_renderer.py` renders the DGD body.
- `python/dynamo_operator/k8s_nvidia_dgd.py` applies the rendered body and
  watches DGD status.

The renderer is intentionally independent of Kubernetes client libraries so it
can be unit-tested without a cluster.

## Local Checks

```bash
python3 -m unittest python.dynamo_operator.test_dgd_renderer
PYTHONPYCACHEPREFIX=/private/tmp/taas-ui-pycache python3 -m py_compile \
  python/dynamo_operator/dgd_renderer.py \
  python/dynamo_operator/test_dgd_renderer.py
```

## Cluster Validation

Use a DGD direct deploy first. A successful Phase 1 validation means:

1. UI creates a model and submits a DGD deployment.
2. Gateway publishes `model.deploy.requested`.
3. Operator creates one profile ConfigMap when disaggregated serving is enabled.
4. Operator creates one `DynamoGraphDeployment`.
5. Dynamo controller creates Frontend, vLLM worker, and Planner pods.
6. Operator observes Ready and publishes `deployment.status.updated`.
7. Gateway stores the running endpoint and registers it in LiteLLM.
8. An OpenAI-compatible request succeeds through LiteLLM.

If this path works, the next branch can add shared GlobalPlanner namespace
management, multi-model single-pool autoscaling, and richer rollout status
display.

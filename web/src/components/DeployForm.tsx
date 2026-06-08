import { useState } from 'react';
import type { DeploymentConfig } from '../api/client';

const GPU_OPTIONS = ['h200_sxm', 'h100_sxm', 'h20', 'l20', 'a100_sxm', 'a10g', 'l40s'];
const TP_OPTIONS = [1, 2, 4, 8];
const PP_OPTIONS = [1, 2, 4];

interface Props {
  modelName: string;
  modelSlug: string;
  onSubmit: (config: DeploymentConfig) => void;
  onCancel: () => void;
  isSubmitting: boolean;
}

const defaultConfig: DeploymentConfig = {
  name: '',
  deploy_mode: 'dgd',
  sla_tier: 'standard',
  backend: 'vllm',
  gpu_type: 'l20',
  gpu_count_per_replica: 1,
  num_gpus_per_node: 8,
  replicas_min: 1,
  replicas_max: 1,
  tensor_parallel_size: 1,
  pipeline_parallel_size: 1,
  input_sequence_length: 2048,
  output_sequence_length: 256,
  target_ttft_ms: 2000,
  target_itl_ms: 200,
  search_strategy: 'rapid',
  disagg_enabled: true,
  prefill_replicas: 1,
  decode_replicas: 1,
  frontend_replicas: 1,
  router_mode: 'kv',
  max_batch_size: 32,
  max_sequence_length: 4096,
  dtype: 'auto',
};

export default function DeployForm({ modelName, modelSlug, onSubmit, onCancel, isSubmitting }: Props) {
  const [config, setConfig] = useState<DeploymentConfig>({
    ...defaultConfig,
    name: `${modelSlug}-prod`,
  });
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [envRows, setEnvRows] = useState<Array<{ key: string; value: string }>>([]);
  const [extraArgRows, setExtraArgRows] = useState<Array<{ key: string; value: string }>>([]);

  const isDGDR = config.deploy_mode === 'dgdr';
  const isDGD = config.deploy_mode === 'dgd';

  const set = <K extends keyof DeploymentConfig>(key: K, value: DeploymentConfig[K]) =>
    setConfig((prev) => ({ ...prev, [key]: value }));

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const finalConfig = { ...config };
    if (envRows.length > 0) {
      finalConfig.env_vars = Object.fromEntries(envRows.filter((r) => r.key).map((r) => [r.key, r.value]));
    }
    if (extraArgRows.length > 0) {
      finalConfig.extra_args = Object.fromEntries(extraArgRows.filter((r) => r.key).map((r) => [r.key, r.value]));
    }
    onSubmit(finalConfig);
  };

  return (
    <form onSubmit={handleSubmit}>
      <div className="card" style={{ padding: 24, marginBottom: 24 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 4 }}>Deploy {modelName}</h2>
        <p className="text-muted" style={{ fontSize: 13, marginBottom: 20 }}>
          Configure NVIDIA Dynamo deployment for this model.
        </p>

        {/* ── Mode Toggle ─────────────────────────────── */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 24 }}>
          <ModeCard
            icon="🔬"
            title="Auto-Optimized (DGDR)"
            subtitle="SLA-driven profiling finds optimal config. Slower, but optimal."
            active={isDGDR}
            onClick={() => set('deploy_mode', 'dgdr')}
          />
          <ModeCard
            icon="⚡"
            title="Direct Deploy (DGD)"
            subtitle="Explicit config, no profiling, instant deployment."
            active={isDGD}
            onClick={() => set('deploy_mode', 'dgd')}
          />
        </div>

        {/* ── Name ────────────────────────────────────── */}
        <FormSection title="Deployment Name">
          <input
            type="text"
            className="form-input"
            value={config.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="e.g., llama-3-prod"
            required
          />
        </FormSection>

        {/* ── Inference Engine ────────────────────────── */}
        <FormSection title="Inference Engine">
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            {(['vllm', 'sglang', 'trtllm'] as const).map((b) => (
              <label
                key={b}
                className="form-radio-card"
                style={{
                  flex: 1,
                  padding: '10px 16px',
                  border: `2px solid ${config.backend === b ? 'var(--primary)' : 'var(--border)'}`,
                  borderRadius: 8,
                  cursor: 'pointer',
                  textAlign: 'center',
                  background: config.backend === b ? 'var(--primary-bg, rgba(99,102,241,0.08))' : 'transparent',
                }}
              >
                <input
                  type="radio"
                  name="backend"
                  value={b}
                  checked={config.backend === b}
                  onChange={() => set('backend', b)}
                  style={{ display: 'none' }}
                />
                <div style={{ fontWeight: 600, fontSize: 14 }}>
                  {b === 'vllm' ? 'vLLM' : b === 'sglang' ? 'SGLang' : 'TensorRT-LLM'}
                </div>
              </label>
            ))}
          </div>
          <input
            type="text"
            className="form-input"
            value={config.backend_image ?? ''}
            onChange={(e) => set('backend_image', e.target.value || undefined)}
            placeholder="Container image override (optional)"
            style={{ fontSize: 13 }}
          />
        </FormSection>

        {/* ── Hardware ────────────────────────────────── */}
        <FormSection title="Hardware">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            <FormField label="GPU Type">
              <select className="form-input" value={config.gpu_type} onChange={(e) => set('gpu_type', e.target.value)}>
                {GPU_OPTIONS.map((g) => <option key={g} value={g}>{g}</option>)}
              </select>
            </FormField>
            <FormField label="GPUs per replica">
              <input type="number" className="form-input" min={1} max={16}
                value={config.gpu_count_per_replica} onChange={(e) => set('gpu_count_per_replica', +e.target.value)} />
            </FormField>
            {isDGDR && (
              <FormField label="GPUs per node">
                <input type="number" className="form-input" min={1} max={16}
                  value={config.num_gpus_per_node} onChange={(e) => set('num_gpus_per_node', +e.target.value)} />
              </FormField>
            )}
          </div>
        </FormSection>

        {/* ── Parallelism ────────────────────────────── */}
        <FormSection title="Parallelism">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <FormField label="Tensor Parallel (TP)">
              <div style={{ display: 'flex', gap: 4 }}>
                {TP_OPTIONS.map((v) => (
                  <button key={v} type="button"
                    className={`btn btn-sm ${config.tensor_parallel_size === v ? 'btn-primary' : ''}`}
                    onClick={() => set('tensor_parallel_size', v)}>{v}</button>
                ))}
              </div>
            </FormField>
            <FormField label="Pipeline Parallel (PP)">
              <div style={{ display: 'flex', gap: 4 }}>
                {PP_OPTIONS.map((v) => (
                  <button key={v} type="button"
                    className={`btn btn-sm ${config.pipeline_parallel_size === v ? 'btn-primary' : ''}`}
                    onClick={() => set('pipeline_parallel_size', v)}>{v}</button>
                ))}
              </div>
            </FormField>
          </div>
        </FormSection>

        {/* ── Scaling ────────────────────────────────── */}
        <FormSection title="Scaling">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <FormField label="Min replicas">
              <input type="number" className="form-input" min={1} max={32}
                value={config.replicas_min} onChange={(e) => set('replicas_min', +e.target.value)} />
            </FormField>
            <FormField label="Max replicas">
              <input type="number" className="form-input" min={1} max={32}
                value={config.replicas_max} onChange={(e) => set('replicas_max', +e.target.value)} />
            </FormField>
          </div>
        </FormSection>

        {/* ── SLA Targets (DGDR only) ────────────────── */}
        {isDGDR && (
          <FormSection title="SLA Targets & Workload Profile">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
              <FormField label="TTFT target (ms)">
                <input type="number" className="form-input" min={0} step={10}
                  value={config.target_ttft_ms} onChange={(e) => set('target_ttft_ms', +e.target.value)} />
              </FormField>
              <FormField label="ITL target (ms)">
                <input type="number" className="form-input" min={0} step={1}
                  value={config.target_itl_ms} onChange={(e) => set('target_itl_ms', +e.target.value)} />
              </FormField>
              <FormField label="Input seq length">
                <input type="number" className="form-input" min={1}
                  value={config.input_sequence_length} onChange={(e) => set('input_sequence_length', +e.target.value)} />
              </FormField>
              <FormField label="Output seq length">
                <input type="number" className="form-input" min={1}
                  value={config.output_sequence_length} onChange={(e) => set('output_sequence_length', +e.target.value)} />
              </FormField>
            </div>
            <FormField label="Search strategy">
              <div style={{ display: 'flex', gap: 8 }}>
                {(['rapid', 'thorough'] as const).map((s) => (
                  <label key={s} style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                    <input type="radio" name="search" checked={config.search_strategy === s}
                      onChange={() => set('search_strategy', s)} />
                    <span style={{ fontSize: 14 }}>{s === 'rapid' ? '⚡ Rapid' : '🔬 Thorough'}</span>
                  </label>
                ))}
              </div>
            </FormField>
          </FormSection>
        )}

        {/* ── Disaggregated Serving ──────────────────── */}
        <FormSection title="Disaggregated Serving (Prefill/Decode Separation)">
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginBottom: 12 }}>
            <input type="checkbox" checked={config.disagg_enabled}
              onChange={(e) => set('disagg_enabled', e.target.checked)} />
            <span style={{ fontWeight: 500, fontSize: 14 }}>Enable prefill/decode separation</span>
          </label>
          {config.disagg_enabled && (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <FormField label="Prefill workers">
                <input type="number" className="form-input" min={1} max={16}
                  value={config.prefill_replicas} onChange={(e) => set('prefill_replicas', +e.target.value)} />
              </FormField>
              <FormField label="Decode workers">
                <input type="number" className="form-input" min={1} max={16}
                  value={config.decode_replicas} onChange={(e) => set('decode_replicas', +e.target.value)} />
              </FormField>
            </div>
          )}
        </FormSection>

        {/* ── DGD Options (DGD only) ─────────────────── */}
        {isDGD && (
          <FormSection title="DGD Direct Deploy Options">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
              <FormField label="Frontend replicas">
                <input type="number" className="form-input" min={1}
                  value={config.frontend_replicas} onChange={(e) => set('frontend_replicas', +e.target.value)} />
              </FormField>
              <FormField label="Router mode">
                <select className="form-input" value={config.router_mode}
                  onChange={(e) => set('router_mode', e.target.value as 'random' | 'kv')}>
                  <option value="random">random</option>
                  <option value="kv">kv (KV-aware)</option>
                </select>
              </FormField>
              <FormField label="Dynamo namespace">
                <input type="text" className="form-input"
                  value={config.dynamo_namespace ?? ''} placeholder="auto"
                  onChange={(e) => set('dynamo_namespace', e.target.value || undefined)} />
              </FormField>
            </div>
            <FormField label="Worker command override">
              <textarea className="form-input" rows={3}
                style={{ fontFamily: 'monospace', fontSize: 13 }}
                value={config.worker_command ?? ''}
                placeholder="Leave empty for auto-generated command based on backend + model"
                onChange={(e) => set('worker_command', e.target.value || undefined)} />
            </FormField>
            <FormField label="Environment variables">
              <KVEditor rows={envRows} onChange={setEnvRows} keyPlaceholder="ENV_NAME" valuePlaceholder="value" />
            </FormField>
          </FormSection>
        )}

        {/* ── Advanced ───────────────────────────────── */}
        <div style={{ marginBottom: 16 }}>
          <button type="button" className="btn btn-sm"
            onClick={() => setShowAdvanced(!showAdvanced)}
            style={{ fontSize: 13 }}>
            {showAdvanced ? '▼' : '▶'} Advanced settings
          </button>
        </div>
        {showAdvanced && (
          <FormSection title="Advanced">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 12 }}>
              <FormField label="Data type">
                <select className="form-input" value={config.dtype ?? 'auto'}
                  onChange={(e) => set('dtype', e.target.value)}>
                  {['auto', 'fp16', 'bf16', 'fp8'].map((d) => <option key={d} value={d}>{d}</option>)}
                </select>
              </FormField>
              <FormField label="Max batch size">
                <input type="number" className="form-input" min={1}
                  value={config.max_batch_size} onChange={(e) => set('max_batch_size', +e.target.value)} />
              </FormField>
              <FormField label="Max sequence length">
                <input type="number" className="form-input" min={1}
                  value={config.max_sequence_length} onChange={(e) => set('max_sequence_length', +e.target.value)} />
              </FormField>
            </div>
            <FormField label="Extra backend args">
              <KVEditor rows={extraArgRows} onChange={setExtraArgRows} keyPlaceholder="--flag" valuePlaceholder="value" />
            </FormField>
          </FormSection>
        )}

        {/* ── YAML Preview ───────────────────────────── */}
        <FormSection title="Preview">
          <pre style={{
            background: 'var(--bg-secondary, #1e1e2e)',
            color: 'var(--text-mono, #cdd6f4)',
            padding: 16, borderRadius: 8, fontSize: 12,
            fontFamily: 'monospace', overflow: 'auto', maxHeight: 300,
            border: '1px solid var(--border)',
          }}>
            {generatePreview(config, modelSlug)}
          </pre>
        </FormSection>

        {/* ── Actions ────────────────────────────────── */}
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 24 }}>
          <button type="button" className="btn" onClick={onCancel}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={isSubmitting || !config.name}>
            {isSubmitting ? 'Deploying…' : isDGDR ? '🔬 Deploy with Profiling' : '⚡ Deploy Now'}
          </button>
        </div>
      </div>
    </form>
  );
}

/* ── Sub-components ──────────────────────────────────────────── */

function ModeCard({ icon, title, subtitle, active, onClick }: {
  icon: string; title: string; subtitle: string; active: boolean; onClick: () => void;
}) {
  return (
    <div
      onClick={onClick}
      style={{
        border: `2px solid ${active ? 'var(--primary)' : 'var(--border)'}`,
        borderRadius: 12,
        padding: '16px 20px',
        cursor: 'pointer',
        background: active ? 'var(--primary-bg, rgba(99,102,241,0.08))' : 'transparent',
        transition: 'all 0.15s ease',
      }}
    >
      <div style={{ fontSize: 24, marginBottom: 4 }}>{icon}</div>
      <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 2 }}>{title}</div>
      <div className="text-muted" style={{ fontSize: 12 }}>{subtitle}</div>
    </div>
  );
}

function FormSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset style={{ border: 'none', padding: 0, marginBottom: 20 }}>
      <legend style={{ fontWeight: 600, fontSize: 14, marginBottom: 8, color: 'var(--text)' }}>{title}</legend>
      {children}
    </fieldset>
  );
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 4 }}>
      <label className="text-muted" style={{ display: 'block', fontSize: 12, marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  );
}

function KVEditor({ rows, onChange, keyPlaceholder, valuePlaceholder }: {
  rows: Array<{ key: string; value: string }>;
  onChange: (rows: Array<{ key: string; value: string }>) => void;
  keyPlaceholder: string; valuePlaceholder: string;
}) {
  const update = (idx: number, field: 'key' | 'value', val: string) => {
    const next = [...rows];
    next[idx] = { ...next[idx], [field]: val };
    onChange(next);
  };
  const add = () => onChange([...rows, { key: '', value: '' }]);
  const remove = (idx: number) => onChange(rows.filter((_, i) => i !== idx));

  return (
    <div>
      {rows.map((row, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
          <input type="text" className="form-input" placeholder={keyPlaceholder}
            value={row.key} onChange={(e) => update(i, 'key', e.target.value)} style={{ flex: 1 }} />
          <input type="text" className="form-input" placeholder={valuePlaceholder}
            value={row.value} onChange={(e) => update(i, 'value', e.target.value)} style={{ flex: 2 }} />
          <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(i)}>✕</button>
        </div>
      ))}
      <button type="button" className="btn btn-sm" onClick={add} style={{ fontSize: 12 }}>+ Add</button>
    </div>
  );
}

/* ── YAML Preview Generator ──────────────────────────────────── */

function generatePreview(config: DeploymentConfig, modelSlug: string): string {
  if (config.deploy_mode === 'dgdr') {
    const lines = [
      `apiVersion: nvidia.com/v1beta1`,
      `kind: DynamoGraphDeploymentRequest`,
      `metadata:`,
      `  name: ${config.name}`,
      `spec:`,
      `  model: ${modelSlug}`,
      `  backend: ${config.backend}`,
    ];
    if (config.backend_image) lines.push(`  image: "${config.backend_image}"`);
    if (config.search_strategy) lines.push(`  searchStrategy: ${config.search_strategy}`);
    lines.push(`  hardware:`);
    lines.push(`    gpuSku: ${config.gpu_type}`);
    if (config.num_gpus_per_node) lines.push(`    numGpusPerNode: ${config.num_gpus_per_node}`);
    lines.push(`  workload:`);
    lines.push(`    isl: ${config.input_sequence_length ?? 4096}`);
    lines.push(`    osl: ${config.output_sequence_length ?? 512}`);
    lines.push(`  sla:`);
    if (config.target_ttft_ms) lines.push(`    ttft: ${config.target_ttft_ms}`);
    if (config.target_itl_ms) lines.push(`    itl: ${config.target_itl_ms}`);
    lines.push(`  autoApply: true`);
    return lines.join('\n');
  }

  // DGD preview
  const ns = config.dynamo_namespace || `taas-${config.name}`;
  const profileCm = `planner-profile-data-${config.name}`;
  const lines = [
    ...(config.disagg_enabled ? [
      `apiVersion: v1`,
      `kind: ConfigMap`,
      `metadata:`,
      `  name: ${profileCm}`,
      `  namespace: ${ns}`,
      `data:`,
      `  prefill_raw_data.json: |`,
      `    { ... }`,
      `  decode_raw_data.json: |`,
      `    { ... }`,
      `---`,
    ] : []),
    `apiVersion: nvidia.com/v1alpha1`,
    `kind: DynamoGraphDeployment`,
    `metadata:`,
    `  name: ${config.name}`,
    `spec:`,
    `  services:`,
    `    Frontend:`,
    `      dynamoNamespace: ${ns}`,
    `      componentType: frontend`,
    `      replicas: ${config.frontend_replicas ?? 1}`,
    `      envs:`,
    `        DYN_ROUTER_MODE: "${config.router_mode ?? 'kv'}"`,
  ];

  if (config.disagg_enabled) {
    lines.push(`    VllmPrefillWorker:`);
    lines.push(`      dynamoNamespace: ${ns}`);
    lines.push(`      componentType: worker`);
    lines.push(`      subComponentType: prefill`);
    lines.push(`      replicas: ${config.prefill_replicas ?? 1}`);
    lines.push(`      resources:`);
    lines.push(`        limits:`);
    lines.push(`          gpu: "${config.gpu_count_per_replica ?? 1}"`);
    lines.push(`      args:`);
    lines.push(`        - python3 -m dynamo.${config.backend} --model ${modelSlug} --disaggregation-mode prefill`);
    lines.push(`    VllmDecodeWorker:`);
    lines.push(`      dynamoNamespace: ${ns}`);
    lines.push(`      componentType: worker`);
    lines.push(`      subComponentType: decode`);
    lines.push(`      replicas: ${config.decode_replicas ?? 1}`);
    lines.push(`      resources:`);
    lines.push(`        limits:`);
    lines.push(`          gpu: "${config.gpu_count_per_replica ?? 1}"`);
    lines.push(`      args:`);
    lines.push(`        - python3 -m dynamo.${config.backend} --model ${modelSlug}`);
    lines.push(`    Planner:`);
    lines.push(`      dynamoNamespace: ${ns}`);
    lines.push(`      componentType: planner`);
    lines.push(`      replicas: 1`);
    lines.push(`      profileConfigMap: ${profileCm}`);
  } else {
    lines.push(`    Worker:`);
    lines.push(`      dynamoNamespace: ${ns}`);
    lines.push(`      componentType: worker`);
    lines.push(`      replicas: ${config.replicas_min ?? 1}`);
    lines.push(`      resources:`);
    lines.push(`        limits:`);
    lines.push(`          gpu: "${config.gpu_count_per_replica ?? 1}"`);
    lines.push(`      args:`);
    const tp = config.tensor_parallel_size && config.tensor_parallel_size > 1 ? ` --tp ${config.tensor_parallel_size}` : '';
    lines.push(`        - python3 -m dynamo.${config.backend} --model ${modelSlug}${tp}`);
  }

  return lines.join('\n');
}

"use strict";
(() => {
  const payloadNode = document.getElementById("builder-payload");
  const payload = JSON.parse(payloadNode.value);
  const clone = (value) => (
    typeof structuredClone === "function"
      ? structuredClone(value)
      : JSON.parse(JSON.stringify(value))
  );
  const byKey = new Map(payload.plugins.map((plugin) => [plugin.key, plugin]));
  const builderRules = globalThis.MolCascadeBuilderRules;
  if (!builderRules) throw new Error("MolCascade builder rules failed to load.");
  const layers = new Map(payload.layers.map((layer) => [layer.slot, layer]));
  const layerOrder = new Map(payload.layers.map((layer, index) => [layer.slot, index]));
  const alternativesBySlot = new Map();
  (payload.alternatives || []).forEach((alternative) => {
    const options = alternativesBySlot.get(alternative.slot) || [];
    options.push(alternative);
    alternativesBySlot.set(alternative.slot, options);
  });

  let pipeline = clone(payload.default_pipeline);
  let selectedStageId = pipeline.stages[0]?.id || null;
  let selectedEdge = null;
  let paletteQuery = "";
  let paletteFilter = "all";
  let paletteDrag = null;
  let nodeDrag = null;
  let panDrag = null;
  let pendingConnection = null;
  let fieldCounter = 0;
  let renderFrame = null;
  let toastTimer = null;

  const palette = document.getElementById("palette");
  const canvas = document.getElementById("pipeline-canvas");
  const scene = document.getElementById("graph-scene");
  const edgeLayer = document.getElementById("edge-layer");
  const previewPath = document.getElementById("connection-preview");
  const nodeLayer = document.getElementById("node-layer");
  const tierGuides = document.getElementById("tier-guides");
  const emptyState = document.getElementById("canvas-empty");
  const inspector = document.getElementById("inspector-body");
  const errors = document.getElementById("errors");
  const nameInput = document.getElementById("pipeline-name");
  const targetInput = document.getElementById("final-target");
  const stageCount = document.getElementById("stage-count");
  const branchCount = document.getElementById("branch-count");
  const modal = document.getElementById("preview-modal");
  const preview = document.getElementById("config-preview");
  const search = document.getElementById("plugin-search");
  const filter = document.getElementById("palette-filter");
  const modeSelect = document.getElementById("tier-mode");
  const modeTierName = document.getElementById("mode-tier-name");
  const zoomLevel = document.getElementById("zoom-level");
  const connectorToast = document.getElementById("connector-toast");

  const TIER_PADDING_X = 28;
  const TIER_PADDING_Y = 52;
  const TIER_GAP = 84;
  const FALLBACK_NODE_WIDTH = 238;
  const FALLBACK_NODE_HEIGHT = 150;
  const CO_PRODUCED_EVIDENCE_GATES = new Set([
    "prediction.numeric_evidence_gate@0.1.0",
    "synthesis.numeric_evidence_gate@0.1.0",
  ]);

  const styleSheet = [...document.styleSheets].find((sheet) => (
    sheet.ownerNode?.tagName === "STYLE"
  ));
  const dynamicRules = new Map();
  const nodeElements = new Map();

  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const svgElement = (tag, className = "") => {
    const node = document.createElementNS("http:" + "//www.w3.org/2000/svg", tag);
    if (className) node.setAttribute("class", className);
    return node;
  };

  function dynamicRule(key, selector) {
    if (dynamicRules.has(key)) return dynamicRules.get(key);
    const index = styleSheet.cssRules.length;
    styleSheet.insertRule(`${selector} {}`, index);
    const rule = styleSheet.cssRules[index];
    dynamicRules.set(key, rule);
    return rule;
  }

  function setRuleProperties(key, selector, properties) {
    const rule = dynamicRule(key, selector);
    Object.entries(properties).forEach(([name, value]) => {
      rule.style.setProperty(name, value);
    });
  }

  const safeIdentifierPart = (value) => {
    const cleaned = String(value)
      .replace(/[^A-Za-z0-9_.-]+/g, "_")
      .replace(/^[^A-Za-z]+/, "");
    return cleaned || "input";
  };

  const humanize = (value) => String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toLocaleUpperCase());

  function parameterLabel(plugin, name, definition) {
    const overrides = {
      maximum_rule_of_five_violations: "Maximum Rule of Five violations",
      minimum_qed: "Minimum QED",
      failure_action: "Failure action",
      mode: "Combination mode",
      pains_action: "PAINS action",
      brenk_action: "BRENK action",
      nih_action: "NIH action",
      zinc_action: "ZINC action",
      min_pass_count: "Minimum passing branches",
      reference_path: "Lead / reference file",
      reference_sha256: "Pinned reference SHA-256",
      objective: "Similarity objective",
      analogue_min_similarity: "Minimum lead similarity",
      novelty_max_similarity: "Maximum lead similarity",
      models_dir: "Verified local models directory",
      expected_model_manifest_sha256: "Model-tree SHA-256 (required)",
      expected_package_code_sha256: "Installed package-code SHA-256 (required)",
      allow_unsafe_model_deserialization: "Trust verified Torch checkpoints",
    };
    const label = overrides[name] || definition.title || humanize(name);
    return definition.required && !/\brequired\b/i.test(label)
      ? `${label} (required)`
      : label;
  }

  function parameterHelp(plugin, name, definition) {
    const overrides = {
      maximum_rule_of_five_violations: "Leave blank to annotate without a Rule of Five cutoff.",
      minimum_qed: "Leave blank to annotate without a QED cutoff; QED is not an efficacy score.",
      failure_action: "WARN records threshold failures; REJECT removes them at this branch.",
      mode: "Parallel (ALL) sets this to All Required. Advanced policies remain editable here.",
      min_pass_count: "Used only by the advanced Min Pass Count policy.",
      pains_action: "PAINS matches are triage flags and default to WARN, not rejection.",
      reference_path: "Local CSV, TSV, or SMI file containing the curated lead/reference set.",
      reference_sha256: "Optional but recommended: pin the exact reference bytes for reproducibility.",
      objective: "Annotate, retain analogues above a minimum, or retain novel molecules below a maximum.",
      analogue_min_similarity: "Required only for the Analogue objective.",
      novelty_max_similarity: "Required only for the Novel objective; lower values demand more uniqueness.",
      models_dir: "Inspect this local directory with MolCascade before loading any checkpoint.",
      expected_model_manifest_sha256: "Required: copy model_manifest_sha256 from inspect_admet_ai_model_assets().",
      expected_package_code_sha256: "Required: copy package_code_sha256 from inspect_admet_ai_installation().",
      allow_unsafe_model_deserialization: "Enable only after reviewing and pinning the exact package and model bytes. Imported model code is not sandboxed.",
    };
    return overrides[name] || definition.description || "";
  }

  const stageById = (stageId) => pipeline.stages.find((stage) => stage.id === stageId);
  const pluginFor = (stage) => byKey.get(stage?.plugin);
  const enabledStages = () => pipeline.stages.filter((stage) => stage.enabled !== false);
  const pluginOptions = (slot) => {
    const layer = layers.get(slot);
    return layer ? payload.plugins.filter((plugin) => plugin.kind === layer.kind) : [];
  };
  const isPolicyJoin = (stage) => Boolean(pluginFor(stage)?.is_policy_join);
  const requiresCoProducedEvidence = (stage) => (
    CO_PRODUCED_EVIDENCE_GATES.has(pluginFor(stage)?.key)
  );
  const outputContract = (stage, port) => pluginFor(stage)?.output_ports?.[port];
  const hasParentOutput = (stage) => Object.values(pluginFor(stage)?.output_ports || {})
    .includes("parent/v1");
  const hasDecisionOutput = (stage) => Object.values(pluginFor(stage)?.output_ports || {})
    .includes("decision/v1");
  const acceptsParent = (stage) => (pluginFor(stage)?.input_contracts || [])
    .includes("parent/v1");
  const isDecisionCriterion = (stage) => (
    !isPolicyJoin(stage) && hasParentOutput(stage) && hasDecisionOutput(stage) && acceptsParent(stage)
  );

  function defaultConfig(plugin) {
    return clone(plugin?.default_config || {});
  }

  function ensureBuilderMetadata() {
    pipeline.metadata = pipeline.metadata || {};
    let layout = pipeline.metadata.builder_layout;
    if (!layout || typeof layout !== "object" || Array.isArray(layout)) {
      layout = {};
      pipeline.metadata.builder_layout = layout;
    }
    layout.schema_version = 2;
    if (!layout.viewport || typeof layout.viewport !== "object") {
      layout.viewport = { x: 32, y: 72, zoom: 0.76 };
    }
    if (!layout.tier_modes || typeof layout.tier_modes !== "object") {
      layout.tier_modes = {};
    }
    payload.layers.forEach((layer) => {
      if (!["parallel_all", "serial"].includes(layout.tier_modes[layer.slot])) {
        layout.tier_modes[layer.slot] = layer.slot === "hard_gate" ? "parallel_all" : "serial";
      }
      const criterionCount = pipeline.stages.filter((stage) => (
        stage.enabled !== false && stage.slot === layer.slot && isDecisionCriterion(stage)
      )).length;
      layout.tier_modes[layer.slot] = builderRules.effectiveTierMode(
        layout.tier_modes[layer.slot],
        criterionCount,
      );
    });
    if (!layout.nodes || typeof layout.nodes !== "object") layout.nodes = {};
    pipeline.stages.forEach((stage, index) => {
      let position = layout.nodes[stage.id];
      if (!position || typeof position !== "object") {
        const slotIndex = layerOrder.get(stage.slot) ?? index;
        position = { x: 90 + slotIndex * 310, y: 140 + (index % 3) * 180 };
        layout.nodes[stage.id] = position;
      }
      position.x = Number.isFinite(Number(position.x)) ? Number(position.x) : 90 + index * 280;
      position.y = Number.isFinite(Number(position.y)) ? Number(position.y) : 140;
      position.tier = stage.slot;
      position.mode = layout.tier_modes[stage.slot] || "serial";
    });
    Object.keys(layout.nodes).forEach((stageId) => {
      if (!stageById(stageId)) delete layout.nodes[stageId];
    });
    return layout;
  }

  function layoutFor(stageId) {
    return ensureBuilderMetadata().nodes[stageId];
  }

  function tierMode(slot) {
    return ensureBuilderMetadata().tier_modes[slot] || "serial";
  }

  function setTierModeMetadata(slot, mode) {
    const layout = ensureBuilderMetadata();
    layout.tier_modes[slot] = mode;
    pipeline.stages.filter((stage) => stage.slot === slot).forEach((stage) => {
      layout.nodes[stage.id].tier = slot;
      layout.nodes[stage.id].mode = mode;
    });
  }

  function viewState() {
    return ensureBuilderMetadata().viewport;
  }

  function updateSceneTransform() {
    const view = viewState();
    view.zoom = Math.min(1.8, Math.max(0.24, Number(view.zoom) || 1));
    view.x = Number(view.x) || 0;
    view.y = Number(view.y) || 0;
    setRuleProperties("scene-transform", ".graph-scene", {
      transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})`,
    });
    zoomLevel.textContent = `${Math.round(view.zoom * 100)}%`;
  }

  function setNodePositionRule(index, position) {
    setRuleProperties(`node-${index}`, `.graph-node[data-layout-index="${index}"]`, {
      left: `${Math.round(position.x)}px`,
      top: `${Math.round(position.y)}px`,
    });
  }

  function uniqueId(base) {
    const used = new Set(pipeline.stages.map((stage) => stage.id));
    const root = safeIdentifierPart(base);
    let candidate = root;
    let suffix = 2;
    while (used.has(candidate)) candidate = `${root}_${suffix++}`;
    return candidate;
  }

  function uniqueRequestPort(stage, base) {
    const used = new Set((stage.inputs || []).map((binding) => binding.request_port));
    const root = safeIdentifierPart(base);
    let candidate = root;
    let suffix = 2;
    while (used.has(candidate)) candidate = `${root}_${suffix++}`;
    return candidate;
  }

  function topologicalOrder(stages = pipeline.stages) {
    const ids = new Set(stages.map((stage) => stage.id));
    const original = new Map(stages.map((stage, index) => [stage.id, index]));
    const indegree = new Map(stages.map((stage) => [stage.id, 0]));
    const outgoing = new Map(stages.map((stage) => [stage.id, []]));
    stages.forEach((stage) => {
      new Set((stage.inputs || []).map((binding) => binding.stage)).forEach((sourceId) => {
        if (!ids.has(sourceId)) return;
        indegree.set(stage.id, (indegree.get(stage.id) || 0) + 1);
        outgoing.get(sourceId).push(stage.id);
      });
    });
    const queue = stages
      .filter((stage) => indegree.get(stage.id) === 0)
      .sort((left, right) => original.get(left.id) - original.get(right.id));
    const ordered = [];
    while (queue.length) {
      const stage = queue.shift();
      ordered.push(stage);
      outgoing.get(stage.id).forEach((targetId) => {
        indegree.set(targetId, indegree.get(targetId) - 1);
        if (indegree.get(targetId) === 0) {
          queue.push(stageById(targetId));
          queue.sort((left, right) => original.get(left.id) - original.get(right.id));
        }
      });
    }
    return ordered.length === stages.length ? ordered : null;
  }

  function sortPipelineTopologically() {
    const ordered = topologicalOrder();
    if (!ordered) return false;
    pipeline.stages = ordered;
    return true;
  }

  function wouldCreateCycle(sourceId, targetId) {
    if (sourceId === targetId) return true;
    const outgoing = new Map(pipeline.stages.map((stage) => [stage.id, []]));
    pipeline.stages.forEach((stage) => (stage.inputs || []).forEach((binding) => {
      outgoing.get(binding.stage)?.push(stage.id);
    }));
    const pending = [targetId];
    const seen = new Set();
    while (pending.length) {
      const current = pending.pop();
      if (current === sourceId) return true;
      if (seen.has(current)) continue;
      seen.add(current);
      pending.push(...(outgoing.get(current) || []));
    }
    return false;
  }

  function occupiedTierOrders() {
    return [...new Set(enabledStages().map((stage) => layerOrder.get(stage.slot)))]
      .filter((order) => Number.isInteger(order))
      .sort((left, right) => left - right);
  }

  function stageConnectionDecision(source, port, target) {
    const targetPlugin = pluginFor(target);
    return builderRules.connectionDecision({
      sourceId: source?.id,
      targetId: target?.id,
      sourceSlot: source?.slot,
      targetSlot: target?.slot,
      sourceOrder: layerOrder.get(source?.slot),
      targetOrder: layerOrder.get(target?.slot),
      contract: outputContract(source, port),
      acceptedContracts: targetPlugin?.input_contracts || [],
      occupiedTierOrders: occupiedTierOrders(),
      targetTierMode: tierMode(target?.slot),
      sourceIsPolicy: isPolicyJoin(source),
      targetIsPolicy: isPolicyJoin(target),
      sourceIsCriterion: isDecisionCriterion(source),
      targetIsCriterion: isDecisionCriterion(target),
      requireSameTierEvidence: requiresCoProducedEvidence(target),
      createsCycle: source && target ? wouldCreateCycle(source.id, target.id) : false,
    });
  }

  function showConnectorToast(message, success = false) {
    if (toastTimer !== null) clearTimeout(toastTimer);
    connectorToast.textContent = message;
    connectorToast.classList.toggle("success", success);
    connectorToast.hidden = false;
    toastTimer = setTimeout(() => {
      connectorToast.hidden = true;
      toastTimer = null;
    }, success ? 2200 : 5200);
  }

  function clearPortCompatibility() {
    document.querySelectorAll(".input-port").forEach((port) => {
      port.classList.remove("accepts-connection", "rejects-connection");
      port.removeAttribute("data-connection-reason");
      port.title = `Connect an accepted input to ${port.dataset.inputStage}`;
    });
  }

  function showPortCompatibility(source, port) {
    document.querySelectorAll(".input-port").forEach((input) => {
      const target = stageById(input.dataset.inputStage);
      const decision = stageConnectionDecision(source, port, target);
      input.classList.add(decision.allowed ? "accepts-connection" : "rejects-connection");
      input.dataset.connectionReason = decision.reason;
      input.title = decision.reason;
    });
  }

  function compatibleOutputs(stage, downstreamPlugin) {
    if (!stage || !downstreamPlugin) return [];
    const upstream = pluginFor(stage);
    return Object.entries(upstream?.output_ports || {})
      .filter(([, contract]) => downstreamPlugin.input_contracts.includes(contract))
      .map(([port, contract]) => ({ port, contract }));
  }

  function compatibleSourceStages(target, plugin) {
    return pipeline.stages
      .filter((candidate) => (
        candidate.id !== target.id
        && candidate.enabled !== false
      ))
      .map((candidate) => ({
        stage: candidate,
        outputs: compatibleOutputs(candidate, plugin).filter((output) => (
          stageConnectionDecision(candidate, output.port, target).allowed
        )),
      }))
      .filter((candidate) => candidate.outputs.length);
  }

  function bindingsFromOneSource(target, source, outputs) {
    const plugin = pluginFor(target);
    const outputByContract = new Map();
    outputs.forEach((output) => {
      if (!outputByContract.has(output.contract)) outputByContract.set(output.contract, output);
    });
    const usedRequestPorts = new Set();
    const requestPort = (base) => {
      const root = safeIdentifierPart(base);
      let candidate = root;
      let suffix = 2;
      while (usedRequestPorts.has(candidate)) candidate = `${root}_${suffix++}`;
      usedRequestPorts.add(candidate);
      return candidate;
    };
    return [...new Set(plugin?.input_contracts || [])].flatMap((contract) => {
      const output = outputByContract.get(contract);
      if (!output) return [];
      return [{
        request_port: requestPort(contract === "parent/v1" ? "primary" : output.port),
        stage: source.id,
        port: output.port,
      }];
    });
  }

  function parentBindingFrom(stage) {
    return (stage.inputs || []).find((binding) => (
      outputContract(stageById(binding.stage), binding.port) === "parent/v1"
    ));
  }

  function findTierAnchor(criteria, slot) {
    const blocked = new Set([
      ...criteria.map((stage) => stage.id),
      ...pipeline.stages.filter((stage) => stage.slot === slot && isPolicyJoin(stage)).map((stage) => stage.id),
    ]);
    for (const criterion of criteria) {
      const binding = parentBindingFrom(criterion);
      const source = stageById(binding?.stage);
      if (
        binding
        && !blocked.has(binding.stage)
        && stageConnectionDecision(source, binding.port, criterion).allowed
      ) return clone(binding);
    }
    const targetOrder = layerOrder.get(slot) ?? payload.layers.length;
    const candidates = pipeline.stages.flatMap((stage) => {
      if (
        blocked.has(stage.id)
        || stage.enabled === false
        || !hasParentOutput(stage)
        || (layerOrder.get(stage.slot) ?? -1) > targetOrder
      ) return [];
      const port = Object.entries(pluginFor(stage).output_ports)
        .find(([, contract]) => contract === "parent/v1")?.[0];
      if (!port || !stageConnectionDecision(stage, port, criteria[0]).allowed) return [];
      return [{ stage, port }];
    });
    const selected = candidates.at(-1);
    return selected
      ? { request_port: "primary", stage: selected.stage.id, port: selected.port }
      : null;
  }

  function setParentInput(stage, sourceBinding, requestPort = "primary") {
    const retained = (stage.inputs || []).filter((binding) => (
      outputContract(stageById(binding.stage), binding.port) !== "parent/v1"
    ));
    retained.push({
      request_port: requestPort,
      stage: sourceBinding.stage,
      port: sourceBinding.port,
    });
    stage.inputs = retained;
  }

  function decisionPort(stage) {
    return Object.entries(pluginFor(stage)?.output_ports || {})
      .find(([, contract]) => contract === "decision/v1")?.[0] || null;
  }

  function primaryParentPort(stage) {
    return Object.entries(pluginFor(stage)?.output_ports || {})
      .find(([, contract]) => contract === "parent/v1")?.[0] || "primary";
  }

  function criteriaForTier(slot) {
    return pipeline.stages
      .filter((stage) => stage.slot === slot && stage.enabled !== false && isDecisionCriterion(stage))
      .sort((left, right) => {
        const a = layoutFor(left.id);
        const b = layoutFor(right.id);
        return a.x - b.x || a.y - b.y || left.id.localeCompare(right.id);
      });
  }

  function rewireExternalSources(oldSourceIds, replacementStage) {
    const oldIds = new Set(oldSourceIds);
    pipeline.stages.forEach((stage) => {
      if (oldIds.has(stage.id)) return;
      (stage.inputs || []).forEach((binding) => {
        if (!oldIds.has(binding.stage)) return;
        const oldContract = outputContract(stageById(binding.stage), binding.port);
        if (oldContract !== "parent/v1") return;
        binding.stage = replacementStage.id;
        binding.port = primaryParentPort(replacementStage);
      });
    });
  }

  function removeJoinStages(slot, replacement = null) {
    const joins = pipeline.stages.filter((stage) => stage.slot === slot && isPolicyJoin(stage));
    if (replacement) rewireExternalSources(joins.map((stage) => stage.id), replacement);
    const ids = new Set(joins.map((stage) => stage.id));
    pipeline.stages = pipeline.stages.filter((stage) => !ids.has(stage.id));
    joins.forEach((stage) => delete ensureBuilderMetadata().nodes[stage.id]);
    return joins;
  }

  function ensureJoinStage(slot, criteria) {
    let join = pipeline.stages.find((stage) => stage.slot === slot && isPolicyJoin(stage));
    if (join) return join;
    const joinPlugin = payload.plugins.find((plugin) => plugin.is_policy_join && plugin.selectable);
    if (!joinPlugin) return null;
    join = {
      id: uniqueId(`${slot}_all_policy`),
      slot,
      plugin: joinPlugin.key,
      inputs: [],
      config: defaultConfig(joinPlugin),
      enabled: true,
    };
    const layout = ensureBuilderMetadata();
    const positions = criteria.map((stage) => layout.nodes[stage.id]);
    layout.nodes[join.id] = {
      x: Math.max(...positions.map((position) => position.x)) + 280,
      y: positions.reduce((sum, position) => sum + position.y, 0) / positions.length,
      tier: slot,
      mode: "parallel_all",
    };
    pipeline.stages.push(join);
    return join;
  }

  function applyTierMode(slot, mode, { arrange = true } = {}) {
    if (!["parallel_all", "serial"].includes(mode)) return false;
    const criteria = criteriaForTier(slot);
    const effectiveMode = builderRules.effectiveTierMode(mode, criteria.length);
    setTierModeMetadata(slot, effectiveMode);
    const existingJoins = pipeline.stages.filter((stage) => stage.slot === slot && isPolicyJoin(stage));
    const priorExit = existingJoins.at(-1) || criteria.at(-1) || null;
    if (!criteria.length) {
      removeJoinStages(slot);
      if (arrange) arrangeAllNodes();
      render();
      return true;
    }
    const anchor = findTierAnchor(criteria, slot);
    if (!anchor) {
      render();
      return false;
    }

    if (effectiveMode === "serial") {
      let upstream = anchor;
      criteria.forEach((criterion) => {
        setParentInput(criterion, upstream);
        upstream = {
          request_port: "primary",
          stage: criterion.id,
          port: primaryParentPort(criterion),
        };
      });
      const last = criteria.at(-1);
      removeJoinStages(slot, last);
      if (priorExit && priorExit.id !== last.id && !isPolicyJoin(priorExit)) {
        rewireExternalSources([priorExit.id], last);
      }
    } else {
      criteria.forEach((criterion) => setParentInput(criterion, anchor));
      const join = ensureJoinStage(slot, criteria);
      if (!join) {
        render();
        return false;
      }
      join.config = { ...defaultConfig(pluginFor(join)), mode: "all_required", min_pass_count: null };
      join.inputs = [{ request_port: "parents", stage: anchor.stage, port: anchor.port }];
      criteria.forEach((criterion) => {
        const port = decisionPort(criterion);
        if (!port) return;
        join.inputs.push({
          request_port: uniqueRequestPort(join, `decision_${criterion.id}`),
          stage: criterion.id,
          port,
        });
      });
      const priorIds = [
        ...existingJoins.map((stage) => stage.id),
        ...(priorExit && !isPolicyJoin(priorExit) ? [priorExit.id] : []),
      ].filter((id) => id !== join.id);
      rewireExternalSources(priorIds, join);
      pipeline.stages = pipeline.stages.filter((stage) => (
        !isPolicyJoin(stage) || stage.slot !== slot || stage.id === join.id
      ));
    }
    sortPipelineTopologically();
    if (arrange) arrangeAllNodes();
    render();
    return true;
  }

  function autoWire(stage) {
    const plugin = pluginFor(stage);
    if (!plugin || plugin.kind === "source") {
      stage.inputs = [];
      return;
    }
    if (plugin.is_policy_join) {
      applyTierMode(stage.slot, "parallel_all", { arrange: false });
      return;
    }
    const candidates = compatibleSourceStages(stage, plugin);
    const preferred = candidates
      .filter(({ stage: source }) => (layerOrder.get(source.slot) ?? -1) <= (layerOrder.get(stage.slot) ?? 999))
      .at(-1) || candidates.at(-1);
    if (!preferred) {
      stage.inputs = [];
      return;
    }
    stage.inputs = bindingsFromOneSource(
      stage,
      preferred.stage,
      preferred.outputs,
    );
    sortPipelineTopologically();
  }

  function visibleCanvasCenter() {
    const rect = canvas.getBoundingClientRect();
    return screenToWorld(rect.left + rect.width / 2, rect.top + rect.height / 2);
  }

  function addPlugin(pluginKey, slot, position = null) {
    const plugin = byKey.get(pluginKey);
    const layer = layers.get(slot);
    if (!plugin || !layer || !plugin.selectable) return;
    if (plugin.kind !== layer.kind && !plugin.is_policy_join) return;
    const drop = position || visibleCanvasCenter();

    if (plugin.kind === "source") {
      const source = pipeline.stages.find((stage) => pluginFor(stage)?.kind === "source");
      if (source) {
        source.plugin = plugin.key;
        source.config = defaultConfig(plugin);
        source.inputs = [];
        source.enabled = true;
        const layout = layoutFor(source.id);
        layout.x = Math.max(20, drop.x);
        layout.y = Math.max(60, drop.y);
        selectedStageId = source.id;
        render();
        return;
      }
    }

    const stage = {
      id: uniqueId(layer.default_id || plugin.id || slot),
      slot,
      plugin: plugin.key,
      inputs: [],
      config: defaultConfig(plugin),
      enabled: true,
    };
    pipeline.stages.push(stage);
    ensureBuilderMetadata().nodes[stage.id] = {
      x: Math.max(20, Math.min(4680, drop.x - 119)),
      y: Math.max(70, Math.min(2020, drop.y - 65)),
      tier: slot,
      mode: tierMode(slot),
    };
    autoWire(stage);
    selectedStageId = stage.id;
    selectedEdge = null;
    if (isDecisionCriterion(stage)) {
      applyTierMode(slot, tierMode(slot), { arrange: false });
    } else {
      render();
    }
  }

  function removeStage(stageId) {
    const stage = stageById(stageId);
    if (!stage) return;
    const slot = stage.slot;
    const wasCriterion = isDecisionCriterion(stage);
    const wasJoin = isPolicyJoin(stage);
    pipeline.stages = pipeline.stages.filter((candidate) => candidate.id !== stageId);
    pipeline.stages.forEach((candidate) => {
      candidate.inputs = (candidate.inputs || []).filter((binding) => binding.stage !== stageId);
    });
    delete ensureBuilderMetadata().nodes[stageId];
    selectedStageId = pipeline.stages[0]?.id || null;
    if (wasJoin) setTierModeMetadata(slot, "serial");
    if (wasCriterion && tierMode(slot) === "parallel_all") {
      applyTierMode(slot, "parallel_all", { arrange: false });
    } else {
      render();
    }
  }

  function renameStage(stage, replacement) {
    const oldId = stage.id;
    stage.id = replacement;
    pipeline.stages.forEach((candidate) => (candidate.inputs || []).forEach((binding) => {
      if (binding.stage === oldId) binding.stage = replacement;
    }));
    const layout = ensureBuilderMetadata();
    layout.nodes[replacement] = layout.nodes[oldId];
    delete layout.nodes[oldId];
    selectedStageId = replacement;
  }

  function connectOutput(sourceId, port, targetId) {
    const source = stageById(sourceId);
    const target = stageById(targetId);
    const targetPlugin = pluginFor(target);
    const contract = outputContract(source, port);
    if (!source || !target || !contract || !targetPlugin) {
      return { allowed: false, reason: "The requested connection endpoint no longer exists." };
    }
    const permission = stageConnectionDecision(source, port, target);
    if (!permission.allowed) return permission;

    const previousInputs = clone(target.inputs || []);
    target.inputs = target.inputs || [];
    if (isPolicyJoin(target)) {
      if (contract === "parent/v1") {
        target.inputs = target.inputs.filter((binding) => (
          outputContract(stageById(binding.stage), binding.port) !== "parent/v1"
        ));
        target.inputs.push({ request_port: "parents", stage: sourceId, port });
      } else {
        if (target.inputs.some((binding) => binding.stage === sourceId && binding.port === port)) {
          return permission;
        }
        target.inputs.push({
          request_port: uniqueRequestPort(target, `decision_${sourceId}`),
          stage: sourceId,
          port,
        });
      }
    } else if (requiresCoProducedEvidence(target)) {
      const compatible = compatibleOutputs(source, targetPlugin).filter((output) => (
        stageConnectionDecision(source, output.port, target).allowed
      ));
      const required = new Set(targetPlugin.input_contracts || []);
      const available = new Set(compatible.map((output) => output.contract));
      if ([...required].every((requiredContract) => available.has(requiredContract))) {
        target.inputs = bindingsFromOneSource(target, source, compatible);
      } else {
        target.inputs = target.inputs.filter((binding) => (
          outputContract(stageById(binding.stage), binding.port) !== contract
        ));
        target.inputs.push({
          request_port: uniqueRequestPort(target, contract === "parent/v1" ? "primary" : contract.split("/")[0]),
          stage: sourceId,
          port,
        });
      }
    } else {
      target.inputs = target.inputs.filter((binding) => (
        outputContract(stageById(binding.stage), binding.port) !== contract
      ));
      target.inputs.push({
        request_port: uniqueRequestPort(target, contract === "parent/v1" ? "primary" : contract.split("/")[0]),
        stage: sourceId,
        port,
      });
    }
    if (!sortPipelineTopologically()) {
      target.inputs = previousInputs;
      return { allowed: false, reason: "This connection would make the graph cyclic." };
    }
    render();
    return permission;
  }

  function removeBinding(targetId, bindingIndex) {
    const target = stageById(targetId);
    if (!target) return;
    target.inputs.splice(bindingIndex, 1);
    selectedEdge = null;
    render();
  }

  function tierGeometryGroups() {
    const grouped = new Map();
    pipeline.stages.forEach((stage) => {
      const position = layoutFor(stage.id);
      const node = nodeElements.get(stage.id);
      const boxes = grouped.get(stage.slot) || [];
      boxes.push({
        id: stage.id,
        x: position.x,
        y: position.y,
        width: node?.offsetWidth || FALLBACK_NODE_WIDTH,
        height: node?.offsetHeight || FALLBACK_NODE_HEIGHT,
      });
      grouped.set(stage.slot, boxes);
    });
    return [...grouped.entries()]
      .sort(([left], [right]) => (layerOrder.get(left) ?? 99) - (layerOrder.get(right) ?? 99))
      .map(([slot, boxes]) => ({ slot, boxes }));
  }

  function normalizeTierLanes() {
    const allocations = builderRules.allocateNonOverlappingLanes(tierGeometryGroups(), {
      paddingX: TIER_PADDING_X,
      paddingY: TIER_PADDING_Y,
      gap: TIER_GAP,
    });
    let shifted = false;
    allocations.forEach((allocation) => {
      if (!allocation.shiftX) return;
      shifted = true;
      pipeline.stages.filter((stage) => stage.slot === allocation.slot).forEach((stage) => {
        layoutFor(stage.id).x += allocation.shiftX;
      });
    });
    if (shifted) {
      pipeline.stages.forEach((stage, index) => setNodePositionRule(index, layoutFor(stage.id)));
    }
    return allocations;
  }

  function arrangeAllNodes() {
    const ordered = topologicalOrder() || pipeline.stages;
    const slots = [...new Set(ordered.map((stage) => stage.slot))]
      .sort((left, right) => (layerOrder.get(left) ?? 99) - (layerOrder.get(right) ?? 99));
    slots.forEach((slot) => {
      const stages = ordered.filter((stage) => stage.slot === slot);
      const criteria = stages.filter((stage) => isDecisionCriterion(stage));
      const joins = stages.filter((stage) => isPolicyJoin(stage));
      const others = stages.filter((stage) => !isDecisionCriterion(stage) && !isPolicyJoin(stage));
      if (tierMode(slot) === "parallel_all" && criteria.length > 1) {
        criteria.forEach((stage, index) => {
          Object.assign(layoutFor(stage.id), { x: 90, y: 140 + index * 190 });
        });
        joins.forEach((stage, index) => {
          Object.assign(layoutFor(stage.id), {
            x: 390 + index * 290,
            y: 140 + (criteria.length - 1) * 95,
          });
        });
        others.forEach((stage, index) => {
          Object.assign(layoutFor(stage.id), { x: 680 + index * 290, y: 140 });
        });
      } else {
        stages.forEach((stage, index) => {
          Object.assign(layoutFor(stage.id), { x: 90 + index * 290, y: 140 });
        });
      }
      stages.forEach((stage) => {
        const position = layoutFor(stage.id);
        position.tier = slot;
        position.mode = tierMode(slot);
      });
    });
    renderScene();
  }

  function fitView() {
    const stages = pipeline.stages.filter((stage) => stage.enabled !== false);
    if (!stages.length) return;
    const positions = stages.map((stage) => layoutFor(stage.id));
    const minX = Math.min(...positions.map((position) => position.x)) - 65;
    const minY = Math.min(...positions.map((position) => position.y)) - 80;
    const maxX = Math.max(...positions.map((position) => position.x)) + 305;
    const maxY = Math.max(...positions.map((position) => position.y)) + 205;
    const rect = canvas.getBoundingClientRect();
    const zoom = Math.min(1.12, Math.max(0.24, Math.min(
      (rect.width - 50) / Math.max(1, maxX - minX),
      (rect.height - 50) / Math.max(1, maxY - minY),
    )));
    const view = viewState();
    view.zoom = zoom;
    view.x = (rect.width - (maxX - minX) * zoom) / 2 - minX * zoom;
    view.y = (rect.height - (maxY - minY) * zoom) / 2 - minY * zoom;
    updateSceneTransform();
    scheduleConnections();
  }

  function resetView() {
    const view = viewState();
    view.x = -30;
    view.y = 80;
    view.zoom = 0.70;
    updateSceneTransform();
    scheduleConnections();
  }

  function screenToWorld(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const view = viewState();
    return {
      x: (clientX - rect.left - view.x) / view.zoom,
      y: (clientY - rect.top - view.y) / view.zoom,
    };
  }

  function componentBadge(plugin) {
    if (plugin.is_policy_join) return "Policy join";
    if (plugin.kind === "gate") return "Criterion";
    if (Object.keys(plugin.output_ports || {}).length > 1) return "Evidence";
    return "Module";
  }

  function searchableText(item) {
    return [
      item.display_name,
      item.description,
      item.id,
      item.backend_id,
      item.license_spdx,
      ...(item.input_contracts || []),
      ...Object.values(item.output_ports || {}),
    ].filter(Boolean).join(" ").toLocaleLowerCase();
  }

  function renderExecutableCard(plugin, layer) {
    const card = element("article", `component-card${plugin.is_policy_join ? " join-component" : ""}`);
    card.dataset.slot = layer.slot;
    card.dataset.plugin = plugin.key;
    card.draggable = plugin.selectable;
    const top = element("div", "component-topline");
    top.append(
      element("span", "component-kind", componentBadge(plugin)),
      element("span", `availability ${plugin.availability}`, plugin.availability_label),
    );
    card.append(top, element("h4", "", plugin.display_name));
    card.append(element("p", "", plugin.description || "No description supplied."));
    if (plugin.science_note) card.append(element("div", "component-science", plugin.science_note));
    const footer = element("div", "component-footer");
    footer.append(element("span", "license", plugin.license_spdx));
    const add = element("button", "mini-button", plugin.selectable ? "Add" : "Unavailable");
    add.type = "button";
    add.disabled = !plugin.selectable;
    add.setAttribute("aria-label", `Add ${plugin.display_name} to the canvas`);
    add.addEventListener("click", () => addPlugin(plugin.key, layer.slot));
    footer.append(add);
    card.append(footer);
    card.addEventListener("dragstart", (event) => {
      if (!plugin.selectable) return;
      paletteDrag = { pluginKey: plugin.key, slot: layer.slot };
      card.classList.add("dragging");
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData("text/plain", plugin.key);
    });
    card.addEventListener("dragend", () => {
      paletteDrag = null;
      card.classList.remove("dragging");
      canvas.classList.remove("drag-over");
    });
    return card;
  }

  function renderResearchCard(option, layer) {
    const card = element("article", "component-card research-option");
    card.dataset.slot = layer.slot;
    card.append(
      element("div", "component-topline"),
      element("h4", "", option.display_name),
      element("p", "", option.description),
      element("div", "adapter-note", option.adapter_status),
    );
    card.firstChild.append(
      element("span", "component-kind", `Research · ${option.interface}`),
      element("span", `availability ${option.availability}`, option.availability_label),
    );
    const footer = element("div", "component-footer");
    footer.append(element("span", "license", option.license_spdx));
    const unavailable = element("button", "mini-button", "Adapter pending");
    unavailable.type = "button";
    unavailable.disabled = true;
    footer.append(unavailable);
    card.append(footer);
    return card;
  }

  function renderPalette() {
    palette.replaceChildren();
    const query = paletteQuery.trim().toLocaleLowerCase();
    payload.layers.forEach((layer) => {
      const executable = pluginOptions(layer.slot).filter((plugin) => (
        paletteFilter !== "research" && (!query || searchableText(plugin).includes(query))
      ));
      const research = (alternativesBySlot.get(layer.slot) || []).filter((option) => (
        paletteFilter !== "executable" && (!query || searchableText(option).includes(query))
      ));
      if (!executable.length && !research.length) return;
      const group = element("section", "palette-group");
      group.dataset.slot = layer.slot;
      const heading = element("div", "palette-group-head");
      heading.append(
        element("h3", "", layer.short_title),
        element("span", "count-badge", String(executable.length + research.length)),
      );
      group.append(heading);
      executable.forEach((plugin) => group.append(renderExecutableCard(plugin, layer)));
      research.forEach((option) => group.append(renderResearchCard(option, layer)));
      palette.append(group);
    });
    if (!palette.children.length) {
      palette.append(element("div", "palette-empty", "No module or research option matches this search."));
    }
  }

  function nodeButton(label, title, action, className = "") {
    const button = element("button", `icon-button${className ? ` ${className}` : ""}`, label);
    button.type = "button";
    button.title = title;
    button.setAttribute("aria-label", title);
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      action();
    });
    return button;
  }

  function startConnection(event, stage, port, contract, button) {
    event.preventDefault();
    event.stopPropagation();
    pendingConnection = { stageId: stage.id, port, contract, button };
    document.querySelectorAll(".port.pending").forEach((item) => item.classList.remove("pending"));
    button.classList.add("pending");
    const center = portWorldCenter(button);
    pendingConnection.start = center;
    showPortCompatibility(stage, port);
    drawPreview(center);
  }

  function finishConnection(event, target) {
    if (!pendingConnection) return;
    event.preventDefault();
    event.stopPropagation();
    const result = connectOutput(pendingConnection.stageId, pendingConnection.port, target.id);
    showConnectorToast(result.reason, result.allowed);
    cancelConnection();
  }

  function cancelConnection() {
    pendingConnection?.button?.classList.remove("pending");
    clearPortCompatibility();
    pendingConnection = null;
    previewPath.setAttribute("d", "");
  }

  function renderNode(stage, index) {
    const plugin = pluginFor(stage);
    const node = element(
      "article",
      `graph-node${stage.id === selectedStageId ? " selected" : ""}${stage.enabled === false ? " disabled" : ""}${plugin?.is_policy_join ? " policy-node" : ""}`,
    );
    node.dataset.stageId = stage.id;
    node.dataset.slot = stage.slot;
    node.dataset.layoutIndex = String(index);
    node.tabIndex = 0;
    node.setAttribute("aria-label", `${plugin?.display_name || stage.plugin}, node ${stage.id}`);
    const position = layoutFor(stage.id);
    setNodePositionRule(index, position);

    node.addEventListener("click", () => {
      selectedStageId = stage.id;
      selectedEdge = null;
      render();
    });
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectedStageId = stage.id;
        render();
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removeStage(stage.id);
      }
    });

    const handle = element("div", "node-drag-handle");
    const top = element("div", "node-topline");
    top.append(
      element("span", "node-kind", plugin?.is_policy_join ? "ALL policy" : layers.get(stage.slot)?.short_title || stage.slot),
      element("span", "node-state", stage.enabled === false ? "Excluded" : "Executable"),
    );
    handle.append(
      top,
      element("h3", "node-title", plugin?.display_name || stage.plugin),
      element("div", "node-id", stage.id),
    );
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      selectedStageId = stage.id;
      const start = screenToWorld(event.clientX, event.clientY);
      nodeDrag = {
        stageId: stage.id,
        index,
        startX: start.x,
        startY: start.y,
        originX: position.x,
        originY: position.y,
      };
      handle.setPointerCapture(event.pointerId);
    });

    const body = element("div", "node-body");
    const summary = element("div", "node-summary");
    summary.append(
      element("span", "node-chip", `${(stage.inputs || []).length} input${(stage.inputs || []).length === 1 ? "" : "s"}`),
      element("span", "node-chip", `${Object.keys(plugin?.output_ports || {}).length} output${Object.keys(plugin?.output_ports || {}).length === 1 ? "" : "s"}`),
    );
    if (isDecisionCriterion(stage) || plugin?.is_policy_join) {
      summary.append(element("span", "node-chip mode", tierMode(stage.slot) === "parallel_all" ? "Parallel ALL" : "Serial"));
    }
    body.append(summary);

    const footer = element("div", "node-footer");
    footer.append(
      element("span", "", `${plugin?.license_spdx || "Unknown"} · ${plugin?.version || ""}`),
      nodeButton("×", `Remove ${stage.id}`, () => removeStage(stage.id), "danger"),
    );
    node.append(handle, body, footer);

    if (plugin?.kind !== "source") {
      const input = element("button", "port input-port");
      input.type = "button";
      input.dataset.inputStage = stage.id;
      input.title = `Connect an accepted input to ${stage.id}`;
      input.setAttribute("aria-label", `Connect input to ${stage.id}`);
      input.addEventListener("pointerup", (event) => finishConnection(event, stage));
      input.addEventListener("click", (event) => finishConnection(event, stage));
      node.append(input, element("span", "port-label input-label", "input"));
    }

    Object.entries(plugin?.output_ports || {}).forEach(([port, contract], portIndex) => {
      const output = element("button", "port output-port");
      output.type = "button";
      output.dataset.outputStage = stage.id;
      output.dataset.outputPort = port;
      output.dataset.portIndex = String(portIndex);
      output.title = `${stage.id}.${port} · ${contract}`;
      output.setAttribute("aria-label", `Connect ${stage.id} ${port} output, ${contract}`);
      output.addEventListener("pointerdown", (event) => startConnection(event, stage, port, contract, output));
      const label = element("span", "port-label output-label", port);
      label.dataset.portIndex = String(portIndex);
      node.append(output, label);
    });
    return node;
  }

  function renderTierGuides() {
    tierGuides.replaceChildren();
    normalizeTierLanes()
      .filter((allocation) => allocation.bounds)
      .forEach((allocation, index) => {
        const { slot, bounds } = allocation;
        const guide = element("div", "tier-guide");
        guide.dataset.slot = slot;
        guide.dataset.guideIndex = String(index);
        setRuleProperties(`guide-${index}`, `.tier-guide[data-guide-index="${index}"]`, {
          left: `${Math.round(bounds.left)}px`,
          top: `${Math.round(bounds.top)}px`,
          width: `${Math.round(bounds.width)}px`,
          height: `${Math.round(bounds.height)}px`,
        });
        guide.append(element("span", "tier-guide-label", layers.get(slot)?.short_title || slot));
        tierGuides.append(guide);
      });
  }

  function portWorldCenter(port) {
    const node = port.closest(".graph-node");
    const stage = stageById(node?.dataset.stageId);
    const position = stage ? layoutFor(stage.id) : { x: 0, y: 0 };
    return {
      x: position.x + port.offsetLeft + port.offsetWidth / 2,
      y: position.y + port.offsetTop + port.offsetHeight / 2,
    };
  }

  function orthogonalPath(start, end, laneOffset = 0) {
    if (end.x > start.x + 80) {
      const middle = start.x + (end.x - start.x) / 2 + laneOffset;
      return `M ${start.x} ${start.y} H ${middle} V ${end.y} H ${end.x}`;
    }
    const detourY = Math.min(start.y, end.y) - 54 - Math.abs(laneOffset);
    return `M ${start.x} ${start.y} H ${start.x + 42} V ${detourY} H ${end.x - 42} V ${end.y} H ${end.x}`;
  }

  function outputPortElement(stageId, portName) {
    const node = nodeElements.get(stageId);
    return [...(node?.querySelectorAll(".output-port") || [])]
      .find((port) => port.dataset.outputPort === portName) || null;
  }

  function drawConnections() {
    renderFrame = null;
    edgeLayer.replaceChildren();
    const records = [];
    pipeline.stages.forEach((target) => {
      const targetNode = nodeElements.get(target.id);
      const input = targetNode?.querySelector(".input-port");
      if (!input) return;
      (target.inputs || []).forEach((binding, bindingIndex) => {
        const output = outputPortElement(binding.stage, binding.port);
        if (!output) return;
        const start = portWorldCenter(output);
        const end = portWorldCenter(input);
        const key = `${target.id}\u0000${bindingIndex}`;
        const source = stageById(binding.stage);
        const contract = outputContract(source, binding.port);
        const permission = stageConnectionDecision(source, binding.port, target);
        records.push({
          source,
          target,
          binding,
          bindingIndex,
          key,
          contract,
          edgeClass: permission.edgeClass,
          permissionReason: permission.reason,
          start,
          end,
        });
      });
    });

    const visibleRoutes = new Map();
    const hitRoutes = new Map();
    const fanOutGroups = new Map();
    records.filter((record) => record.edgeClass === "primary").forEach((record) => {
      const groupKey = `${record.source.id}\u0000${record.binding.port}`;
      const group = fanOutGroups.get(groupKey) || [];
      group.push(record);
      fanOutGroups.set(groupKey, group);
    });
    fanOutGroups.forEach((group) => {
      if (group.length < 2) return;
      const start = group[0].start;
      const nearestTarget = Math.min(...group.map((record) => record.end.x));
      const busX = Math.min(nearestTarget - 42, start.x + 92);
      const top = Math.min(start.y, ...group.map((record) => record.end.y));
      const policyParents = group.filter((record) => isPolicyJoin(record.target));
      const tierBottom = Math.max(...group.map((record) => {
        const position = layoutFor(record.target.id);
        const node = nodeElements.get(record.target.id);
        return position.y + (node?.offsetHeight || FALLBACK_NODE_HEIGHT);
      }));
      const detourY = tierBottom + 34;
      const bottom = Math.max(
        start.y,
        ...group.filter((record) => !isPolicyJoin(record.target)).map((record) => record.end.y),
        ...(policyParents.length ? [detourY] : []),
      );
      const bus = svgElement("path", "edge-bus primary-edge");
      bus.setAttribute("d", `M ${start.x} ${start.y} H ${busX} M ${busX} ${top} V ${bottom}`);
      edgeLayer.append(bus);
      group.forEach((record) => {
        if (isPolicyJoin(record.target)) {
          const route = `M ${busX} ${detourY} H ${record.end.x - 42} V ${record.end.y} H ${record.end.x}`;
          visibleRoutes.set(record.key, route);
          hitRoutes.set(
            record.key,
            `M ${record.start.x} ${record.start.y} H ${busX} V ${detourY} H ${record.end.x - 42} V ${record.end.y} H ${record.end.x}`,
          );
        } else {
          visibleRoutes.set(record.key, `M ${busX} ${record.end.y} H ${record.end.x}`);
          hitRoutes.set(
            record.key,
            `M ${record.start.x} ${record.start.y} H ${busX} V ${record.end.y} H ${record.end.x}`,
          );
        }
      });
    });

    const fanInGroups = new Map();
    records.filter((record) => record.edgeClass === "decision").forEach((record) => {
      const group = fanInGroups.get(record.target.id) || [];
      group.push(record);
      fanInGroups.set(record.target.id, group);
    });
    fanInGroups.forEach((group) => {
      if (group.length < 2) return;
      const end = group[0].end;
      const latestSource = Math.max(...group.map((record) => record.start.x));
      const busX = Math.max(latestSource + 42, end.x - 78);
      const top = Math.min(end.y, ...group.map((record) => record.start.y));
      const bottom = Math.max(end.y, ...group.map((record) => record.start.y));
      const bus = svgElement("path", "edge-bus decision-edge");
      bus.setAttribute("d", `M ${busX} ${top} V ${bottom} M ${busX} ${end.y} H ${end.x}`);
      edgeLayer.append(bus);
      group.forEach((record) => {
        visibleRoutes.set(record.key, `M ${record.start.x} ${record.start.y} H ${busX}`);
        hitRoutes.set(
          record.key,
          `M ${record.start.x} ${record.start.y} H ${busX} V ${record.end.y} H ${record.end.x}`,
        );
      });
    });

    records.forEach((record, recordIndex) => {
        const className = `${record.edgeClass}-edge`;
        const path = svgElement(
          "path",
          `edge-path ${className}${selectedEdge === record.key ? " selected-edge" : ""}`,
        );
        const route = visibleRoutes.get(record.key)
          || orthogonalPath(record.start, record.end, (recordIndex % 3 - 1) * 12);
        const hitRoute = hitRoutes.get(record.key) || route;
        path.setAttribute("d", route);
        const hit = svgElement("path", "edge-hit");
        hit.setAttribute("d", hitRoute);
        const title = svgElement("title");
        title.textContent = `${record.binding.stage}.${record.binding.port} to ${record.target.id}.${record.binding.request_port} · ${record.contract}. Click to remove.`;
        hit.append(title);
        hit.addEventListener("click", (event) => {
          event.stopPropagation();
          selectedEdge = record.key;
          removeBinding(record.target.id, record.bindingIndex);
        });
        const label = svgElement(
          "text",
          `edge-label${record.edgeClass === "rejected" ? " invalid-label" : ""}`,
        );
        label.setAttribute("x", String(Math.max(record.start.x + 14, record.end.x - 69)));
        label.setAttribute("y", String(record.end.y - 7));
        label.textContent = record.edgeClass === "rejected"
          ? "invalid"
          : record.edgeClass === "primary"
          ? "parents"
          : record.edgeClass === "decision"
            ? "decision"
            : record.contract.split("/")[0];
        if (record.edgeClass === "rejected") {
          title.textContent += ` · INVALID: ${record.permissionReason}`;
        }
        edgeLayer.append(path, hit, label);
    });
  }

  function scheduleConnections() {
    if (renderFrame !== null) cancelAnimationFrame(renderFrame);
    renderFrame = requestAnimationFrame(drawConnections);
  }

  function drawPreview(point) {
    if (!pendingConnection?.start) return;
    previewPath.setAttribute("d", orthogonalPath(pendingConnection.start, point));
  }

  function renderScene() {
    ensureBuilderMetadata();
    nodeElements.clear();
    nodeLayer.replaceChildren();
    pipeline.stages.forEach((stage, index) => {
      const node = renderNode(stage, index);
      nodeElements.set(stage.id, node);
      nodeLayer.append(node);
    });
    renderTierGuides();
    emptyState.hidden = pipeline.stages.length > 0;
    const explicit = new Map();
    pipeline.stages.forEach((stage) => (stage.inputs || []).forEach((binding) => {
      const key = `${binding.stage}.${binding.port}`;
      explicit.set(key, (explicit.get(key) || 0) + 1);
    }));
    const branches = [...explicit.values()].filter((count) => count > 1).length;
    stageCount.textContent = `${enabledStages().length} executable nodes`;
    branchCount.textContent = `${branches} ${branches === 1 ? "branch point" : "branch points"}`;
    updateSceneTransform();
    scheduleConnections();
  }

  function field(labelText, input, helpText) {
    const wrapper = element("div", "field");
    const label = element("label", "", labelText);
    if (!input.id) input.id = `field-${++fieldCounter}`;
    label.htmlFor = input.id;
    wrapper.append(label, input);
    if (helpText) wrapper.append(element("div", "help", helpText));
    return wrapper;
  }

  function resolveSchema(schema, definition) {
    let resolved = definition || {};
    if (resolved.$ref?.startsWith("#/$defs/")) {
      resolved = schema.$defs?.[resolved.$ref.slice(8)] || resolved;
    }
    if (resolved.anyOf) {
      const nonNull = resolved.anyOf.find((item) => item.type !== "null") || resolved.anyOf[0];
      const nested = resolveSchema(schema, nonNull);
      return { ...resolved, ...nested, nullable: resolved.anyOf.some((item) => item.type === "null") };
    }
    return resolved;
  }

  function renderPrimitiveConfig(container, stage, plugin) {
    const schema = plugin.config_schema || {};
    const required = new Set(schema.required || []);
    const entries = Object.entries(schema.properties || {});
    if (plugin.key === "chemistry.rdkit_drug_likeness@0.1.0") {
      const priority = new Map([
        ["maximum_rule_of_five_violations", 0],
        ["minimum_qed", 1],
        ["failure_action", 2],
        ["batch_size", 3],
      ]);
      entries.sort(([left], [right]) => (priority.get(left) ?? 50) - (priority.get(right) ?? 50));
    }
    entries.forEach(([name, rawDefinition]) => {
      if (name === "schema_version") return;
      const definition = resolveSchema(schema, rawDefinition);
      const current = stage.config[name];
      let input;
      if (Array.isArray(definition.enum)) {
        input = element("select", "");
        if (definition.nullable) {
          const blank = element("option", "", "Not set");
          blank.value = "";
          input.append(blank);
        }
        definition.enum.forEach((optionValue) => {
          const option = element("option", "", humanize(optionValue));
          option.value = String(optionValue);
          input.append(option);
        });
        input.value = current ?? definition.default ?? "";
        input.addEventListener("change", () => {
          stage.config[name] = input.value === "" && definition.nullable ? null : input.value;
          validatePipeline();
        });
      } else if (definition.type === "boolean") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = current ?? definition.default ?? false;
        input.addEventListener("change", () => {
          stage.config[name] = input.checked;
          validatePipeline();
        });
      } else if (definition.type === "integer" || definition.type === "number") {
        input = document.createElement("input");
        input.type = "number";
        if (definition.minimum !== undefined) input.min = String(definition.minimum);
        if (definition.maximum !== undefined) input.max = String(definition.maximum);
        input.step = definition.type === "integer" ? "1" : "any";
        input.value = current ?? definition.default ?? "";
        input.addEventListener("change", () => {
          if (input.value === "" && definition.nullable) stage.config[name] = null;
          else if (input.value !== "") {
            stage.config[name] = definition.type === "integer"
              ? Number.parseInt(input.value, 10)
              : Number(input.value);
          }
          validatePipeline();
        });
      } else if (definition.type === "string") {
        input = document.createElement("input");
        input.type = "text";
        input.value = current ?? definition.default ?? "";
        input.addEventListener("input", () => {
          stage.config[name] = input.value;
          validatePipeline();
        });
      } else {
        return;
      }
      const merged = { ...rawDefinition, ...definition, required: required.has(name) };
      container.append(field(
        parameterLabel(plugin, name, merged),
        input,
        parameterHelp(plugin, name, merged),
      ));
    });
  }

  function renderInputBindings(container, stage, plugin) {
    container.append(element(
      "p",
      "section-copy",
      "Primary parent flow can enter only the next occupied tier; same-tier parent flow requires Serial mode. Dotted teal edges are exact-contract side evidence and never replace the parent population.",
    ));
    const bindings = element("div", "binding-editor");
    (stage.inputs || []).forEach((binding, bindingIndex) => {
      const row = element("div", "binding-row");
      const requestInput = document.createElement("input");
      requestInput.value = binding.request_port;
      requestInput.setAttribute("aria-label", "Downstream request port");
      requestInput.addEventListener("change", () => {
        binding.request_port = requestInput.value.trim();
        render();
      });

      const sourceSelect = document.createElement("select");
      sourceSelect.setAttribute("aria-label", "Upstream node");
      compatibleSourceStages(stage, plugin).forEach(({ stage: candidate }) => {
        const option = element("option", "", candidate.id);
        option.value = candidate.id;
        sourceSelect.append(option);
      });
      if (![...sourceSelect.options].some((option) => option.value === binding.stage)) {
        const current = element("option", "", binding.stage);
        current.value = binding.stage;
        sourceSelect.prepend(current);
      }
      sourceSelect.value = binding.stage;

      const portSelect = document.createElement("select");
      portSelect.setAttribute("aria-label", "Retained output port");
      const refreshPorts = () => {
        portSelect.replaceChildren();
        compatibleOutputs(stageById(sourceSelect.value), plugin).forEach(({ port, contract }) => {
          const option = element("option", "", `${port} · ${contract}`);
          option.value = port;
          portSelect.append(option);
        });
        if ([...portSelect.options].some((option) => option.value === binding.port)) {
          portSelect.value = binding.port;
        } else if (portSelect.options.length) {
          binding.port = portSelect.options[0].value;
        }
      };
      sourceSelect.addEventListener("change", () => {
        binding.stage = sourceSelect.value;
        binding.port = "primary";
        refreshPorts();
        sortPipelineTopologically();
        render();
      });
      portSelect.addEventListener("change", () => {
        binding.port = portSelect.value;
        render();
      });
      refreshPorts();
      row.append(
        requestInput,
        sourceSelect,
        portSelect,
        nodeButton("×", `Remove input ${binding.request_port}`, () => removeBinding(stage.id, bindingIndex)),
      );
      bindings.append(row);
    });
    if (!(stage.inputs || []).length) {
      bindings.append(element("div", "binding-empty", "No explicit input connection"));
    }
    container.append(bindings);
    const actions = element("div", "inline-actions");
    const wire = element("button", "mini-button", plugin.is_policy_join ? "Rebuild ALL join" : "Auto-connect input");
    wire.type = "button";
    wire.disabled = plugin.kind === "source";
    wire.addEventListener("click", () => {
      if (plugin.is_policy_join) applyTierMode(stage.slot, "parallel_all");
      else {
        autoWire(stage);
        render();
      }
    });
    const clear = element("button", "mini-button quiet", "Clear inputs");
    clear.type = "button";
    clear.disabled = plugin.kind === "source";
    clear.addEventListener("click", () => {
      stage.inputs = [];
      render();
    });
    actions.append(wire, clear);
    container.append(actions);
  }

  function renderFlowMode(container, stage) {
    const criteria = criteriaForTier(stage.slot);
    const applicable = stage.slot === "hard_gate" || criteria.length > 0 || isPolicyJoin(stage);
    const select = document.createElement("select");
    [
      ["parallel_all", "Parallel (ALL)"],
      ["serial", "Serial"],
    ].forEach(([value, label]) => {
      const option = element("option", "", label);
      option.value = value;
      option.disabled = value === "parallel_all" && criteria.length < 2;
      select.append(option);
    });
    select.value = tierMode(stage.slot);
    select.disabled = !applicable;
    select.addEventListener("change", () => applyTierMode(stage.slot, select.value));
    container.append(field(
      "Same-tier evaluation",
      select,
      applicable
        ? criteria.length < 2
          ? "Add at least two decision criteria to enable Parallel (ALL). A single criterion is always Serial."
          : "Parallel (ALL): every criterion sees the same population and all must pass. Serial: each criterion sees only prior survivors."
        : "This tier has no decision-emitting criterion, so branch policy is not applicable.",
    ));
  }

  function renderInspector() {
    inspector.replaceChildren();
    const stage = stageById(selectedStageId);
    if (!stage) {
      const empty = element("div", "inspector-empty");
      empty.append(
        element("strong", "", "Select a node"),
        document.createTextNode("Choose a canvas node to edit its implementation, connections, and parameters."),
      );
      inspector.append(empty);
      return;
    }
    const plugin = pluginFor(stage);
    const layer = layers.get(stage.slot);

    const identity = element("section", "section");
    const heading = element("div", "section-heading");
    heading.append(
      element("h3", "", "Node identity"),
      element("span", "section-index", String(pipeline.stages.indexOf(stage) + 1)),
    );
    identity.append(heading);
    const idInput = document.createElement("input");
    idInput.value = stage.id;
    idInput.addEventListener("change", () => {
      const replacement = idInput.value.trim();
      if (!/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(replacement)) {
        idInput.setCustomValidity("Start with a letter and use only letters, digits, dots, underscores, or hyphens.");
        idInput.reportValidity();
        idInput.value = stage.id;
        return;
      }
      if (pipeline.stages.some((candidate) => candidate !== stage && candidate.id === replacement)) {
        idInput.setCustomValidity("Node IDs must be unique.");
        idInput.reportValidity();
        idInput.value = stage.id;
        return;
      }
      idInput.setCustomValidity("");
      renameStage(stage, replacement);
      render();
    });
    identity.append(field("Node ID", idInput, "Stable identifier used by connections, audit events, and reports."));
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.checked = stage.enabled !== false;
    enabled.addEventListener("change", () => {
      stage.enabled = enabled.checked;
      render();
    });
    identity.append(field("Include in executable config", enabled, "Excluded nodes remain on this editing canvas but are never exported."));
    renderFlowMode(identity, stage);
    inspector.append(identity);

    const implementation = element("section", "section");
    implementation.append(element("h3", "", "Local implementation"));
    const select = document.createElement("select");
    pluginOptions(stage.slot).forEach((candidate) => {
      const option = element("option", "", `${candidate.display_name} · ${candidate.availability_label}`);
      option.value = candidate.key;
      option.disabled = !candidate.selectable;
      select.append(option);
    });
    if (plugin?.is_policy_join && ![...select.options].some((option) => option.value === plugin.key)) {
      const option = element("option", "", `${plugin.display_name} · ${plugin.availability_label}`);
      option.value = plugin.key;
      select.append(option);
    }
    select.value = stage.plugin;
    select.addEventListener("change", () => {
      stage.plugin = select.value;
      stage.config = defaultConfig(pluginFor(stage));
      autoWire(stage);
      render();
    });
    implementation.append(field("Tool / module", select, "Only registered, version-pinned, locally executable adapters can be selected."));
    implementation.append(element(
      "div",
      plugin?.availability === "available" ? "status-note" : "notice",
      plugin?.status_reason || "Adapter status unavailable.",
    ));
    if (plugin?.science_note) implementation.append(element("div", "science-note", plugin.science_note));
    const facts = element("div", "fact-row");
    [plugin?.license_spdx, plugin?.cardinality, plugin?.determinism].filter(Boolean)
      .forEach((value) => facts.append(element("span", "pill", value)));
    implementation.append(facts);
    inspector.append(implementation);

    if (plugin?.kind !== "source") {
      const inputs = element("section", "section");
      const inputHeading = element("div", "section-heading");
      inputHeading.append(
        element("h3", "", "Port connections"),
        element("span", "pill", `${plugin.input_contracts.length} accepted contracts`),
      );
      inputs.append(inputHeading);
      renderInputBindings(inputs, stage, plugin);
      inspector.append(inputs);
    }

    if (plugin) {
      const configuration = element("section", "section");
      configuration.append(element("h3", "", plugin.is_policy_join ? "Combination policy" : "Parameters"));
      if (plugin.is_policy_join) {
        configuration.append(element(
          "p",
          "section-copy",
          "This is the executable convergence barrier. Parallel (ALL) pins it to All Required; changing the tier to Serial removes it.",
        ));
      }
      const parameterGrid = element("div", "parameter-grid");
      renderPrimitiveConfig(parameterGrid, stage, plugin);
      configuration.append(parameterGrid);
      const advanced = document.createElement("textarea");
      advanced.value = JSON.stringify(stage.config, null, 2);
      advanced.spellcheck = false;
      advanced.addEventListener("change", () => {
        try {
          stage.config = JSON.parse(advanced.value);
          advanced.setCustomValidity("");
          validatePipeline();
          renderScene();
        } catch (error) {
          advanced.setCustomValidity(`Invalid JSON: ${error.message}`);
          advanced.reportValidity();
        }
      });
      configuration.append(field("Advanced JSON", advanced, "The CLI performs authoritative schema validation before execution."));
      inspector.append(configuration);
    }
  }

  function modeSlot() {
    const selected = stageById(selectedStageId);
    if (selected && (selected.slot === "hard_gate" || criteriaForTier(selected.slot).length)) {
      return selected.slot;
    }
    return pipeline.stages.some((stage) => stage.slot === "hard_gate") ? "hard_gate" : selected?.slot || "hard_gate";
  }

  function updateModeControl() {
    const slot = modeSlot();
    const layer = layers.get(slot);
    const criteria = criteriaForTier(slot);
    const applicable = slot === "hard_gate" || criteria.length > 0;
    const parallelOption = modeSelect.querySelector('option[value="parallel_all"]');
    if (parallelOption) parallelOption.disabled = criteria.length < 2;
    modeTierName.textContent = layer?.short_title || humanize(slot);
    modeSelect.value = tierMode(slot);
    modeSelect.disabled = !applicable;
    modeSelect.title = criteria.length < 2
      ? "Add at least two decision criteria to enable Parallel (ALL)."
      : "Choose whether same-tier criteria run as one survivor chain or as ALL-required branches.";
  }

  function validationIssues() {
    const issues = [];
    const enabled = enabledStages();
    if (!pipeline.name?.trim()) issues.push({ severity: "error", message: "Pipeline name cannot be blank." });
    if (!enabled.length) issues.push({ severity: "error", message: "Include at least one executable node." });
    const sources = enabled.filter((stage) => pluginFor(stage)?.kind === "source");
    if (sources.length !== 1) issues.push({ severity: "error", message: "The executable graph must contain exactly one input source." });
    if (!Number.isInteger(Number(pipeline.metadata.final_parent_target)) || Number(pipeline.metadata.final_parent_target) < 1) {
      issues.push({ severity: "error", message: "Final parents must be a positive integer." });
    }
    if (!topologicalOrder()) issues.push({ severity: "error", message: "The graph contains a cycle. Remove one of the highlighted conceptual connections." });

    const seen = new Set();
    pipeline.stages.forEach((stage, index) => {
      const plugin = pluginFor(stage);
      const layer = layers.get(stage.slot);
      if (!/^[A-Za-z][A-Za-z0-9_.-]{0,127}$/.test(stage.id)) {
        issues.push({ severity: "error", message: `Node ${index + 1} has an invalid ID.` });
      }
      if (seen.has(stage.id)) issues.push({ severity: "error", message: `Duplicate node ID: ${stage.id}.` });
      seen.add(stage.id);
      if (!plugin) issues.push({ severity: "error", message: `${stage.id} references an unknown executable plugin.` });
      if (!layer) issues.push({ severity: "error", message: `${stage.id} has an unknown screening tier.` });
      if (plugin && layer && plugin.kind !== layer.kind && !plugin.is_policy_join) {
        issues.push({ severity: "error", message: `${stage.id} is incompatible with the ${layer.short_title} tier.` });
      }
      if (stage.enabled !== false && plugin && !plugin.selectable) {
        issues.push({ severity: "error", message: `${stage.id} is not locally executable: ${plugin.status_reason}` });
      }
      if (stage.enabled !== false && plugin?.kind !== "source" && !(stage.inputs || []).length) {
        issues.push({ severity: "error", message: `${stage.id} needs an explicit input connection.` });
      }
      const requestPorts = new Set();
      (stage.inputs || []).forEach((binding) => {
        if (requestPorts.has(binding.request_port)) {
          issues.push({ severity: "error", message: `${stage.id} repeats request port ${binding.request_port}.` });
        }
        requestPorts.add(binding.request_port);
        const source = stageById(binding.stage);
        const contract = outputContract(source, binding.port);
        if (!source) issues.push({ severity: "error", message: `${stage.id} references missing node ${binding.stage}.` });
        else if (source.enabled === false && stage.enabled !== false) {
          issues.push({ severity: "error", message: `${stage.id} depends on excluded node ${source.id}.` });
        } else if (!contract) {
          issues.push({ severity: "error", message: `${stage.id} references missing output ${binding.stage}.${binding.port}.` });
        } else if (plugin && !plugin.input_contracts.includes(contract)) {
          issues.push({ severity: "error", message: `${stage.id} does not accept ${contract} from ${binding.stage}.${binding.port}.` });
        } else {
          const permission = stageConnectionDecision(source, binding.port, stage);
          if (!permission.allowed) {
            issues.push({
              severity: "error",
              message: `${binding.stage}.${binding.port} → ${stage.id} is not allowed: ${permission.reason}`,
            });
          }
        }
      });
      if (requiresCoProducedEvidence(stage) && plugin) {
        const coProduced = builderRules.coProducedInputsDecision({
          requiredContracts: plugin.input_contracts,
          bindings: (stage.inputs || []).map((binding) => ({
            sourceId: binding.stage,
            contract: outputContract(stageById(binding.stage), binding.port),
          })),
        });
        if (!coProduced.allowed) {
          issues.push({ severity: "error", message: `${stage.id}: ${coProduced.reason}` });
        }
      }
      if (plugin?.key === "applicability.rdkit_reference_similarity@0.1.0") {
        const config = stage.config || {};
        const embedded = Array.isArray(config.references) && config.references.length > 0;
        const fileBacked = typeof config.reference_path === "string" && config.reference_path.trim();
        if (embedded === Boolean(fileBacked)) {
          issues.push({ severity: "error", message: `${stage.id} must use either one local reference file or embedded references.` });
        }
        if (config.reference_sha256 && !/^[0-9a-f]{64}$/.test(config.reference_sha256)) {
          issues.push({ severity: "error", message: `${stage.id} reference SHA-256 must contain 64 lowercase hexadecimal characters.` });
        }
      }
      if (stage.enabled !== false && plugin?.key === "prediction.admet_ai_v2@0.1.0") {
        const config = stage.config || {};
        if (typeof config.models_dir !== "string" || !config.models_dir.trim()) {
          issues.push({ severity: "error", message: `${stage.id} needs a verified local ADMET-AI models directory.` });
        }
        [
          ["expected_model_manifest_sha256", "model-tree"],
          ["expected_package_code_sha256", "package-code"],
        ].forEach(([name, label]) => {
          if (!/^[0-9a-f]{64}$/.test(config[name] || "")) {
            issues.push({ severity: "error", message: `${stage.id} ${label} SHA-256 must contain 64 lowercase hexadecimal characters from the inspection helper.` });
          }
        });
        if (!Array.isArray(config.endpoints) || !config.endpoints.length) {
          issues.push({ severity: "error", message: `${stage.id} needs at least one exact ADMET-AI output-column mapping.` });
        }
        if (config.allow_unsafe_model_deserialization !== true) {
          issues.push({ severity: "error", message: `${stage.id} requires explicit trust after package and model pins have been reviewed.` });
        }
      }
    });

    payload.layers.forEach((layer) => {
      const criteria = criteriaForTier(layer.slot);
      const joins = pipeline.stages.filter((stage) => (
        stage.enabled !== false && stage.slot === layer.slot && isPolicyJoin(stage)
      ));
      if (criteria.length < 2) {
        if (joins.length) {
          issues.push({ severity: "error", message: `${layer.short_title} needs at least two criteria for a Parallel (ALL) policy join.` });
        }
        return;
      }
      if (tierMode(layer.slot) === "parallel_all") {
        const anchors = criteria.map((stage) => {
          const binding = parentBindingFrom(stage);
          return binding ? `${binding.stage}.${binding.port}` : "";
        });
        if (!anchors[0] || new Set(anchors).size !== 1) {
          issues.push({ severity: "error", message: `${layer.short_title} Parallel (ALL) criteria must consume the same parent population.` });
        }
        if (joins.length !== 1) {
          issues.push({ severity: "error", message: `${layer.short_title} Parallel (ALL) requires exactly one executable policy join.` });
        } else {
          const decisionStages = new Set((joins[0].inputs || []).filter((binding) => (
            outputContract(stageById(binding.stage), binding.port) === "decision/v1"
          )).map((binding) => binding.stage));
          const missing = criteria.filter((stage) => !decisionStages.has(stage.id));
          if (missing.length) {
            issues.push({ severity: "error", message: `${joins[0].id} is missing decisions from ${missing.map((stage) => stage.id).join(", ")}.` });
          }
          if (joins[0].config?.mode !== "all_required") {
            issues.push({ severity: "error", message: `${joins[0].id} must use All Required for Parallel (ALL).` });
          }
        }
      } else {
        if (joins.length) issues.push({ severity: "error", message: `${layer.short_title} Serial mode must not contain a parallel policy join.` });
        criteria.slice(1).forEach((stage, index) => {
          const binding = parentBindingFrom(stage);
          if (!binding || binding.stage !== criteria[index].id) {
            issues.push({ severity: "error", message: `${layer.short_title} Serial mode must chain ${criteria[index].id} into ${stage.id}.` });
          }
        });
      }
    });
    return issues;
  }

  function validatePipeline() {
    const issues = validationIssues();
    errors.replaceChildren();
    issues.slice(0, 8).forEach((issue) => {
      const item = element("div", `issue-item ${issue.severity}`);
      item.append(
        element("span", "issue-label", issue.severity === "error" ? "Fix" : "Review"),
        element("span", "", issue.message),
      );
      errors.append(item);
    });
    return issues;
  }

  function setFinalTarget(value) {
    const target = Number.parseInt(value || "0", 10);
    pipeline.metadata.final_parent_target = target;
    const selector = [...pipeline.stages].reverse().find((stage) => (
      stage.enabled !== false && pluginFor(stage)?.kind === "selector"
    ));
    if (selector && Object.hasOwn(selector.config, "target_count")) selector.config.target_count = target;
  }

  function normalizedConfig() {
    pipeline.name = nameInput.value.trim();
    setFinalTarget(targetInput.value);
    sortPipelineTopologically();
    const executableIds = new Set(pipeline.stages.filter((stage) => (
      stage.enabled !== false && byKey.get(stage.plugin)?.selectable
    )).map((stage) => stage.id));
    const config = clone(pipeline);
    config.stages = config.stages
      .filter((stage) => executableIds.has(stage.id))
      .map((stage) => ({
        ...stage,
        enabled: true,
        inputs: (stage.inputs || []).filter((binding) => executableIds.has(binding.stage)),
      }));
    const nodes = {};
    executableIds.forEach((stageId) => {
      nodes[stageId] = clone(ensureBuilderMetadata().nodes[stageId]);
    });
    config.metadata.builder_layout = {
      schema_version: 2,
      viewport: clone(viewState()),
      tier_modes: clone(ensureBuilderMetadata().tier_modes),
      nodes,
    };
    config.metadata.builder_view = "node_canvas/v2";
    return config;
  }

  function configText() {
    return `${JSON.stringify(normalizedConfig(), null, 2)}\n`;
  }

  function hasBlockingIssues() {
    return validatePipeline().some((issue) => issue.severity === "error");
  }

  function downloadBlob(text, filename) {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  async function saveConfig() {
    if (hasBlockingIssues()) return;
    const text = configText();
    if (window.showSaveFilePicker) {
      try {
        const handle = await window.showSaveFilePicker({
          suggestedName: payload.export_filename,
          types: [{ description: "MolCascade JSON configuration", accept: { "application/json": [".json"] } }],
        });
        const writable = await handle.createWritable();
        await writable.write(text);
        await writable.close();
        return;
      } catch (error) {
        if (error.name === "AbortError") return;
      }
    }
    downloadBlob(text, payload.export_filename);
  }

  function render() {
    ensureBuilderMetadata();
    pipeline.stages.forEach((stage) => { stage.inputs = stage.inputs || []; });
    nameInput.value = pipeline.name || "";
    targetInput.value = pipeline.metadata.final_parent_target || 45000;
    renderPalette();
    renderScene();
    renderInspector();
    updateModeControl();
    validatePipeline();
  }

  search.addEventListener("input", () => {
    paletteQuery = search.value;
    renderPalette();
  });
  filter.addEventListener("change", () => {
    paletteFilter = filter.value;
    renderPalette();
  });
  nameInput.addEventListener("input", () => {
    pipeline.name = nameInput.value;
    validatePipeline();
  });
  targetInput.addEventListener("input", () => {
    setFinalTarget(targetInput.value);
    validatePipeline();
  });
  modeSelect.addEventListener("change", () => applyTierMode(modeSlot(), modeSelect.value));

  canvas.addEventListener("dragover", (event) => {
    if (!paletteDrag) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    canvas.classList.add("drag-over");
  });
  canvas.addEventListener("dragleave", (event) => {
    if (!canvas.contains(event.relatedTarget)) canvas.classList.remove("drag-over");
  });
  canvas.addEventListener("drop", (event) => {
    event.preventDefault();
    canvas.classList.remove("drag-over");
    if (!paletteDrag) return;
    addPlugin(paletteDrag.pluginKey, paletteDrag.slot, screenToWorld(event.clientX, event.clientY));
    paletteDrag = null;
  });

  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest(".graph-node") || event.target.closest(".edge-hit")) return;
    if (pendingConnection) cancelConnection();
    panDrag = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      originX: viewState().x,
      originY: viewState().y,
    };
    canvas.classList.add("panning");
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const view = viewState();
    const worldX = (event.clientX - rect.left - view.x) / view.zoom;
    const worldY = (event.clientY - rect.top - view.y) / view.zoom;
    const next = Math.min(1.8, Math.max(0.24, view.zoom * Math.exp(-event.deltaY * 0.0012)));
    view.x = event.clientX - rect.left - worldX * next;
    view.y = event.clientY - rect.top - worldY * next;
    view.zoom = next;
    updateSceneTransform();
    scheduleConnections();
  }, { passive: false });

  document.addEventListener("pointermove", (event) => {
    if (nodeDrag) {
      const point = screenToWorld(event.clientX, event.clientY);
      const position = layoutFor(nodeDrag.stageId);
      position.x = Math.max(20, Math.min(4700, nodeDrag.originX + point.x - nodeDrag.startX));
      position.y = Math.max(60, Math.min(2050, nodeDrag.originY + point.y - nodeDrag.startY));
      setNodePositionRule(nodeDrag.index, position);
      renderTierGuides();
      scheduleConnections();
    } else if (panDrag) {
      const view = viewState();
      view.x = panDrag.originX + event.clientX - panDrag.clientX;
      view.y = panDrag.originY + event.clientY - panDrag.clientY;
      updateSceneTransform();
      scheduleConnections();
    }
    if (pendingConnection) drawPreview(screenToWorld(event.clientX, event.clientY));
  });
  document.addEventListener("pointerup", () => {
    if (nodeDrag) {
      nodeDrag = null;
      renderTierGuides();
      scheduleConnections();
    }
    if (panDrag) {
      panDrag = null;
      canvas.classList.remove("panning");
    }
  });

  document.getElementById("auto-layout").addEventListener("click", () => {
    arrangeAllNodes();
    fitView();
  });
  document.getElementById("fit-view").addEventListener("click", fitView);
  document.getElementById("reset-view").addEventListener("click", resetView);
  document.getElementById("save-config").addEventListener("click", saveConfig);
  document.getElementById("download-config").addEventListener("click", () => {
    if (!hasBlockingIssues()) downloadBlob(configText(), payload.export_filename);
  });
  document.getElementById("restore-default").addEventListener("click", () => {
    pipeline = clone(payload.default_pipeline);
    selectedStageId = pipeline.stages[0]?.id || null;
    selectedEdge = null;
    cancelConnection();
    render();
  });
  document.getElementById("preview-config").addEventListener("click", () => {
    preview.value = configText();
    modal.classList.add("open");
    document.getElementById("close-preview").focus();
  });
  document.getElementById("close-preview").addEventListener("click", () => modal.classList.remove("open"));
  modal.addEventListener("click", (event) => {
    if (event.target === modal) modal.classList.remove("open");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      modal.classList.remove("open");
      cancelConnection();
    }
    if (
      (event.key === "Delete" || event.key === "Backspace")
      && selectedStageId
      && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
    ) removeStage(selectedStageId);
  });
  window.addEventListener("resize", scheduleConnections);

  render();
})();

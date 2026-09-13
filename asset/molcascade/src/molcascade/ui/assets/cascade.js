/* MolCascade offline cascade builder.
 *
 * Three rules hold this file together.
 *
 * 1. `state` IS the cascade configuration file.  Every control mutates that
 *    object in place and every render reads from it, so what the Download
 *    button writes is exactly what the screen showed.
 *
 * 2. `planStages()` is a line-by-line mirror of `lower_cascade`.  The canvas is
 *    drawn from it, the review dialog lists it, and a test compares it against
 *    the real Python lowering.  A block that appears on screen therefore names
 *    a stage that will really execute, and a stage that will execute has a
 *    block on screen -- the picture cannot drift away from the run.
 *
 * 3. No network, no eval, no inline handlers: every listener is attached with
 *    addEventListener and every asset is already in the document.
 */
"use strict";

(function () {
  const payload = JSON.parse(document.getElementById("builder-payload").value);

  const CRITERIA = new Map(payload.criteria.map((spec) => [spec.id, spec]));
  const STAGES = new Map(payload.stages.map((stage) => [stage.id, stage]));
  const MODES = new Map(payload.tier_modes.map((mode) => [mode.value, mode]));

  const dom = {
    funnel: document.getElementById("funnel"),
    tray: document.getElementById("tray"),
    traySearch: document.getElementById("tray-search"),
    machine: document.getElementById("machine"),
    inspector: document.getElementById("inspector"),
    inspectorHint: document.getElementById("inspector-hint"),
    status: document.getElementById("status-bar"),
    name: document.getElementById("cascade-name"),
    target: document.getElementById("target-count"),
    reset: document.getElementById("action-reset"),
    load: document.getElementById("action-load"),
    review: document.getElementById("action-review"),
    download: document.getElementById("action-download"),
    file: document.getElementById("config-file"),
    picker: document.getElementById("picker"),
    pickerSearch: document.getElementById("picker-search"),
    pickerList: document.getElementById("picker-list"),
    reviewDialog: document.getElementById("review"),
    reviewBody: document.getElementById("review-body"),
    reviewPlan: document.getElementById("review-plan"),
    reviewCommand: document.getElementById("review-command"),
  };

  let state = clone(payload.default_cascade);
  /** What the inspector is showing: {kind, tier, criterion}. */
  let selection = { kind: "library" };
  let pickerTier = null;
  /** The brick currently in the air, or null.  Cleared on drop and dragend. */
  let drag = null;

  /* ------------------------------------------------------------ utilities */

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function el(tag, props, children) {
    const node = document.createElement(tag);
    const options = props || {};
    for (const key of Object.keys(options)) {
      const value = options[key];
      if (key === "checked") {
        node.checked = Boolean(value);
      } else if (key === "value") {
        node.value = value === null || value === undefined ? "" : String(value);
      } else if (key === "text") {
        node.textContent = value === null || value === undefined ? "" : String(value);
      } else if (key === "dataset") {
        // A null dataset, or a null entry inside one, means "this node is not a
        // stage".  Writing the key anyway would put an empty `data-stage` on the
        // canvas, and the faithfulness check reads exactly that attribute.
        for (const name of Object.keys(value || {})) {
          if (value[name] !== null && value[name] !== undefined) {
            node.dataset[name] = String(value[name]);
          }
        }
      } else if (key === "on") {
        for (const event of Object.keys(value || {})) node.addEventListener(event, value[event]);
      } else if (value === null || value === undefined || value === false) {
        continue;
      } else if (key === "class") {
        node.className = String(value);
      } else if (value === true) {
        node.setAttribute(key, "");
      } else {
        node.setAttribute(key, String(value));
      }
    }
    for (const child of [].concat(children || [])) {
      if (child) node.appendChild(child);
    }
    return node;
  }

  function replace(container, nodes) {
    container.textContent = "";
    for (const node of [].concat(nodes || [])) {
      if (node) container.appendChild(node);
    }
  }

  function getPath(target, path) {
    let cursor = target;
    for (const key of path.split(".")) {
      if (cursor === null || cursor === undefined || typeof cursor !== "object") return undefined;
      cursor = cursor[key];
    }
    return cursor;
  }

  function setPath(target, path, value) {
    const keys = path.split(".");
    let cursor = target;
    for (let index = 0; index < keys.length - 1; index += 1) {
      const key = keys[index];
      const nested = cursor[key];
      if (nested === null || typeof nested !== "object" || Array.isArray(nested)) cursor[key] = {};
      cursor = cursor[key];
    }
    cursor[keys[keys.length - 1]] = value;
  }

  function setStatus(kind, message) {
    dom.status.className = "status-bar" + (kind ? " " + kind : "");
    dom.status.textContent = message || "";
  }

  /* Confirmations must never paper over a blocking problem: if the cascade is
   * currently unexportable, the reason for that stays on screen. */
  function announce(kind, message) {
    if (blockingIssues().length) return;
    setStatus(kind, message);
  }

  function plural(count, word) {
    return count + " " + word + (count === 1 ? "" : "s");
  }

  function countCriteria(count) {
    return count + (count === 1 ? " criterion" : " criteria");
  }

  /* "1 criterion is" / "3 criteria are".  Validation messages are read at the
   * moment something is wrong, which is the worst moment to make the reader
   * wonder whether the sentence or the number is the mistake. */
  function criteriaAre(count) {
    return countCriteria(count) + (count === 1 ? " is" : " are");
  }

  function accentFor(stageId) {
    const stage = STAGES.get(stageId);
    return stage ? stage.accent : "#6785b9";
  }

  /* Below this many molecules per second, where a block sits in the stack
   * matters more than anything else about it. The line is drawn at 500 because
   * the measured backends fall into two clear groups either side of it -- a few
   * hundred per second for descriptor arithmetic, high hundreds for parsing --
   * and nothing useful sits near the boundary. */
  const SLOW_PER_SECOND = 500;

  /* Rates are per second; the decision they inform is about millions. Rounded
   * hard, because the point is "an afternoon, not a coffee break" and a second
   * significant figure would only suggest a precision the measurement lacks. */
  function hoursPerMillion(rate) {
    const hours = 1e6 / rate / 3600;
    if (hours < 1) return Math.round(hours * 60) + " minutes";
    if (hours < 10) return (Math.round(hours * 2) / 2) + " hours";
    return Math.round(hours) + " hours";
  }

  /* ----------------------------------------------------------- model reads */

  function activeCriteria(tier) {
    return tier.criteria.filter((criterion) => criterion.enabled);
  }

  function specFor(criterion) {
    return criterion.criterion ? CRITERIA.get(criterion.criterion) || null : null;
  }

  function optionFor(criterion) {
    const spec = specFor(criterion);
    if (!spec) return null;
    return spec.options.find((option) => option.plugin_ref === criterion.backend) || null;
  }

  /** Whether this criterion emits a complete decision stream of its own. */
  function producesDecision(criterion) {
    if (criterion.gate) return true;
    const spec = specFor(criterion);
    return spec ? spec.evidence === "decision" : null;
  }

  /* Some tools read evidence they do not compute.  Uni-Dock and GNINA dock a 3D
   * structure and generate none, so a cascade that names either one without a
   * conformer block above it cannot be lowered at all -- `lower_cascade` stops
   * with CASCADE_CRITERION_EVIDENCE_UNAVAILABLE.  The builder used to export
   * that file happily, which meant the operator met the problem as a failure of
   * something a tool had just generated for them.  So the dependency is read
   * from the installed plugins into the payload, satisfied automatically when a
   * block is added, and checked before every export. */

  function evidenceNeeds(criterion) {
    const option = optionFor(criterion);
    return (option && option.needs_evidence) || [];
  }

  function evidenceProduced(criterion) {
    const option = optionFor(criterion);
    return (option && option.produces_evidence) || [];
  }

  /** The criterion this installation would use to produce one evidence contract. */
  function evidenceProducer(contract) {
    for (const spec of payload.criteria) {
      for (const option of spec.options) {
        if (!option.executable) continue;
        if ((option.produces_evidence || []).indexOf(contract) < 0) continue;
        return spec;
      }
    }
    return null;
  }

  /** Every enabled block above `criterionId` that produces `contract`, in order.
   *
   * "Above" is the rule `_lower_tier` applies, and it is not simply "earlier in
   * the funnel".  A serial tier threads its criteria one into the next, so an
   * earlier sibling counts.  A parallel tier freezes the producer map before its
   * loop -- every criterion reads the population entering the tier, so that none
   * of them can hide evidence from another by filtering first -- and there a
   * sibling never counts, however early it sits.  Getting this wrong in only one
   * direction is what makes it worth stating: the builder would accept an
   * arrangement lowering rejects, and the operator would meet the refusal as a
   * failure of the file the builder had just written for them.
   */
  function evidenceProducersAbove(criterionId, contract) {
    const producers = [];
    for (const tier of state.tiers) {
      if (!tier.enabled) continue;
      const siblings = tier.mode === "serial";
      const holds = activeCriteria(tier).some((item) => item.id === criterionId);
      for (const item of activeCriteria(tier)) {
        if (item.id === criterionId) break;
        if (holds && !siblings) continue;
        if (evidenceProduced(item).indexOf(contract) >= 0) producers.push(item);
      }
      if (holds) break;
    }
    return producers;
  }

  function missingEvidence(criterion) {
    return evidenceNeeds(criterion).filter(
      (contract) => !evidenceProducersAbove(criterion.id, contract).length
    );
  }

  /* Which producer a criterion reads, when more than one is on.
   *
   * Two docking engines both emit `docking_score/v1`, and each stage's table
   * holds only its own engine's rows, so binding the wrong one hands the reader
   * a table in which none of the rows it expects exist.  `lower_cascade` refuses
   * to guess -- CASCADE_CRITERION_EVIDENCE_AMBIGUOUS -- and `evidence_from` is
   * how a criterion answers, in the exported file where it can be audited.
   *
   * The shipped cascade already answers it; what did not, until now, was every
   * criterion the operator adds from the picker, and every pin left behind after
   * the engine it named was switched off.  Both exported a file that would not
   * lower.  So the pin is maintained as a property of the current arrangement
   * rather than written once: kept while it still names a producer that is on
   * and above, and re-pointed at the first one that is when it does not.
   *
   * First rather than best, because the builder has no way to rank them -- the
   * consumers do, and refuse a scale they cannot read by name.  The inspector
   * shows the choice so it can be corrected without editing the file.
   */
  function reconcileEvidence() {
    for (const tier of state.tiers) {
      for (const criterion of tier.criteria) {
        const needs = evidenceNeeds(criterion);
        const pins = criterion.evidence_from || {};
        const next = {};
        for (const contract of needs) {
          const producers = evidenceProducersAbove(criterion.id, contract);
          if (producers.length < 2) {
            // One producer needs no pin, and none is an error `collectIssues`
            // reports on its own.  Either way a pin here could only go stale.
            continue;
          }
          const held = pins[contract];
          const kept = producers.some((item) => item.id === held);
          next[contract] = kept ? held : producers[0].id;
        }
        if (Object.keys(next).length) criterion.evidence_from = next;
        else delete criterion.evidence_from;
      }
    }
  }

  function usedIds() {
    const used = new Set();
    used.add(state.ingest.id);
    if (state.standardize) used.add(state.standardize.id);
    for (const tier of state.tiers) {
      used.add(tier.id);
      used.add(tier.id + "__policy");
      for (const criterion of tier.criteria) {
        used.add(criterion.id);
        used.add(criterion.id + "__gate");
      }
    }
    for (const step of state.finalize.steps) used.add(step.id);
    return used;
  }

  function uniqueId(base) {
    const used = usedIds();
    const taken = (candidate) =>
      used.has(candidate) || used.has(candidate + "__gate") || used.has(candidate + "__policy");
    if (!taken(base)) return base;
    for (let suffix = 2; suffix < 1000; suffix += 1) {
      const candidate = base + "_" + suffix;
      if (!taken(candidate)) return candidate;
    }
    return base + "_x";
  }

  function findTier(tierId) {
    return state.tiers.findIndex((tier) => tier.id === tierId);
  }

  function tierById(tierId) {
    return state.tiers.find((tier) => tier.id === tierId) || null;
  }

  function locate(criterionId) {
    for (let tierIndex = 0; tierIndex < state.tiers.length; tierIndex += 1) {
      const index = state.tiers[tierIndex].criteria.findIndex((item) => item.id === criterionId);
      if (index >= 0) return { tierIndex: tierIndex, index: index };
    }
    return null;
  }

  function criterionLabel(criterion) {
    if (criterion.label) return criterion.label;
    const spec = specFor(criterion);
    return spec ? spec.label : criterion.id;
  }

  /* ------------------------------------------------------- execution plan */

  /* A faithful mirror of `lower_cascade`.  Keep the two in step: the test
   * `test_the_canvas_shows_exactly_the_stages_that_will_run` compares this list
   * against the stages Python actually compiles, and fails if they diverge.
   *
   * The rules that are easy to get wrong, and are therefore spelled out:
   *   - a disabled tier, criterion or step contributes nothing;
   *   - a tier whose enabled criteria are all gone is skipped entirely;
   *   - a gate stage exists only where the criterion carries a gate;
   *   - a parallel tier joins only when TWO OR MORE criteria reach the join,
   *     because a single-input join would add a stage without changing anyone.
   */
  function planStages() {
    const plan = [];
    const add = (stage, role, label, extra) =>
      plan.push(
        Object.assign({ stage: stage, role: role, label: label }, extra || {})
      );

    add(state.ingest.id, "ingest", "Molecule library");
    if (state.standardize && state.standardize.enabled) {
      add(state.standardize.id, "standardize", "Standardize and register");
    }

    for (const tier of state.tiers) {
      if (!tier.enabled) continue;
      const active = activeCriteria(tier);
      if (!active.length) continue;
      for (const criterion of active) {
        add(criterion.id, "criterion", criterionLabel(criterion), {
          tier: tier.id,
          criterion: criterion.id,
        });
        if (criterion.gate) {
          add(criterion.id + "__gate", "threshold", criterionLabel(criterion) + " threshold", {
            tier: tier.id,
            criterion: criterion.id,
          });
        }
      }
      if (tier.mode !== "serial" && active.length > 1) {
        add(tier.id + "__policy", "policy", tier.title + " policy", { tier: tier.id });
      }
    }

    for (const step of state.finalize.steps) {
      if (!step.enabled) continue;
      add(step.id, "finalize", step.id);
    }
    return plan;
  }

  /* --------------------------------------------------------- model writes */

  function newCriterion(spec, option) {
    return {
      id: uniqueId(spec.id),
      criterion: spec.id,
      backend: option.plugin_ref,
      label: spec.label + " · " + option.engine,
      settings: clone(option.initial_settings),
      gate: option.gate_plugin
        ? { backend: option.gate_plugin, settings: clone(option.initial_gate_settings) }
        : null,
      enabled: true,
    };
  }

  /* Provision what a newly chosen tool reads but does not compute.
   *
   * A serial tier of its own, directly above the one that needs it: the
   * producers are pure evidence with no threshold of their own, so a parallel
   * tier would demand a decision they cannot make.  Added rather than merely
   * complained about because the operator has no way to know that Uni-Dock
   * needs conformers and KarmaDock does not, and the alternative -- exporting a
   * file that will not lower -- is the failure this builder exists to prevent.
   * It is an ordinary block afterwards: movable, editable and removable, and
   * `collectIssues` says so if removing it breaks the cascade.
   */
  function provideEvidenceFor(criterion, tier) {
    const provided = [];
    // By producer rather than by contract: one block that computes two of the
    // things a tool reads must be added once, not twice.
    const added = new Set();
    for (const contract of missingEvidence(criterion)) {
      const spec = evidenceProducer(contract);
      if (!spec || added.has(spec.id)) continue;
      const option = spec.options.find((item) => item.id === spec.default_option_id);
      if (!option) continue;
      added.add(spec.id);
      const host = {
        id: uniqueId("t" + (state.tiers.length + 1) + "_" + spec.id),
        title: spec.label,
        mode: "serial",
        minimum_passes: null,
        criteria: [newCriterion(spec, option)],
        note: null,
        enabled: true,
      };
      state.tiers.splice(Math.max(0, findTier(tier.id)), 0, host);
      provided.push(spec.label);
    }
    return provided;
  }

  function evidenceAdded(provided) {
    if (!provided.length) return "";
    return (
      " It reads evidence it does not compute, so “" +
      provided.join("”, “") +
      "” was added above it."
    );
  }

  /** Insert a fresh criterion; `index` of null means "at the end of the tier". */
  function addCriterion(tierId, specId, index) {
    const spec = CRITERIA.get(specId);
    const tier = tierById(tierId);
    if (!spec || !tier) return;
    const option = spec.options.find((item) => item.id === spec.default_option_id);
    if (!option) {
      setStatus("error", spec.label + " has no tool installed here, so it cannot be added.");
      return;
    }
    const criterion = newCriterion(spec, option);
    const at = index === null || index === undefined ? tier.criteria.length : index;
    tier.criteria.splice(Math.max(0, Math.min(at, tier.criteria.length)), 0, criterion);
    normalizeTier(tier);
    const provided = provideEvidenceFor(criterion, tier);
    selection = { kind: "criterion", tier: tier.id, criterion: criterion.id };
    render();
    announce(
      "ok",
      "Added " + spec.label + " to “" + tier.title + "”." + evidenceAdded(provided)
    );
  }

  /* Swapping the tool resets thresholds to the new tool's reviewed defaults.
   * Carrying numbers across would be silently wrong: SA score runs 1-10 and
   * RAscore runs 0-1, so a "maximum 6" copied between them turns a real filter
   * into a no-op without anyone noticing. */
  function setCriterionOption(criterion, optionId) {
    const spec = specFor(criterion);
    if (!spec) return;
    const option = spec.options.find((item) => item.id === optionId);
    if (!option || !option.executable) return;
    criterion.backend = option.plugin_ref;
    criterion.label = spec.label + " · " + option.engine;
    criterion.settings = clone(option.initial_settings);
    criterion.gate = option.gate_plugin
      ? { backend: option.gate_plugin, settings: clone(option.initial_gate_settings) }
      : null;
    // Swapping the tool can introduce a dependency the old one did not have:
    // KarmaDock predicts its own geometry, Uni-Dock docks one it is given.
    const found = locate(criterion.id);
    const provided = found ? provideEvidenceFor(criterion, state.tiers[found.tierIndex]) : [];
    render();
    announce(
      "warn",
      "Switched to " + option.engine + ". Thresholds were reset to its defaults." +
        evidenceAdded(provided)
    );
  }

  function removeCriterion(tierId, criterionId) {
    const tier = tierById(tierId);
    if (!tier) return;
    tier.criteria = tier.criteria.filter((criterion) => criterion.id !== criterionId);
    normalizeTier(tier);
    if (selection.kind === "criterion" && selection.criterion === criterionId) {
      selection = { kind: "tier", tier: tier.id };
    }
    render();
  }

  /* One rule for reordering: the arrows move a criterion one slot, and a slot
   * past the end of a tier is the neighbouring tier.  Dragging does the same
   * thing continuously; the arrows exist so the same move is reachable from the
   * keyboard, where no drag gesture is possible. */
  function moveCriterion(criterionId, delta) {
    const found = locate(criterionId);
    if (!found) return;
    const tier = state.tiers[found.tierIndex];
    const target = found.index + delta;
    if (target >= 0 && target < tier.criteria.length) {
      const moved = tier.criteria.splice(found.index, 1)[0];
      tier.criteria.splice(target, 0, moved);
      render();
      return;
    }
    const neighbourIndex = found.tierIndex + (delta < 0 ? -1 : 1);
    if (neighbourIndex < 0 || neighbourIndex >= state.tiers.length) {
      announce(
        "warn",
        "That block is already at the " + (delta < 0 ? "top" : "bottom") + " of the cascade."
      );
      return;
    }
    const neighbour = state.tiers[neighbourIndex];
    const moved = tier.criteria.splice(found.index, 1)[0];
    if (delta < 0) neighbour.criteria.push(moved);
    else neighbour.criteria.unshift(moved);
    normalizeTier(tier);
    normalizeTier(neighbour);
    selection = { kind: "criterion", tier: neighbour.id, criterion: moved.id };
    render();
    announce("ok", "Moved to “" + neighbour.title + "”.");
  }

  /** Drop a criterion into `tierId` at `index`, from anywhere in the cascade. */
  function placeCriterion(criterionId, tierId, index) {
    const found = locate(criterionId);
    const target = tierById(tierId);
    if (!found || !target) return;
    const source = state.tiers[found.tierIndex];
    let at = index === null || index === undefined ? target.criteria.length : index;
    // Removing the block first shifts every later slot in its own tier down by
    // one, so a drop below the original position has to shift with it.
    if (source.id === target.id && found.index < at) at -= 1;
    const moved = source.criteria.splice(found.index, 1)[0];
    at = Math.max(0, Math.min(at, target.criteria.length));
    target.criteria.splice(at, 0, moved);
    normalizeTier(source);
    normalizeTier(target);
    selection = { kind: "criterion", tier: target.id, criterion: moved.id };
    render();
    if (source.id === target.id) announce("ok", "Reordered “" + criterionLabel(moved) + "”.");
    else announce("ok", "Moved “" + criterionLabel(moved) + "” to “" + target.title + "”.");
  }

  function moveCriterionToTier(criterionId, tierId) {
    const found = locate(criterionId);
    const target = tierById(tierId);
    if (!found || !target) return;
    if (state.tiers[found.tierIndex].id === target.id) return;
    placeCriterion(criterionId, tierId, target.criteria.length);
  }

  function addTier(index) {
    const id = uniqueId("t" + (state.tiers.length + 1) + "_custom");
    const tier = {
      id: id,
      title: "New tier",
      mode: "serial",
      minimum_passes: null,
      criteria: [],
      note: null,
      enabled: true,
    };
    const at = index === null || index === undefined ? state.tiers.length : index;
    state.tiers.splice(Math.max(0, Math.min(at, state.tiers.length)), 0, tier);
    selection = { kind: "tier", tier: id };
    render();
    announce("ok", "Added a tier. Name it, then drag blocks into it.");
  }

  function moveTier(tierId, delta) {
    const index = findTier(tierId);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= state.tiers.length) return;
    const moved = state.tiers.splice(index, 1)[0];
    state.tiers.splice(target, 0, moved);
    render();
  }

  /** Drop a tier so that it lands *before* the tier currently at `index`. */
  function placeTier(tierId, index) {
    const from = findTier(tierId);
    if (from < 0) return;
    let at = index;
    if (from < at) at -= 1;
    if (at === from) return;
    const moved = state.tiers.splice(from, 1)[0];
    state.tiers.splice(Math.max(0, Math.min(at, state.tiers.length)), 0, moved);
    selection = { kind: "tier", tier: moved.id };
    render();
    announce("ok", "“" + moved.title + "” is now tier " + (findTier(moved.id) + 1) + ".");
  }

  function removeTier(tierId) {
    const tier = tierById(tierId);
    if (!tier) return;
    const question =
      "Delete “" + tier.title + "” and its " + countCriteria(tier.criteria.length) + "?";
    if (tier.criteria.length && !window.confirm(question)) return;
    state.tiers = state.tiers.filter((item) => item.id !== tierId);
    if (selection.tier === tierId) selection = { kind: "library" };
    render();
  }

  /* `minimum_passes` is only meaningful for the at-least mode.  Switching into
   * that mode seeds a majority vote; an existing K is never silently clamped,
   * because quietly lowering a consensus threshold is a change to the science,
   * not a repair.  An unsatisfiable K is reported instead. */
  function normalizeTier(tier) {
    if (tier.mode === "at_least") {
      if (tier.minimum_passes === null || tier.minimum_passes === undefined) {
        // A strict majority, so two criteria start out needing both to agree
        // rather than quietly behaving like "any".
        const count = activeCriteria(tier).length;
        tier.minimum_passes = Math.max(1, Math.ceil((count + 1) / 2));
      }
    } else {
      tier.minimum_passes = null;
    }
  }

  function setTierMode(tier, mode) {
    tier.mode = mode;
    normalizeTier(tier);
    render();
  }

  /* ------------------------------------------------------------ validation */

  /** Which settings container one threshold descriptor writes into. */
  function settingsFor(criterion, descriptor) {
    return descriptor.target === "gate"
      ? (criterion.gate || {}).settings
      : criterion.settings;
  }

  /* A text setting the run cannot start without, left blank.
   *
   * This used to be exportable, and the consequence was the whole reason this
   * check exists: a cascade whose KarmaDock path was an empty string downloaded
   * cleanly, then failed at `molcascade validate` with Pydantic's
   * `string_too_short` on a field the operator had never seen named. Every such
   * file had to be hand-edited after export, which is exactly what a builder is
   * for avoiding.
   *
   * `required` is honoured alongside `!nullable` because they answer different
   * questions: `nullable` is whether the *plugin's* field accepts null, and
   * `required` is whether a cascade built *here* can run without it. See
   * `ThresholdField` for the one field where those disagree.
   *
   * Installation paths are excluded, and that exclusion is the whole point of
   * the split below. They are not settings a cascade carries -- they say where
   * an engine is installed on a host this browser is usually not running on --
   * so blocking a download over one made the builder unable to draw a docking
   * cascade at all on a laptop, while `preflight_engine_paths` was already
   * prepared to resolve them where it mattered. */
  function missingRequiredSettings(criterion) {
    const option = optionFor(criterion);
    if (!option) return [];
    const missing = [];
    for (const descriptor of option.thresholds || []) {
      if (descriptor.kind !== "text") continue;
      if (!descriptor.required && descriptor.nullable) continue;
      if (descriptor.installed_path) continue;
      const container = settingsFor(criterion, descriptor);
      const value = container ? getPath(container, descriptor.name) : undefined;
      if (typeof value === "string" && value.trim()) continue;
      missing.push("“" + criterionLabel(criterion) + "” needs " + descriptor.label + ".");
    }
    return missing;
  }

  /* The installation paths this criterion is leaving to whoever runs it.
   *
   * Said once per block rather than once per field, and as a note rather than a
   * complaint: there is nothing here for the author to fix, and the run does not
   * proceed in ignorance either. `preflight_engine_paths` looks for each of
   * these before the first molecule is read -- in the config, in
   * `MOLCASCADE_<ENGINE>_<FIELD>`, then where `envs/bootstrap.sh` installs
   * things -- and stops with the install command if it finds nothing. */
  function unresolvedMachinePaths(criterion) {
    const option = optionFor(criterion);
    if (!option) return null;
    const labels = [];
    for (const descriptor of option.thresholds || []) {
      if (descriptor.kind !== "text" || !descriptor.installed_path) continue;
      const container = settingsFor(criterion, descriptor);
      const value = container ? getPath(container, descriptor.name) : undefined;
      if (typeof value === "string" && value.trim()) continue;
      labels.push(descriptor.label);
    }
    if (!labels.length) return null;
    return (
      "“" + criterionLabel(criterion) + "” leaves " + labels.join(" and ") +
      " to the machine that runs it. MolCascade looks for them when the run " +
      "starts and stops with the command that installs them if they are absent."
    );
  }

  function collectIssues() {
    const issues = [];
    const error = (text, where) =>
      issues.push({ level: "error", text: text, where: where || null });
    const warn = (text, where) => issues.push({ level: "warn", text: text, where: where || null });

    if (!state.name || !String(state.name).trim()) {
      error("The cascade needs a name.");
    }
    if (!Number.isInteger(state.finalize.target_count) || state.finalize.target_count < 1) {
      error("The shortlist target must be a whole number of at least 1.");
    }

    const seen = new Map();
    const claim = (id, where) => {
      if (seen.has(id)) error("Two stages are both called “" + id + "”.", where);
      else seen.set(id, where);
    };
    claim(state.ingest.id, "library");
    if (state.standardize) claim(state.standardize.id, "standardize");

    let enabledCriteria = 0;
    let tierIndex = -1;
    for (const tier of state.tiers) {
      if (tier.enabled) tierIndex += 1;
      claim(tier.id + "__policy", tier.id);
      const active = activeCriteria(tier);
      for (const criterion of tier.criteria) {
        claim(criterion.id, tier.id);
        claim(criterion.id + "__gate", tier.id);
      }
      if (!tier.enabled) continue;
      enabledCriteria += active.length;
      if (!active.length) {
        warn("Tier “" + tier.title + "” has no enabled blocks and will be skipped.", tier.id);
        continue;
      }
      if (tier.mode === "at_least") {
        if (tier.minimum_passes === null || tier.minimum_passes === undefined) {
          error("Tier “" + tier.title + "” needs a number of passing criteria.", tier.id);
        } else if (tier.minimum_passes > active.length) {
          error(
            "Tier “" + tier.title + "” needs " + tier.minimum_passes +
              " criteria to pass, but only " + criteriaAre(active.length) + " enabled.",
            tier.id
          );
        }
      }
      if (tier.mode !== "serial") {
        if (active.length > 1 && !payload.join_plugin_available) {
          error(
            "Parallel tiers need " + payload.join_plugin + ", which is not installed here.",
            tier.id
          );
        }
        for (const criterion of active) {
          if (producesDecision(criterion) === false) {
            error(
              "“" + criterionLabel(criterion) + "” produces a score, not a decision. " +
                "Give it a threshold, or set this tier to Serial.",
              tier.id
            );
          }
        }
      }
      for (const criterion of active) {
        const spec = specFor(criterion);
        if (spec && !optionFor(criterion)) {
          warn(
            "“" + criterionLabel(criterion) + "” uses " + criterion.backend +
              ", which this builder does not know. It will be exported unchanged.",
            tier.id
          );
        }
        for (const issue of missingRequiredSettings(criterion)) {
          error(issue, tier.id);
        }
        // An unmet evidence dependency is not a preference: lowering refuses
        // the cascade outright, so this has to stop the export rather than
        // colour the status bar.  Adding a block satisfies it automatically; a
        // file loaded from disk, or one whose producer was moved below its
        // consumer, arrives here instead.
        for (const contract of missingEvidence(criterion)) {
          const producer = evidenceProducer(contract);
          error(
            "“" + criterionLabel(criterion) + "” reads evidence it does not compute, and " +
              "no enabled block above it produces it. " +
              (producer
                ? "Add “" + producer.label + "” to a tier above this one."
                : "Nothing installed here produces " + contract + "."),
            tier.id
          );
        }
        const machinePaths = unresolvedMachinePaths(criterion);
        if (machinePaths) warn(machinePaths, tier.id);
      }
      // The first tier is the only one that sees every molecule in the library,
      // so it is the only place where a slow check costs its full price. The
      // recorded rates span four orders of magnitude, and the difference is
      // invisible until a run is already hours old -- which is too late to move
      // a block. Said in hours-per-million because that is the number someone
      // screening a generated library can actually act on.
      if (tierIndex === 0) {
        for (const criterion of active) {
          const option = optionFor(criterion);
          const rate = option && option.throughput_per_second;
          if (!rate || rate >= SLOW_PER_SECOND) continue;
          warn(
            "“" + criterionLabel(criterion) + "” runs at about " + rate +
              " molecules/s per lane, so in the first tier it costs roughly " +
              hoursPerMillion(rate) + " per million molecules. Cheaper blocks " +
              "above it would leave it far less to read.",
            tier.id
          );
        }
      }
    }

    const enabledSteps = state.finalize.steps.filter((step) => step.enabled).length;
    if (!enabledCriteria && !enabledSteps) {
      error("A cascade needs at least one enabled criterion or finalize step.");
    }
    if (!enabledCriteria) {
      warn("No criteria are enabled: every molecule would reach the shortlist.");
    }
    return issues;
  }

  function blockingIssues() {
    return collectIssues().filter((issue) => issue.level === "error");
  }

  /* -------------------------------------------------------------- dragging */

  /* Drag state lives in a module variable rather than in `dataTransfer`.  A
   * browser only lets a drop handler read `dataTransfer`, never a dragover
   * handler, and dragover is where the decision "may this land here?" has to be
   * made.  The transfer object is still filled in so that dragging out of the
   * window behaves like a normal drag. */
  function draggable(node, descriptor) {
    node.setAttribute("draggable", "true");
    node.addEventListener("dragstart", (event) => {
      drag = descriptor;
      node.classList.add("is-dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        try {
          event.dataTransfer.setData("text/plain", descriptor.label || descriptor.kind);
        } catch (ignored) {
          /* Some browsers refuse text/plain on synthetic drags; the module
           * variable above is the authoritative channel either way. */
        }
      }
    });
    node.addEventListener("dragend", () => {
      drag = null;
      node.classList.remove("is-dragging");
      clearDropHighlights();
    });
    return node;
  }

  const dropZones = [];

  function clearDropHighlights() {
    for (const zone of dropZones) zone.classList.remove("is-over");
  }

  /** Wire `node` as a drop target; `accepts(drag)` decides, `land(drag)` acts. */
  function dropZone(node, accepts, land) {
    dropZones.push(node);
    node.addEventListener("dragover", (event) => {
      if (!drag || !accepts(drag)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
      node.classList.add("is-over");
    });
    node.addEventListener("dragenter", (event) => {
      if (!drag || !accepts(drag)) return;
      event.preventDefault();
      node.classList.add("is-over");
    });
    node.addEventListener("dragleave", () => node.classList.remove("is-over"));
    node.addEventListener("drop", (event) => {
      if (!drag || !accepts(drag)) return;
      event.preventDefault();
      event.stopPropagation();
      const landed = drag;
      drag = null;
      node.classList.remove("is-over");
      land(landed);
    });
    return node;
  }

  const acceptsBrick = (item) => item.kind === "spec" || item.kind === "criterion";
  const acceptsTier = (item) => item.kind === "tier";

  /** Land a brick -- new from the tray, or moved from another tier. */
  function landBrick(item, tierId, index) {
    if (item.kind === "spec") addCriterion(tierId, item.spec, index);
    else placeCriterion(item.criterion, tierId, index);
  }

  /* --------------------------------------------------------------- widgets */

  function iconButton(glyph, label, focusKey, handler, disabled) {
    return el("button", {
      class: "icon-button",
      type: "button",
      title: label,
      "aria-label": label,
      disabled: Boolean(disabled),
      text: glyph,
      dataset: { focusKey: focusKey },
      on: {
        click: (event) => {
          event.stopPropagation();
          handler();
        },
      },
    });
  }

  function selectable(node, target) {
    node.tabIndex = 0;
    node.setAttribute("role", "button");
    node.addEventListener("click", () => {
      selection = target;
      render();
    });
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selection = target;
        render();
      }
    });
    return node;
  }

  function isSelected(target) {
    if (selection.kind !== target.kind) return false;
    if (target.kind === "criterion") return selection.criterion === target.criterion;
    if (target.kind === "tier") return selection.tier === target.tier;
    return true;
  }

  function chip(text, kind, stageId) {
    return el("span", {
      class: "chip" + (kind ? " " + kind : ""),
      text: text,
      dataset: stageId ? { stage: stageId } : null,
    });
  }

  /* --------------------------------------------------------------- the tray */

  function toolsSummary(spec) {
    const installed = spec.options.filter((option) => option.executable).length;
    return installed + " of " + spec.options.length + " tools installed";
  }

  function matchesQuery(spec, query) {
    if (!query) return true;
    const haystack = [spec.label, spec.question, spec.summary]
      .concat(spec.options.map((option) => option.label + " " + option.engine))
      .join(" ")
      .toLowerCase();
    return query
      .toLowerCase()
      .split(/\s+/)
      .every((term) => haystack.indexOf(term) >= 0);
  }

  /** Where a keyboard "add" lands: the selected tier, else the last one. */
  function targetTierId() {
    if (selection.tier && tierById(selection.tier)) return selection.tier;
    return state.tiers.length ? state.tiers[state.tiers.length - 1].id : null;
  }

  function trayBrick(spec) {
    const runnable = spec.has_executable_option;
    const accelerated = spec.options.some((option) => option.executable && option.accelerated);
    const brick = el(
      "button",
      {
        class: "brick",
        type: "button",
        disabled: !runnable,
        title: spec.question,
        dataset: { focusKey: "brick:" + spec.id, spec: spec.id, drag: "spec" },
        on: {
          click: () => {
            const tierId = targetTierId();
            if (!tierId) {
              setStatus("error", "Add a tier first, then drop blocks into it.");
              return;
            }
            addCriterion(tierId, spec.id, null);
          },
        },
      },
      [
        el("div", { class: "brick-name", text: spec.label }),
        el("div", {
          class: "brick-meta",
          text:
            toolsSummary(spec) + (accelerated && !payload.machine.gpu_backends_runnable ? " · GPU tool" : ""),
        }),
      ]
    );
    brick.style.setProperty("--accent", accentFor(spec.stage));
    if (!runnable) return brick;
    return draggable(brick, { kind: "spec", spec: spec.id, label: spec.label });
  }

  function renderTray() {
    const query = dom.traySearch.value.trim();
    const groups = [];
    for (const stage of payload.stages) {
      const specs = payload.criteria.filter(
        (spec) => spec.stage === stage.id && matchesQuery(spec, query)
      );
      if (!specs.length) continue;
      const head = el("div", { class: "tray-group-head" }, [
        el("h3", { text: stage.title }),
        el("span", { text: stage.subtitle }),
      ]);
      head.style.setProperty("--accent", stage.accent);
      groups.push(
        el("div", { class: "tray-group" }, [
          head,
          el("div", { class: "tray-bricks" }, specs.map(trayBrick)),
        ])
      );
    }
    if (!groups.length) {
      groups.push(el("p", { class: "inspector-note", text: "Nothing matches that search." }));
    }
    replace(dom.tray, groups);
  }

  /* ------------------------------------------------------------ the machine */

  function acceleratedCriteria() {
    let count = 0;
    for (const tier of state.tiers) {
      if (!tier.enabled) continue;
      for (const criterion of activeCriteria(tier)) {
        const option = optionFor(criterion);
        if (option && option.accelerated) count += 1;
      }
    }
    return count;
  }

  function renderMachine() {
    const machine = payload.machine;
    const rows = [
      ["Platform", machine.platform],
      [
        "CPU",
        machine.cpu_name + (machine.cpu_cores ? " · " + plural(machine.cpu_cores, "core") : ""),
      ],
      ["Memory", machine.memory_gib === null ? "unknown" : machine.memory_gib + " GiB"],
      [
        "GPU",
        machine.gpu_count
          ? machine.gpu_count + " × " + machine.gpus[0].name +
            (machine.total_gpu_memory_gib ? " · " + machine.total_gpu_memory_gib + " GiB" : "")
          : "none detected",
      ],
      ["Plan", machine.device + " · " + plural(machine.workers, "worker")],
    ];
    const list = el(
      "dl",
      {},
      rows.reduce(
        (nodes, row) =>
          nodes.concat([el("dt", { text: row[0] }), el("dd", { text: row[1] })]),
        []
      )
    );

    const accelerated = acceleratedCriteria();
    const notes = [];
    if (accelerated && !machine.gpu_backends_runnable) {
      notes.push(
        el("p", {
          class: "machine-note warn",
          // The builder is normally opened on a laptop and the cascade is run
          // on a GPU node.  Saying so here is the difference between a slow
          // afternoon and a surprised evening.
          text:
            plural(accelerated, "block") +
            " in this cascade use a GPU-accelerated tool. This machine has none, " +
            "so they would fall back to CPU here. The file itself is fine — run it " +
            "where the GPU is.",
        })
      );
    }
    notes.push(el("p", { class: "machine-note", text: machine.rationale }));

    replace(
      dom.machine,
      [el("h3", { text: "Where this file was written" }), list].concat(notes)
    );
  }

  /* --------------------------------------------------------- funnel render */

  function arrow() {
    return el("div", { class: "flow-arrow", "aria-hidden": "true" });
  }

  /** A thin insertion target between two blocks inside a tier. */
  function slot(tier, index) {
    return dropZone(
      el("div", {
        class: "slot",
        dataset: { drop: "slot", tier: tier.id, index: index },
      }),
      acceptsBrick,
      (item) => landBrick(item, tier.id, index)
    );
  }

  function fixedNode(target, title, subtitle, stageId, extra) {
    const glyph = el("span", { class: "node-glyph", "aria-hidden": "true" });
    const head = el("div", { class: "node-head" }, [
      glyph,
      el("div", { class: "node-title" }, [
        el("h3", { text: title }),
        el("div", { class: "node-sub", text: subtitle }),
      ]),
      extra || null,
    ]);
    selectable(head, target);
    return el(
      "article",
      {
        class: "node" + (isSelected(target) ? " selected" : ""),
        dataset: stageId ? { node: target.kind, stage: stageId } : { node: target.kind },
      },
      [head]
    );
  }

  function blockSummary(criterion, spec, option) {
    const parts = [];
    if (option) {
      for (const descriptor of option.thresholds) {
        if (descriptor.name === "batch_size") continue;
        const value = getPath(settingsFor(criterion, descriptor) || {}, descriptor.name);
        if (value === null || value === undefined || value === "") continue;
        parts.push(descriptor.label + " " + formatValue(value, descriptor));
        if (parts.length >= 2) break;
      }
    }
    if (!parts.length) {
      if (producesDecision(criterion) === false) parts.push("score only — no threshold");
      else if (spec) parts.push(spec.question);
      else parts.push(criterion.backend);
    }
    return parts.join(" · ");
  }

  function formatValue(value, descriptor) {
    if (typeof value === "boolean") return value ? "on" : "off";
    if (descriptor && descriptor.kind === "choice") {
      const choice = descriptor.choices.find((item) => item.value === value);
      if (choice) return choice.label.toLowerCase();
    }
    return String(value);
  }

  /* One criterion, drawn as a brick.  The gate chip is not decoration: it is
   * the `<criterion>__gate` stage, and it carries that stage id. */
  function criterionBlock(tier, criterion, live) {
    const spec = specFor(criterion);
    const option = optionFor(criterion);
    const target = { kind: "criterion", tier: tier.id, criterion: criterion.id };

    const meta = [chip(option ? option.engine : criterion.backend, "engine")];
    if (criterion.gate) {
      meta.push(chip("gate", "gate", live ? criterion.id + "__gate" : null));
    }
    if (option && option.accelerated) {
      meta.push(chip(payload.machine.gpu_backends_runnable ? "GPU" : "GPU · none here",
        payload.machine.gpu_backends_runnable ? "accel" : "alarm"));
    }
    if (!criterion.enabled) meta.push(chip("off", null));

    const block = el(
      "div",
      {
        class:
          "block" +
          (isSelected(target) ? " selected" : "") +
          (criterion.enabled ? "" : " disabled"),
        dataset: {
          tier: tier.id,
          criterion: criterion.id,
          drag: "criterion",
          stage: live ? criterion.id : null,
        },
      },
      [
        el("div", { class: "block-head" }, [
          el("div", { class: "block-name", text: criterionLabel(criterion) }),
          el("div", { class: "card-actions" }, [
            iconButton("▲", "Move up (or into the tier above)", "cu:" + criterion.id, () =>
              moveCriterion(criterion.id, -1)
            ),
            iconButton("▼", "Move down (or into the tier below)", "cd:" + criterion.id, () =>
              moveCriterion(criterion.id, 1)
            ),
            iconButton(
              criterion.enabled ? "◉" : "○",
              criterion.enabled ? "Disable this block" : "Enable this block",
              "ct:" + criterion.id,
              () => {
                criterion.enabled = !criterion.enabled;
                normalizeTier(tier);
                render();
              }
            ),
            iconButton("✕", "Remove this block", "cx:" + criterion.id, () =>
              removeCriterion(tier.id, criterion.id)
            ),
          ]),
        ]),
        el("div", { class: "block-meta" }, meta),
        el("div", { class: "brick-meta", text: blockSummary(criterion, spec, option) }),
      ]
    );
    block.style.setProperty("--accent", accentFor(spec ? spec.stage : ""));
    selectable(block, target);
    return draggable(block, {
      kind: "criterion",
      criterion: criterion.id,
      label: criterionLabel(criterion),
    });
  }

  /* The join is the tier's `__policy` stage.  It is drawn only when lowering
   * would really emit it, so the canvas never promises a vote that the run will
   * quietly skip. */
  function joinBlock(tier, active) {
    const mode = MODES.get(tier.mode);
    const label =
      tier.mode === "at_least"
        ? "at least " + tier.minimum_passes + " of " + active.length + " must pass"
        : mode
          ? mode.short
          : tier.mode;
    const missing = !payload.join_plugin_available;
    return el("div", { class: "join-wrap" }, [
      el(
        "div",
        {
          class: "join-block" + (missing ? " missing" : ""),
          dataset: { stage: tier.id + "__policy" },
        },
        [
          el("strong", { text: missing ? "join unavailable — " + label : "Join: " + label }),
          el("span", { class: "join-id", text: tier.id + "__policy" }),
        ]
      ),
    ]);
  }

  function tierSocket(tier, live) {
    const parallel = tier.mode !== "serial";
    const socket = el("div", {
      class: "socket " + (parallel ? "parallel" : "serial"),
      dataset: { drop: "socket", tier: tier.id },
    });
    dropZone(socket, acceptsBrick, (item) => landBrick(item, tier.id, null));

    if (!tier.criteria.length) {
      socket.appendChild(
        el("div", {
          class: "empty-socket",
          text: "Drop a block here, or press + Add block.",
        })
      );
      return socket;
    }

    tier.criteria.forEach((criterion, index) => {
      socket.appendChild(slot(tier, index));
      if (!parallel && index > 0) socket.appendChild(el("div", { class: "chain" }));
      socket.appendChild(criterionBlock(tier, criterion, live && criterion.enabled));
    });
    socket.appendChild(slot(tier, tier.criteria.length));
    return socket;
  }

  function tierCard(tier, position) {
    const mode = MODES.get(tier.mode);
    const active = activeCriteria(tier);
    const target = { kind: "tier", tier: tier.id };
    const index = findTier(tier.id);
    const live = tier.enabled && active.length > 0;

    const head = el("div", { class: "tier-head" }, [
      draggable(
        el("span", {
          class: "tier-grip",
          "aria-hidden": "true",
          text: "⣿",
          dataset: { drag: "tier", tier: tier.id },
        }),
        { kind: "tier", tier: tier.id, label: tier.title }
      ),
      el("span", { class: "tier-index", "aria-hidden": "true", text: String(position) }),
      el("div", { class: "tier-title" }, [
        el("h3", { text: tier.title }),
        el("div", {
          class: "tier-sub",
          text: countCriteria(active.length) + " · " + (mode ? mode.short : tier.mode),
        }),
      ]),
      el("span", {
        class: "mode-chip " + (tier.mode === "serial" ? "serial" : "parallel"),
        text: mode ? mode.label : tier.mode,
      }),
      el("div", { class: "card-actions" }, [
        iconButton("▲", "Move tier up", "tu:" + tier.id, () => moveTier(tier.id, -1), index === 0),
        iconButton(
          "▼",
          "Move tier down",
          "td:" + tier.id,
          () => moveTier(tier.id, 1),
          index === state.tiers.length - 1
        ),
        iconButton(
          tier.enabled ? "◉" : "○",
          tier.enabled ? "Disable this tier" : "Enable this tier",
          "tt:" + tier.id,
          () => {
            tier.enabled = !tier.enabled;
            render();
          }
        ),
        iconButton("✕", "Delete this tier", "tx:" + tier.id, () => removeTier(tier.id)),
      ]),
    ]);
    selectable(head, target);

    const body = el("div", { class: "tier-body" }, [
      tierSocket(tier, live),
      live && tier.mode !== "serial" && active.length > 1 ? joinBlock(tier, active) : null,
      ...runTimeNeeds(tier, live).map((need) =>
        el("div", { class: "tier-needs" }, [
          el("span", { class: "tier-needs-tag", text: "At run time" }),
          el("span", { text: need }),
        ])
      ),
      tier.note ? el("div", { class: "tier-note", text: tier.note }) : null,
      el("div", { class: "tier-footer" }, [
        el("button", {
          class: "button small",
          type: "button",
          text: "+ Add block",
          dataset: { focusKey: "add:" + tier.id },
          on: { click: () => openPicker(tier.id) },
        }),
      ]),
    ]);

    const card = el(
      "article",
      {
        class:
          "tier" + (isSelected(target) ? " selected" : "") + (tier.enabled ? "" : " disabled"),
        dataset: { tier: tier.id },
      },
      [head, body]
    );
    card.style.setProperty("--accent", accentFor(tierAccentKey(tier)));
    return card;
  }

  /* Some blocks cannot be finished in this file.  A receptor belongs to a
   * campaign rather than to a screening policy, so the docking groups declare
   * what the command line still has to supply and the tier says so while the
   * cascade is being written -- not an hour later, when the preflight refuses
   * to start a run that has already been scheduled.
   *
   * Only live tiers, because only live tiers compile into stages: a disabled
   * docking tier demands nothing, and saying otherwise would train people to
   * read these lines as decoration. */
  function runTimeNeeds(tier, live) {
    if (!live) return [];
    const seen = [];
    for (const criterion of activeCriteria(tier)) {
      const spec = specFor(criterion);
      const stage = spec ? STAGES.get(spec.stage) : null;
      const need = stage && stage.run_time_requirement;
      if (need && seen.indexOf(need) === -1) seen.push(need);
    }
    return seen;
  }

  /* A tier has no stage group of its own, so it borrows the accent of whatever
   * its blocks mostly measure.  An empty tier stays neutral. */
  function tierAccentKey(tier) {
    for (const criterion of tier.criteria) {
      const spec = specFor(criterion);
      if (spec) return spec.stage;
    }
    return "";
  }

  function tierGap(index) {
    // Only the last gap is an edge.  The first one is not: molecules arrive
    // into tier 1 from standardization directly above it, so it carries the
    // connector like every other gap -- it used to draw nothing and have a
    // separate arrow stacked above it instead, which left a blank 16px seam
    // between the end of that arrow and the top of the tier.  Below the last
    // tier comes the "add tier" button, which is not part of the cascade, so
    // that gap still draws nothing.
    const edge = index === state.tiers.length;
    return dropZone(
      el("div", {
        class: "tier-gap" + (edge ? " edge" : ""),
        dataset: { drop: "gap", index: index },
      }),
      acceptsTier,
      (item) => placeTier(item.tier, index)
    );
  }

  function renderFunnel() {
    const focusKey =
      document.activeElement && document.activeElement.dataset
        ? document.activeElement.dataset.focusKey
        : null;

    dropZones.length = 0;
    const nodes = [];
    const format = payload.library_formats.find((item) => item.value === state.library.format);
    nodes.push(
      fixedNode(
        { kind: "library" },
        "Molecule library",
        (format ? format.label : state.library.format) + " · supplied with --library",
        state.ingest.id
      )
    );

    if (state.standardize) {
      nodes.push(arrow());
      nodes.push(
        fixedNode(
          { kind: "standardize" },
          "Standardize and register",
          state.standardize.enabled
            ? "One parent structure per unique molecule"
            : "Disabled — molecules are screened as written",
          state.standardize.enabled ? state.standardize.id : null,
          iconButton(
            state.standardize.enabled ? "◉" : "○",
            state.standardize.enabled ? "Disable standardization" : "Enable standardization",
            "std",
            () => {
              state.standardize.enabled = !state.standardize.enabled;
              render();
            }
          )
        )
      );
    }

    state.tiers.forEach((tier, index) => {
      nodes.push(tierGap(index));
      nodes.push(tierCard(tier, index + 1));
    });
    nodes.push(tierGap(state.tiers.length));

    nodes.push(
      el("div", { class: "add-tier-row" }, [
        el("button", {
          class: "button",
          type: "button",
          text: "+ Add tier",
          dataset: { focusKey: "add-tier" },
          on: { click: () => addTier(null) },
        }),
      ])
    );

    // Whatever survives the last tier still has somewhere to go, so the funnel
    // is drawn as arriving at the shortlist rather than stopping at the button.
    const steps = state.finalize.steps.filter((step) => step.enabled);
    nodes.push(arrow());
    nodes.push(
      fixedNode(
        { kind: "finalize" },
        "Shortlist",
        "Up to " +
          state.finalize.target_count.toLocaleString("en-US") +
          " molecules · " +
          plural(steps.length, "step") +
          " · hand off to docking",
        null,
        el(
          "div",
          { class: "block-meta" },
          steps.map((step) => chip(step.id, null, step.id))
        )
      )
    );

    replace(dom.funnel, nodes);

    if (focusKey) {
      const restored = dom.funnel.querySelector('[data-focus-key="' + CSS.escape(focusKey) + '"]');
      if (restored) restored.focus();
    }
  }

  /* ------------------------------------------------------ inspector render */

  function field(label, control, help) {
    return el("label", { class: "field" }, [
      el("span", { text: label }),
      control,
      help ? el("span", { class: "field-help", text: help }) : null,
    ]);
  }

  function checkboxField(label, checked, onChange, help) {
    return el("div", {}, [
      el("label", { class: "checkbox-row" }, [
        el("input", { type: "checkbox", checked: checked, on: { change: onChange } }),
        el("span", { text: label }),
      ]),
      help ? el("span", { class: "field-help", text: help }) : null,
    ]);
  }

  function numberControl(value, descriptor, onCommit) {
    return el("input", {
      type: "number",
      value: value === null || value === undefined ? "" : value,
      min:
        descriptor.minimum === null || descriptor.minimum === undefined
          ? null
          : descriptor.minimum,
      max:
        descriptor.maximum === null || descriptor.maximum === undefined
          ? null
          : descriptor.maximum,
      step: descriptor.step || (descriptor.kind === "integer" ? 1 : "any"),
      inputmode: descriptor.kind === "integer" ? "numeric" : "decimal",
      on: {
        change: (event) => {
          const raw = event.target.value.trim();
          if (raw === "") {
            if (descriptor.nullable) onCommit(null);
            else render();
            return;
          }
          const parsed = descriptor.kind === "integer" ? parseInt(raw, 10) : parseFloat(raw);
          if (!Number.isFinite(parsed)) {
            setStatus("error", descriptor.label + " must be a number.");
            render();
            return;
          }
          onCommit(parsed);
        },
      },
    });
  }

  function thresholdControl(descriptor, value, onCommit) {
    if (descriptor.kind === "boolean") {
      return checkboxField(
        descriptor.label,
        Boolean(value),
        (event) => onCommit(event.target.checked),
        descriptor.help
      );
    }
    if (descriptor.kind === "choice") {
      const select = el("select", {
        on: { change: (event) => onCommit(event.target.value) },
      });
      for (const choice of descriptor.choices) {
        select.appendChild(el("option", { value: choice.value, text: choice.label }));
      }
      select.value = value === null || value === undefined ? "" : String(value);
      return field(descriptor.label, select, descriptor.help);
    }
    if (descriptor.kind === "text") {
      // An installation path is shown as optional, because it is: the run
      // resolves it on the host that has the engine, which is usually not this
      // one. The variable is still named, since an engine installed somewhere
      // unusual is exactly the case the search cannot cover -- and naming it
      // rather than this box keeps one machine's layout out of a shared file.
      let help = descriptor.help;
      let placeholder = null;
      if (descriptor.installed_path) {
        placeholder = "found when the run starts";
        help =
          (help ? help + " " : "") +
          "Optional: MolCascade looks for this on the machine that runs the " +
          "cascade. Fill it in, or export " + descriptor.environment_variable +
          " there, only if the engine is installed somewhere unusual.";
      }
      return field(
        descriptor.label,
        el("input", {
          type: "text",
          value: value === null || value === undefined ? "" : value,
          placeholder: placeholder,
          on: {
            change: (event) => {
              const raw = event.target.value;
              onCommit(raw === "" && descriptor.nullable ? null : raw);
            },
          },
        }),
        help
      );
    }
    const unit = descriptor.unit ? " (" + descriptor.unit + ")" : "";
    const help = descriptor.nullable
      ? (descriptor.help ? descriptor.help + " " : "") + "Leave blank for no limit."
      : descriptor.help;
    return field(descriptor.label + unit, numberControl(value, descriptor, onCommit), help);
  }

  function section(title, children) {
    return el("div", { class: "inspector-section" }, [el("h3", { text: title })].concat(children));
  }

  function criterionInspector() {
    const tier = tierById(selection.tier);
    const criterion = tier
      ? tier.criteria.find((item) => item.id === selection.criterion)
      : null;
    if (!criterion) return null;
    const spec = specFor(criterion);
    const option = optionFor(criterion);
    const stage = spec ? STAGES.get(spec.stage) : null;

    const nodes = [
      el("h3", { text: criterionLabel(criterion) }),
      el("p", {
        class: "inspector-note",
        text: (stage ? stage.title + " · " : "") + (spec ? spec.question : criterion.backend),
      }),
    ];

    if (spec) {
      const tools = spec.options.map((item) =>
        el(
          "label",
          {
            class:
              "tool-option" +
              (option && item.id === option.id ? " active" : "") +
              (item.executable ? "" : " unavailable"),
          },
          [
            el("input", {
              type: "radio",
              name: "criterion-tool",
              value: item.id,
              checked: Boolean(option && item.id === option.id),
              disabled: !item.executable,
              on: { change: () => setCriterionOption(criterion, item.id) },
            }),
            el("div", {}, [
              el("strong", { text: item.label }),
              el("small", {
                // What a tool needs belongs next to the tool, not in a README:
                // an optional package that is not installed yet is the
                // difference between a cascade that runs and one that stops on
                // its first batch.
                text:
                  item.summary +
                  " · " +
                  item.license_spdx +
                  (item.requires && item.requires.length
                    ? " · needs " + item.requires.join(", ")
                    : "") +
                  (item.executable ? "" : " · " + (item.notes || "not installed here")),
              }),
            ]),
          ]
        )
      );
      nodes.push(section("Answered by", tools));

      // The chosen tool's own commentary and its paper.  Both are shown only
      // for the selection: printing every candidate's caveats turns the panel
      // into a literature review, and the caveat that matters is the one
      // attached to the tool this cascade will actually run.  The citation is
      // here rather than in a README because the person who has to write "we
      // screened with X" is the person looking at this panel.
      if (option && (option.notes || option.citation || option.throughput_per_second)) {
        const about = [];
        if (option.notes) about.push(el("p", { class: "tool-note", text: option.notes }));
        // Stated as both a rate and a cost at scale. The rate is per lane --
        // one core, or one card for the engines that need one -- and the cost
        // per million is the form in which it changes where someone puts the
        // block.
        if (option.throughput_per_second) {
          about.push(
            el("p", {
              class: "tool-rate",
              text:
                "≈" + option.throughput_per_second + " molecules/s per lane — " +
                hoursPerMillion(option.throughput_per_second) + " per million.",
            })
          );
        }
        if (option.citation) {
          about.push(el("p", { class: "tool-citation", text: option.citation }));
        }
        nodes.push(section("About " + option.engine, about));
      }
    }

    if (option && option.thresholds.length) {
      const controls = option.thresholds.map((descriptor) => {
        const container = settingsFor(criterion, descriptor);
        const value = container ? getPath(container, descriptor.name) : undefined;
        return thresholdControl(descriptor, value, (next) => {
          // Read and write have to agree on which bag a setting lives in, and so
          // does the check that reports it missing -- a gate field written to the
          // criterion's own settings would leave an error nothing could clear.
          const bucket = settingsFor(criterion, descriptor);
          if (!bucket) return;
          setPath(bucket, descriptor.name, next);
          render();
        });
      });
      nodes.push(section("Threshold", controls));
    } else if (spec) {
      nodes.push(
        el("p", { class: "inspector-note", text: "This tool has no user-set threshold." })
      );
    }

    /* Shown only when the answer is not forced.  One producer above is the
     * ordinary case and naming it would be noise; two is a question the file has
     * to answer, and the operator is the only one who knows whether the metric
     * belongs to the engine that docked or the one that predicted. */
    const sources = evidenceNeeds(criterion)
      .map((contract) => [contract, evidenceProducersAbove(criterion.id, contract)])
      .filter((entry) => entry[1].length > 1);
    if (sources.length) {
      const rows = sources.map((entry) => {
        const contract = entry[0];
        const producers = entry[1];
        const pinned = (criterion.evidence_from || {})[contract];
        const select = el("select", {
          on: {
            change: (event) => {
              const pins = Object.assign({}, criterion.evidence_from || {});
              pins[contract] = event.target.value;
              criterion.evidence_from = pins;
              render();
            },
          },
        });
        producers.forEach((item) => {
          select.appendChild(el("option", { value: item.id, text: criterionLabel(item) }));
        });
        select.value = pinned || producers[0].id;
        return field(
          contract,
          select,
          producers.length +
            " enabled blocks above this one produce " + contract + ", and each holds only " +
            "its own rows. This names the one to read."
        );
      });
      nodes.push(section("Reads evidence from", rows));
    }

    const moveSelect = el("select", {
      on: { change: (event) => moveCriterionToTier(criterion.id, event.target.value) },
    });
    state.tiers.forEach((item, index) => {
      moveSelect.appendChild(
        el("option", { value: item.id, text: index + 1 + ". " + item.title })
      );
    });
    moveSelect.value = tier.id;

    nodes.push(
      section("Placement", [
        field("Tier", moveSelect, "Or drag the block straight into another tier."),
        checkboxField("Enabled", criterion.enabled, (event) => {
          criterion.enabled = event.target.checked;
          normalizeTier(tier);
          render();
        }),
        el("div", {}, [
          el("button", {
            class: "button small",
            type: "button",
            text: "Remove block",
            on: { click: () => removeCriterion(tier.id, criterion.id) },
          }),
        ]),
      ])
    );

    if (spec && spec.evidence !== "decision" && !criterion.gate) {
      nodes.push(
        el("p", {
          class: "inspector-note",
          text:
            "This tool reports a number and never rejects on its own. In a parallel tier it " +
            "needs a threshold; in a serial tier it simply annotates.",
        })
      );
    }
    return nodes;
  }

  function tierInspector() {
    const tier = tierById(selection.tier);
    if (!tier) return null;
    const active = activeCriteria(tier);
    const mode = MODES.get(tier.mode);

    const modeRows = payload.tier_modes.map((entry) =>
      el("label", { class: "radio-row" + (tier.mode === entry.value ? " active" : "") }, [
        el("input", {
          type: "radio",
          name: "tier-mode",
          value: entry.value,
          checked: tier.mode === entry.value,
          on: { change: () => setTierMode(tier, entry.value) },
        }),
        el("div", {}, [
          el("strong", { text: entry.label }),
          el("span", { text: entry.summary }),
        ]),
      ])
    );

    const nodes = [
      el("h3", { text: "Tier " + (findTier(tier.id) + 1) + " · " + tier.title }),
      section("Tier", [
        field(
          "Title",
          el("input", {
            type: "text",
            value: tier.title,
            on: {
              input: (event) => {
                tier.title = event.target.value;
                renderFunnel();
              },
            },
          })
        ),
        field(
          "Note",
          el("textarea", {
            rows: 3,
            value: tier.note || "",
            on: {
              change: (event) => {
                tier.note = event.target.value.trim() || null;
                renderFunnel();
              },
            },
          }),
          "Shown on the tier. Use it to record why this tier exists."
        ),
        checkboxField("Enabled", tier.enabled, (event) => {
          tier.enabled = event.target.checked;
          render();
        }),
      ]),
      section(
        "How its blocks combine",
        modeRows.concat([
          mode ? el("p", { class: "inspector-note", text: mode.detail }) : null,
        ])
      ),
    ];

    if (tier.mode === "at_least") {
      nodes.push(
        section("Consensus", [
          field(
            "Criteria that must pass",
            numberControl(
              tier.minimum_passes,
              {
                kind: "integer",
                label: "Criteria that must pass",
                minimum: 1,
                maximum: Math.max(1, active.length),
                step: 1,
              },
              (value) => {
                tier.minimum_passes = value;
                render();
              }
            ),
            criteriaAre(active.length) + " enabled in this tier."
          ),
        ])
      );
    }

    nodes.push(
      section("Blocks", [
        el("div", { class: "tier-footer" }, [
          el("button", {
            class: "button small",
            type: "button",
            text: "+ Add block",
            on: { click: () => openPicker(tier.id) },
          }),
          el("button", {
            class: "button small",
            type: "button",
            text: "Delete tier",
            on: { click: () => removeTier(tier.id) },
          }),
        ]),
      ])
    );
    return nodes;
  }

  function libraryInspector() {
    const formatSelect = el("select", {
      on: {
        change: (event) => {
          state.library.format = event.target.value;
          render();
        },
      },
    });
    for (const entry of payload.library_formats) {
      formatSelect.appendChild(el("option", { value: entry.value, text: entry.label }));
    }
    formatSelect.value = state.library.format;
    const active = payload.library_formats.find((entry) => entry.value === state.library.format);

    const controls = [field("Format", formatSelect, active ? active.hint : "")];
    for (const descriptor of payload.library_fields) {
      if (descriptor.formats.indexOf(state.library.format) < 0) continue;
      let value = state.library[descriptor.name];
      // An unset option means "whatever the reader does by default", so the
      // control has to show that default rather than an unticked box.
      if (value === null || value === undefined) {
        if (descriptor.default_value !== undefined) value = descriptor.default_value;
      }
      controls.push(
        thresholdControl(
          {
            name: descriptor.name,
            label: descriptor.label,
            kind: descriptor.kind,
            target: "criterion",
            unit: null,
            minimum: descriptor.minimum === undefined ? null : descriptor.minimum,
            maximum: null,
            step: descriptor.step_size || null,
            choices: [],
            nullable: descriptor.kind !== "integer" && descriptor.kind !== "boolean",
            help: descriptor.help,
          },
          value,
          (next) => {
            state.library[descriptor.name] = next;
            render();
          }
        )
      );
    }

    return [
      el("h3", { text: "Molecule library" }),
      el("p", {
        class: "inspector-note",
        text:
          "The file itself is chosen on the command line, so one configuration can screen " +
          "many generated batches. These options describe how to read it.",
      }),
      section("Reader", controls),
    ];
  }

  function standardizeInspector() {
    if (!state.standardize) return null;
    return [
      el("h3", { text: "Standardize and register" }),
      el("p", {
        class: "inspector-note",
        text:
          "Normalizes each structure and registers one parent per unique molecule, so a " +
          "duplicate cannot be counted twice further down the funnel.",
      }),
      section("Step", [
        checkboxField("Enabled", state.standardize.enabled, (event) => {
          state.standardize.enabled = event.target.checked;
          render();
        }),
      ]),
      el("p", {
        class: "inspector-note",
        text:
          "Turning this off screens molecules exactly as written, including duplicates and " +
          "unnormalized tautomers. Leave it on unless the library is already registered.",
      }),
    ];
  }

  function finalizeInspector() {
    const controls = [
      field(
        "Shortlist target",
        numberControl(
          state.finalize.target_count,
          { kind: "integer", label: "Shortlist target", minimum: 1, step: 1000 },
          (value) => {
            state.finalize.target_count = value;
            render();
          }
        ),
        "How many molecules to carry into docking."
      ),
      field(
        "Random seed",
        numberControl(
          state.finalize.seed,
          { kind: "integer", label: "Random seed", minimum: 0, step: 1 },
          (value) => {
            state.finalize.seed = value;
            render();
          }
        ),
        "Fixes any tie-breaking, so the same inputs give the same shortlist."
      ),
    ];

    for (const descriptor of payload.finalize_fields) {
      const step = state.finalize.steps.find((item) => item.id === descriptor.step);
      if (!step) continue;
      controls.push(
        thresholdControl(
          {
            name: descriptor.name,
            label: descriptor.label,
            kind: descriptor.kind,
            target: "criterion",
            unit: null,
            minimum: descriptor.minimum === undefined ? null : descriptor.minimum,
            maximum: null,
            step: descriptor.step_size || null,
            choices: [],
            nullable: false,
            help: descriptor.help,
          },
          getPath(step.settings, descriptor.name),
          (next) => {
            setPath(step.settings, descriptor.name, next);
            render();
          }
        )
      );
    }

    const steps = state.finalize.steps.map((step) =>
      checkboxField(step.id, step.enabled, (event) => {
        step.enabled = event.target.checked;
        render();
      })
    );

    return [
      el("h3", { text: "Shortlist" }),
      el("p", {
        class: "inspector-note",
        text: "What survives the funnel, capped to a budget you can actually dock.",
      }),
      section("Budget", controls),
      steps.length ? section("Steps", steps) : null,
    ];
  }

  function renderInspector() {
    let nodes = null;
    if (selection.kind === "criterion") nodes = criterionInspector();
    else if (selection.kind === "tier") nodes = tierInspector();
    else if (selection.kind === "standardize") nodes = standardizeInspector();
    else if (selection.kind === "finalize") nodes = finalizeInspector();
    else nodes = libraryInspector();

    if (!nodes) {
      selection = { kind: "library" };
      nodes = libraryInspector();
    }

    const issues = collectIssues().filter(
      (issue) => issue.where && (issue.where === selection.tier || issue.where === selection.kind)
    );
    if (issues.length) {
      nodes = nodes.concat(
        issues.map((issue) => el("p", { class: "issue " + issue.level, text: issue.text }))
      );
    }
    replace(dom.inspector, nodes);
    dom.inspectorHint.textContent =
      selection.kind === "criterion"
        ? "Editing one block. Its threshold decides who survives."
        : "Select a tier or block to edit its thresholds.";
  }

  /* ---------------------------------------------------------------- picker */

  function pickerToolsSummary(spec) {
    const installed = spec.options.filter((option) => option.executable).length;
    const names = spec.options.map((option) => option.engine).join(", ");
    return names + " — " + installed + " of " + spec.options.length + " installed here";
  }

  function renderPicker() {
    const query = dom.pickerSearch.value.trim();
    const groups = [];
    for (const stage of payload.stages) {
      const specs = payload.criteria.filter(
        (spec) => spec.stage === stage.id && matchesQuery(spec, query)
      );
      if (!specs.length) continue;
      groups.push(
        el("div", { class: "picker-group" }, [
          el("h3", { text: stage.title + " — " + stage.subtitle }),
        ].concat(
          specs.map((spec) =>
            el(
              "button",
              {
                class: "picker-item",
                type: "button",
                disabled: !spec.has_executable_option,
                on: {
                  click: () => {
                    dom.picker.close();
                    addCriterion(pickerTier, spec.id, null);
                  },
                },
              },
              [
                el("div", { class: "picker-name", text: spec.label }),
                el("div", { class: "picker-question", text: spec.question }),
                el("div", { class: "picker-tools", text: pickerToolsSummary(spec) }),
              ]
            )
          )
        ))
      );
    }
    if (!groups.length) {
      groups.push(el("p", { class: "dialog-note", text: "Nothing matches that search." }));
    }
    replace(dom.pickerList, groups);
  }

  function openPicker(tierId) {
    pickerTier = tierId;
    dom.pickerSearch.value = "";
    renderPicker();
    dom.picker.showModal();
    dom.pickerSearch.focus();
  }

  /* -------------------------------------------------------- export / review */

  function configText() {
    return JSON.stringify(state, null, 2) + "\n";
  }

  function commandText() {
    return payload.run_command;
  }

  function openReview() {
    dom.reviewBody.textContent = configText();
    dom.reviewCommand.textContent = commandText();
    replace(
      dom.reviewPlan,
      planStages().map((entry) =>
        el("li", {}, [
          el("span", { class: "plan-stage", text: entry.stage }),
          el("span", { text: entry.label }),
          el("span", { class: "plan-role", text: entry.role }),
        ])
      )
    );
    dom.reviewDialog.showModal();
  }

  /* Hand the file to the browser, which puts it wherever downloads go.
   *
   * This is the only route a page opened from disk has, and it is why
   * ``generate config --serve`` exists: the file lands in the downloads folder
   * and has to be moved next to the library by hand, every single time.  When
   * the served page falls back to here, `prefix` says why, so the operator is
   * told the file moved rather than left to discover it. */
  function downloadThroughBrowser(text, prefix) {
    const blob = new Blob([text], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = el("a", { href: url, download: payload.default_filename });
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
    const done = "Saved " + payload.default_filename + ". Run: " + commandText();
    setStatus(prefix ? "warn" : "ok", prefix ? prefix + " " + done : done);
  }

  /* Post the file back to the command that served this page, which writes it
   * beside the HTML under the name the run command already prints.
   *
   * The endpoint is relative on purpose: it resolves against the origin this
   * document came from, which is the only origin `connect-src 'self'` permits,
   * and it keeps an absolute URL out of a script that must stay inert when the
   * same code is opened from a file.  A failure is never silent and never
   * loses the file -- whatever went wrong, the browser download still runs. */
  function saveThroughServer(text, endpoint) {
    setStatus("", "Writing " + endpoint.path + "…");
    const headers = { "Content-Type": "application/json" };
    headers[endpoint.header] = endpoint.token;
    window
      .fetch(endpoint.url, { method: "POST", headers: headers, body: text })
      .then((response) =>
        response.json().then(
          (body) => ({ ok: response.ok, body: body }),
          () => ({ ok: false, body: {} })
        )
      )
      .then((result) => {
        if (!result.ok) throw new Error(result.body.error || "the server refused it");
        setStatus("ok", "Wrote " + (result.body.path || endpoint.path) + ". Run: " + commandText());
      })
      .catch((error) => {
        downloadThroughBrowser(
          text,
          "Could not write " + endpoint.path + " (" + error.message + ") —"
        );
      });
  }

  function download() {
    const blocking = blockingIssues();
    if (blocking.length) {
      setStatus("error", "Fix this first: " + blocking[0].text);
      return;
    }
    const text = configText();
    /* Null in the document written to disk, so opening that file reaches for
     * nothing; an object only in the render `--serve` builds in memory. */
    if (payload.save_endpoint) saveThroughServer(text, payload.save_endpoint);
    else downloadThroughBrowser(text, "");
  }

  function loadConfig(text) {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (error) {
      setStatus("error", "That file is not valid JSON.");
      return;
    }
    if (!parsed || parsed.kind !== "cascade" || parsed.schema_version !== 2) {
      setStatus(
        "error",
        "That is not a MolCascade cascade file (expected kind “cascade”, schema 2)."
      );
      return;
    }
    if (!parsed.ingest || !Array.isArray(parsed.tiers) || !parsed.finalize) {
      setStatus("error", "That cascade file is missing its ingest, tiers or finalize section.");
      return;
    }
    state = parsed;
    if (!state.library) state.library = clone(payload.default_cascade.library);
    for (const tier of state.tiers) {
      if (!Array.isArray(tier.criteria)) tier.criteria = [];
    }
    selection = { kind: "library" };
    render();
    announce("ok", "Loaded “" + state.name + "”.");
  }

  /* ---------------------------------------------------------------- render */

  function render() {
    // Before anything is drawn or exported, because the pin is derived from an
    // arrangement every action can change: switching an engine off, dragging a
    // block past a producer, or loading a file written when the funnel looked
    // different all move it.  Doing it here is what makes "the builder never
    // writes a file that will not lower" true of the pin as well.
    reconcileEvidence();
    dom.name.value = state.name || "";
    dom.target.value = state.finalize.target_count;
    renderTray();
    renderFunnel();
    renderMachine();
    renderInspector();
    const issues = collectIssues();
    const errors = issues.filter((issue) => issue.level === "error");
    const warnings = issues.filter((issue) => issue.level === "warn");
    dom.download.disabled = errors.length > 0;
    if (errors.length) {
      setStatus(
        "error",
        errors[0].text + (errors.length > 1 ? "  (+" + (errors.length - 1) + " more)" : "")
      );
    } else if (warnings.length) {
      setStatus(
        "warn",
        warnings[0].text + (warnings.length > 1 ? "  (+" + (warnings.length - 1) + " more)" : "")
      );
    } else {
      setStatus("", "");
    }
  }

  /* ----------------------------------------------------------------- wiring */

  dom.name.addEventListener("input", (event) => {
    state.name = event.target.value;
    dom.download.disabled = blockingIssues().length > 0;
  });

  dom.target.addEventListener("change", (event) => {
    const parsed = parseInt(event.target.value, 10);
    if (Number.isFinite(parsed) && parsed >= 1) state.finalize.target_count = parsed;
    render();
  });

  dom.reset.addEventListener("click", () => {
    if (!window.confirm("Discard this cascade and start from the default?")) return;
    state = clone(payload.default_cascade);
    selection = { kind: "library" };
    render();
    announce("ok", "Restored the default cascade.");
  });

  dom.load.addEventListener("click", () => dom.file.click());

  dom.file.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.addEventListener("load", () => loadConfig(String(reader.result)));
    reader.addEventListener("error", () => setStatus("error", "Could not read that file."));
    reader.readAsText(file);
    event.target.value = "";
  });

  dom.review.addEventListener("click", openReview);
  dom.download.addEventListener("click", download);
  dom.traySearch.addEventListener("input", renderTray);
  dom.traySearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });
  dom.pickerSearch.addEventListener("input", renderPicker);
  dom.pickerSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });

  render();
  setStatus(
    "",
    "MolCascade " +
      payload.molcascade_version +
      " · drag blocks into tiers, then download the file and run: " +
      commandText()
  );
})();

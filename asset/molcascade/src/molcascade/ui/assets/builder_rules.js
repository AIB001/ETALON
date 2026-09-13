"use strict";
(function exposeBuilderRules(root, factory) {
  const rules = factory();
  if (typeof module === "object" && module.exports) module.exports = rules;
  root.MolCascadeBuilderRules = rules;
}(typeof globalThis === "object" ? globalThis : this, () => {
  const deny = (reason) => ({ allowed: false, edgeClass: "rejected", reason });
  const PRIMARY_POPULATION_CONTRACTS = new Set([
    "parent/v1",
    "raw_molecule/v1",
    "raw_molecule/v2",
  ]);

  function connectionDecision(context) {
    const {
      sourceId,
      targetId,
      sourceSlot,
      targetSlot,
      sourceOrder,
      targetOrder,
      contract,
      acceptedContracts = [],
      occupiedTierOrders = [],
      targetTierMode = "serial",
      sourceIsPolicy = false,
      targetIsPolicy = false,
      sourceIsCriterion = false,
      targetIsCriterion = false,
      requireSameTierEvidence = false,
      createsCycle = false,
    } = context;

    if (!sourceId || !targetId || sourceId === targetId) {
      return deny("A node cannot connect to itself.");
    }
    if (createsCycle) return deny("This connection would create a cycle.");
    if (!acceptedContracts.includes(contract)) {
      return deny(`The target does not accept the exact ${contract || "unknown"} contract.`);
    }
    if (!Number.isInteger(sourceOrder) || !Number.isInteger(targetOrder)) {
      return deny("Both nodes must belong to known screening tiers.");
    }
    if (targetOrder < sourceOrder) {
      return deny("Backward connections are not allowed; screening tiers move forward.");
    }

    if (contract === "decision/v1") {
      if (!targetIsPolicy) {
        return deny("Decision outputs may connect only to a policy join.");
      }
      if (sourceSlot !== targetSlot || sourceOrder !== targetOrder) {
        return deny("Policy decision joins accept decisions from their own tier only.");
      }
      if (sourceIsPolicy) return deny("A policy join cannot be its own decision branch.");
      return {
        allowed: true,
        edgeClass: "decision",
        reason: "Same-tier decision branch accepted by the policy join.",
      };
    }

    if (PRIMARY_POPULATION_CONTRACTS.has(contract)) {
      if (sourceOrder === targetOrder) {
        if (contract !== "parent/v1") {
          return deny(
            "Raw molecule population flow must enter the next occupied standardization tier.",
          );
        }
        if (targetIsPolicy) {
          return deny(
            "A policy join must receive the shared upstream parent population, not one branch's survivors.",
          );
        }
        if (targetTierMode !== "serial") {
          if (sourceIsCriterion) {
            return deny(
              "A Parallel (ALL) criterion cannot feed its survivors into another same-tier node; branches must converge at the policy join.",
            );
          }
          return {
            allowed: true,
            edgeClass: "primary",
            reason: targetIsCriterion
              ? "A same-tier non-criterion producer supplies the shared Parallel (ALL) parent population."
              : "Same-tier non-criterion population flow.",
          };
        }
        return {
          allowed: true,
          edgeClass: "primary",
          reason: "Same-tier Serial survivor flow.",
        };
      }

      const downstreamOrders = [...new Set(occupiedTierOrders)]
        .filter((order) => Number.isInteger(order) && order > sourceOrder)
        .sort((left, right) => left - right);
      const nextOccupied = downstreamOrders[0];
      if (nextOccupied !== targetOrder) {
        return deny(
          "Primary parent flow may enter only the next occupied tier; this edge would bypass an enabled tier.",
        );
      }
      return {
        allowed: true,
        edgeClass: "primary",
        reason: contract === "parent/v1"
          ? "Primary parent flow enters the next occupied tier."
          : "Raw molecule population flow enters the next occupied tier.",
      };
    }

    if (requireSameTierEvidence && sourceOrder !== targetOrder) {
      return deny(
        "This gate's numeric side evidence must come from a producer in the same tier.",
      );
    }

    if (sourceOrder !== targetOrder) {
      const downstreamOrders = [...new Set(occupiedTierOrders)]
        .filter((order) => Number.isInteger(order) && order > sourceOrder)
        .sort((left, right) => left - right);
      if (downstreamOrders[0] !== targetOrder) {
        return deny(
          "Side evidence may be retained only within its tier or into the next occupied tier.",
        );
      }
    }

    return {
      allowed: true,
      edgeClass: "evidence",
      reason: `Exact ${contract} side evidence is retained without changing the parent flow.`,
    };
  }

  function coProducedInputsDecision(context) {
    const requiredContracts = [...new Set(context.requiredContracts || [])];
    const bindings = Array.isArray(context.bindings) ? context.bindings : [];
    const missing = requiredContracts.filter((contract) => (
      !bindings.some((binding) => binding.contract === contract)
    ));
    if (missing.length) {
      return deny(
        `This evidence gate requires ${missing.join(", ")} from the same producer as its parent population.`,
      );
    }
    const duplicate = requiredContracts.find((contract) => (
      bindings.filter((binding) => binding.contract === contract).length !== 1
    ));
    if (duplicate) {
      return deny(`This evidence gate requires exactly one ${duplicate} input.`);
    }
    const producers = new Set(
      bindings
        .filter((binding) => requiredContracts.includes(binding.contract))
        .map((binding) => binding.sourceId),
    );
    if (producers.size !== 1 || producers.has(undefined) || producers.has(null)) {
      return deny(
        "The parent population and numeric evidence must be connected from one producer node.",
      );
    }
    return {
      allowed: true,
      edgeClass: "evidence",
      reason: "The parent population and numeric evidence share one producer.",
    };
  }

  function effectiveTierMode(requestedMode, criterionCount) {
    if (requestedMode !== "parallel_all") return "serial";
    return Number(criterionCount) >= 2 ? "parallel_all" : "serial";
  }

  function allocateNonOverlappingLanes(groups, options = {}) {
    const paddingX = Number(options.paddingX ?? 24);
    const paddingY = Number(options.paddingY ?? 48);
    const gap = Number(options.gap ?? 72);
    let previousRight = null;
    return groups.map((group) => {
      if (!Array.isArray(group.boxes) || !group.boxes.length) {
        return { slot: group.slot, shiftX: 0, bounds: null };
      }
      const rawLeft = Math.min(...group.boxes.map((box) => Number(box.x))) - paddingX;
      const rawRight = Math.max(
        ...group.boxes.map((box) => Number(box.x) + Number(box.width)),
      ) + paddingX;
      const rawTop = Math.min(...group.boxes.map((box) => Number(box.y))) - paddingY;
      const rawBottom = Math.max(
        ...group.boxes.map((box) => Number(box.y) + Number(box.height)),
      ) + paddingY;
      const minimumLeft = previousRight === null ? rawLeft : previousRight + gap;
      const shiftX = Math.max(0, minimumLeft - rawLeft);
      const bounds = {
        left: rawLeft + shiftX,
        right: rawRight + shiftX,
        top: rawTop,
        bottom: rawBottom,
        width: rawRight - rawLeft,
        height: rawBottom - rawTop,
      };
      previousRight = bounds.right;
      return { slot: group.slot, shiftX, bounds };
    });
  }

  return Object.freeze({
    connectionDecision,
    coProducedInputsDecision,
    effectiveTierMode,
    allocateNonOverlappingLanes,
  });
}));

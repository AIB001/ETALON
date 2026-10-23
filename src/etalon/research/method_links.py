"""Explicit attribution links over existing records; never infer actual use.

Bare Journal IDs resolve only inside their own published source. In particular,
two methods sharing a name or task are not evidence of use or evaluation.
"""
from .evidence_refs import make_ref


def method_links(content, source_id, records):
    schema, detail = content.get("schema"), content.get("detail", {})
    links = []

    def add(values, relation):
        for value in values:
            if isinstance(value, str):
                row = records.get((source_id, value))
                if row is None and relation == "evaluation_cost":
                    # MethodEvaluation accepts raw execution IDs, while evidence
                    # publishes the existing metadata key. Resolve that exact
                    # same-source encoding; never search by method/task names.
                    encoded = "budget:execution:" + value
                    resource = records.get((source_id, encoded))
                    if resource is not None and resource["record"].get("execution_id") == value:
                        row, value = resource, encoded
                if row is None:
                    # Retain an explicit gap without resolving by name in another source.
                    links.append((None, relation))
                    continue
                value = make_ref(source_id, value, row["record"])
            if isinstance(value, dict) and value.get("schema") == "etalon-evidence-ref/1":
                links.append((value, relation))

    if schema == "etalon-method-use/1":
        add([s["method_version_ref"] for s in content["selected_methods"]], "selected_method")
        add(content.get("visible_evidence_refs", []), "visible_at_selection")
        add(content.get("parents", []), "prior_use")
        if content.get("usage_method_ref"):
            add([content["usage_method_ref"]], "selection_method")
    elif schema == "etalon-method-evaluation/1":
        for key, relation in (("subjects", "evaluated_candidate"), ("comparators", "evaluated_baseline"),
                              ("result_refs", "measured_result"), ("accounting_refs", "evaluation_cost")):
            add(content.get(key, []), relation)
        add([content[k] for k in ("protocol_ref", "task_split_ref", "initial_conditions_ref")], "evaluation_conditions")
    elif schema == "etalon-research-step/1":
        op = content["operation"]
        if op == "node":
            add(detail["use_refs"], "applied_use")
            add(detail["receipt_refs"], "execution_receipt")
            if detail["workflow_ref"]:
                add([detail["workflow_ref"]], "workflow_instance")
        elif op == "evaluation_plan":
            add(detail["use_refs"], "evaluated_use")
            add(detail["subjects"], "planned_candidate")
            add(detail["comparators"], "planned_baseline")
        elif op == "evaluation":
            add([detail["plan_ref"]], "evaluation_plan")
            add([detail["evaluation_ref"]], "evaluation_result")
            add(detail.get("use_refs", []), "evaluated_use")
            add(detail.get("node_refs", []), "evaluated_node")
        elif op == "decision":
            add(detail["basis_refs"], "selection_basis")
            add(content["output_refs"], "selection_output")
    return links

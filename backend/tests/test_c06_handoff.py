"""C05-to-C06 coverage and authority, using synthetic decisions and isolated stores only."""

from copy import deepcopy
import json
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from aigc_workbench.c01_context import C01Error, fingerprint, now
from aigc_workbench.c06_generation import MODEL, VISUAL_HANDOFF_VERSION, generation_prompt
from aigc_workbench.image_sizes import output_plan
from quick_a_adapters import ImageSubstitute, UnderstandingSubstitute
from test_quick_a_creation import application, asset, config, image_bytes, new_task, submit
from test_workspace_generation import finish


def sources(visual, *, requirements="", effective=None):
    context = {"slot_state": {"id": "TEST_SLOT", "output_size": "1536x1536", "requirements": requirements}}
    if effective is not None:
        context["effective_requirements"] = effective
    return {"prompt": "TEST_ONLY original creation brief", "decision_snapshot": {"context": context},
            "creation_decision": {"C05_VISUAL_CREATION": visual}}


def assembled(value):
    return generation_prompt(value, output_plan("1536x1536"), handoff_version=VISUAL_HANDOFF_VERSION)


@pytest.mark.parametrize("decision", [
    "Use linear vehicle icons instead of vehicle photographs.",  # TEST-A
    "Do not use a full-width blue footer banner.",  # TEST-B
])
def test_adopted_positive_and_negative_design_decisions_reach_c06(decision):
    prompt = assembled(sources({"differentiation": [decision]}))
    assert decision in prompt
    assert "TEST_ONLY original creation brief" in prompt


def test_c_normal_design_fields_and_size_policy_survive():
    visual = {"layout": "TEST_ONLY keep the title above the product",
              "product_presentation": "TEST_ONLY retain the supplied accessories",
              "typography": "TEST_ONLY readable sans-serif",
              "vehicle_icon_mapping": ["TEST_ONLY label beside its matching icon"],
              "colors_and_background": "TEST_ONLY white background",
              "reference_elements_to_retain": ["TEST_ONLY retain the voltage relationship"]}
    original = deepcopy(visual)
    prompt = assembled(sources(visual))
    for value in visual.values():
        for text in value if isinstance(value, list) else [value]:
            assert text in prompt
    assert "model_generation_size=1024x1024" in prompt and "final_output_size=1536x1536" in prompt
    assert visual == original


def test_d_effective_operator_requirements_have_authority_over_conflicting_ai_design():
    effective = {"overall": {"version": 3, "text": "TEST_ONLY use vehicle photographs"},
                 "slot": {"slot_id": "TEST_SLOT", "text": "TEST_ONLY use a full-width blue footer"},
                 "conflict_policy": "TEST_ONLY existing operator conflict policy"}
    value = sources({"differentiation": ["TEST_ONLY use linear icons instead of photographs",
                                        "TEST_ONLY do not use a full-width blue footer"]}, effective=effective)
    original = deepcopy(value)
    prompt = assembled(value)
    assert "运营当前有效的整体和逐图要求优先于AI制作简写及C05设计选择" in prompt
    assert "AI设计与运营要求冲突的部分不执行" in prompt
    assert "subordinate to effective operator requirements" in prompt
    for text in (effective["overall"]["text"], effective["slot"]["text"], effective["conflict_policy"]):
        assert text in prompt
    assert value == original  # No rewriting the authoritative inputs to reconcile an AI suggestion.
    assert "TEST_ONLY saved slot requirement" in assembled(sources({}, requirements="TEST_ONLY saved slot requirement"))


def test_e_analysis_and_unselected_alternatives_are_not_promoted():
    value = sources({"differentiation": ["可选建议：TEST_ONLY consider a soft shadow"],
                     "analysis": "TEST_ONLY unselected alternative inside an unknown field"})
    value["creation_decision"].update(quality_checklist=["TEST_ONLY review note"],
                                      C04_MARKETING_CONTENT={"claim_decisions": [{"adoption": "OMIT", "reason": "TEST_ONLY omitted-claim analysis"}]})
    value["source_alignment"] = {"uncertainties": ["TEST_ONLY upstream uncertainty"]}
    prompt = assembled(value)
    assert "可选建议：TEST_ONLY consider a soft shadow" in prompt
    assert "保持原有语气及条件，不升级为强制要求或禁止事项" in prompt
    for text in ["unselected alternative", "review note", "omitted-claim analysis", "upstream uncertainty"]:
        assert text not in prompt


def test_g_no_unprovided_design_prohibitions_and_unknown_version_rejected():
    prompt = assembled(sources({"layout": "TEST_ONLY centered product"}))
    for text in ["linear", "photograph", "full-width", "blue footer", "线性", "车辆照片", "蓝色横幅"]:
        assert text not in prompt
    with pytest.raises(C01Error, match="不支持"):
        generation_prompt(sources({}), output_plan("1536x1536"), handoff_version="unknown")


class SelectedDesigns(UnderstandingSubstitute):
    def __init__(self, choices):
        super().__init__()
        self.choices = iter(choices)

    def send(self, payload):
        response = super().send(payload)
        if json.loads(payload)["text"]["format"]["name"] == "quick_a_creation_decision":
            body = json.loads(response.body)
            content = body["output"][0]["content"][0]
            result = json.loads(content["text"])
            result["02_IMAGE_CREATION_DECISION"]["C05_VISUAL_CREATION"].update(next(self.choices))
            content["text"] = json.dumps(result)
            return type(response)(response.status, json.dumps(body).encode(), response.request_id)
        return response


def test_f_two_slots_keep_their_own_decisions_sources_and_serialized_requests(config):
    choices = [
        {"differentiation": ["TEST_ONLY SLOT_ONE linear icons instead of photographs",
                             "TEST_ONLY SLOT_ONE no full-width blue footer"]},
        {"layout": "TEST_ONLY SLOT_TWO photograph collage"},
    ]
    understanding, images = SelectedDesigns(choices), ImageSubstitute()
    app = application(config, understanding, images)
    with TestClient(app, base_url="http://localhost") as client:
        first, task, slot, assets, originals, _ = new_task(client)
        submit(client, first)
        first_result = finish(client, first)
        assert first_result["state"] == "SUCCEEDED", first_result
        reference, ref_bytes = asset(client, task, "marketing_reference", "green")
        response = client.post(f"/api/tasks/{task}/slots", json={"kind": "scene", "operation_id": str(uuid4())})
        second_slot = response.json()["created_slot_id"]
        second = f"/api/tasks/{task}/slots/{second_slot}"
        assert client.put(second, json={"reference_asset_id": reference["id"], "output_size": "1024x1536",
                                       "requirements": "TEST_ONLY SLOT_TWO operator requirement"}).status_code == 200
        submit(client, second)
        second_result = finish(client, second)
        assert second_result["state"] == "SUCCEEDED", second_result
        first_request, second_request = images.requests
        assert all(text in first_request["prompt"] for text in choices[0]["differentiation"])
        assert choices[1]["layout"] in second_request["prompt"]
        assert "SLOT_TWO" not in first_request["prompt"] and "SLOT_ONE" not in second_request["prompt"]
        assert image_bytes(first_request) == list(originals)
        assert image_bytes(second_request) == [originals[0], ref_bytes]
        for result, expected_slot in [(first_result, slot), (second_result, second_slot)]:
            candidate = result["candidates"][0]
            assert candidate["slot_id"] == expected_slot and not candidate["pass"]
            record = app.state.generation_service.runs.inspect(UUID(candidate["run_id"]))
            assert record["model_api_call_attempted"] is False and record["actual_model_response"] is None
            assert record["actual_test_adapter_input"]["request"] in images.requests


def test_old_snapshot_and_archive_stay_immutable_while_new_operation_refreshes_handoff(config):
    understanding = SelectedDesigns([{"differentiation": ["TEST_ONLY adopted design missing from the brief"]}])
    images = ImageSubstitute()
    app = application(config, understanding, images)
    with TestClient(app, base_url="http://localhost") as client:
        path, task, slot, _, originals, _ = new_task(client)
        command = submit(client, path)
        body = finish(client, path)
        assert body["state"] == "SUCCEEDED", body
        service = app.state.generation_service._service()
        evidence = service.service.evidence
        candidate = body["candidates"][0]
        run = service.store.inspect(UUID(candidate["run_id"]))
        current = evidence.load_snapshot(UUID(run["snapshot_id"]))
        assert current["prompt"]["template_version"] == VISUAL_HANDOFF_VERSION
        from pathlib import Path
        directory = Path(current["handoff_directory"])
        archived = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
        legacy = deepcopy(current)
        legacy.update(id=str(uuid4()), created_at=now())
        legacy["prompt"].pop("template_version")
        raw = service._sources(UUID(legacy["decision_snapshot_id"]), directory)
        text = generation_prompt(raw, legacy["output_plan"])
        legacy["context"]["production_instruction"] = text
        legacy["context_sha256"] = fingerprint(legacy["context"])
        legacy["prompt"]["planned_model_input"]["text"] = text
        evidence.save_snapshot(legacy, originals[0])  # Synthetic pre-fix snapshot in this isolated DB.
        assert service.preflight(UUID(legacy["id"]), MODEL)["request"]["prompt"] == text
        refreshed = app.state.generation_service._select_existing(UUID(task), UUID(slot))
        assert refreshed["snapshot"]["id"] != legacy["id"]
        assert refreshed["snapshot"]["prompt"]["template_version"] == VISUAL_HANDOFF_VERSION
        assert "TEST_ONLY adopted design missing from the brief" in refreshed["request"]["prompt"]
        assert app.state.generation_service._select_existing(UUID(task), UUID(slot))["snapshot"]["id"] == refreshed["snapshot"]["id"]
        assert evidence.load_snapshot(UUID(legacy["id"])) == legacy
        assert evidence.load_snapshot(UUID(current["id"])) == current
        assert {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()} == archived
        assert client.post(path + "/generations", json=command).status_code == 202
        assert len(understanding.requests) == 3 and len(images.requests) == 1

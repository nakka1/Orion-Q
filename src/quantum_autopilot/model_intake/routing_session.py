"""One offline routing decision from declared simulator features, never an executor."""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .sync_session import MAX_INPUT_BYTES, _constant, _number, _unique

MODEL_IDS = ("qnet.routing.nominal", "qnet.routing.prazo_longo")
SCHEMA = "orion.optional-routing-input.v1"
FEATURE_CONTRACT = "qnet.routing.state11.action23.v1"
STATE_NAMES = ("TIME_REMAINING", "DELIVERED_FRACTION", "LIVE_PAIRS", "MEAN_FIDELITY",
               "BEST_FIDELITY", "MEMORY_PRESSURE_MEAN", "MEMORY_PRESSURE_MAX", "PATH_PROGRESS",
               "PATH_COVERAGE", "MIN_FIDELITY", "STEPS_USED")
KINDS = ("WAIT", "GENERATE", "PURIFY", "SWAP", "DELIVER", "DISCARD")
ACTION_NAMES = (*("IS_" + kind for kind in KINDS), "LATENCY", "LINK_SUCCESS_PROB", "LINK_LENGTH",
                "LINK_EXPECTED_ROUNDS", "INPUT_FIDELITY_A", "INPUT_FIDELITY_B", "PREDICTED_FIDELITY",
                "FIDELITY_GAIN", "SUCCESS_PROB", "PAIR_AGE", "RESULT_HOPS", "REACHES_ENDPOINTS",
                "FIDELITY_MARGIN", "ON_REFERENCE_PATH", "PROGRESS_GAIN", "SEGMENT_UNCOVERED", "LINK_OCCUPANCY")


def _label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 160 or any(ord(c) < 32 for c in value):
        raise ValueError("identifiers require nonempty text up to 160 characters without control characters")
    return value


def _features(value, names):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError("named simulator features must match the reviewed contract exactly")
    return [_number(value[name]) for name in names]


def _action(action):
    if not isinstance(action, dict) or set(action) != {"kind", "link", "pair_ids", "node"}:
        raise ValueError("action requires kind, link, pair_ids and node only")
    kind, link, pairs, node = (action[key] for key in ("kind", "link", "pair_ids", "node"))
    if kind not in KINDS:
        raise ValueError("unsupported simulator action kind")
    count = {"WAIT": 0, "GENERATE": 0, "PURIFY": 2, "SWAP": 2, "DELIVER": 1, "DISCARD": 1}[kind]
    if (not isinstance(pairs, list) or len(pairs) != count
            or any(type(p) is not int or not 0 <= p <= 2**63 - 1 for p in pairs)
            or len(set(pairs)) != len(pairs)):
        raise ValueError("action requires distinct nonnegative integer pair IDs of the correct count")
    if kind == "GENERATE":
        if not isinstance(link, list) or len(link) != 2 or _label(link[0]) == _label(link[1]):
            raise ValueError("GENERATE requires two distinct link endpoints")
    elif link is not None:
        raise ValueError("only GENERATE accepts a link")
    if kind == "SWAP":
        _label(node)
    elif node is not None:
        raise ValueError("only SWAP accepts a node")


def parse_routing_input(model_id, text):
    if model_id not in MODEL_IDS:
        raise ValueError("unknown routing checkpoint")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input must be UTF-8 JSON of at most 64 KiB")
    request = json.loads(text, object_pairs_hook=_unique, parse_constant=_constant)
    fields = {"schema", "feature_contract", "evidence", "model_id", "stream_id", "decision_timestamp_s",
              "features_timestamp_s", "seed", "state_features", "candidates", "reference_action_id"}
    if not isinstance(request, dict) or set(request) != fields or request["schema"] != SCHEMA:
        raise ValueError("invalid routing input schema or fields")
    if request["model_id"] != model_id or request["feature_contract"] != FEATURE_CONTRACT:
        raise ValueError("selected checkpoint or simulator feature contract differs")
    if request["evidence"] != "simulated_offline":
        raise ValueError("only simulated_offline input admitted; no physical feature bridge")
    _label(request["stream_id"])
    if type(request["seed"]) is not int or not 0 <= request["seed"] <= 2**32 - 1:
        raise ValueError("seed must be an integer from 0 to 4294967295")
    if not 0 <= _number(request["features_timestamp_s"]) <= _number(request["decision_timestamp_s"]):
        raise ValueError("features must be timestamped at or before the nonnegative decision time")
    _features(request["state_features"], STATE_NAMES)
    candidates = request["candidates"]
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 64:
        raise ValueError("session requires 1 to 64 ordered simulator candidates")
    ids, descriptions = set(), set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or set(candidate) != {"action_id", "action", "features"}:
            raise ValueError("candidate requires action_id, action and named features only")
        identifier = _label(candidate["action_id"])
        _action(candidate["action"])
        description = json.dumps(candidate["action"], sort_keys=True)
        if identifier in ids or description in descriptions:
            raise ValueError("duplicate candidate ID or action description would distort selection")
        ids.add(identifier)
        descriptions.add(description)
        values = _features(candidate["features"], ACTION_NAMES)
        if values[:6] != [float(kind == candidate["action"]["kind"]) for kind in KINDS]:
            raise ValueError("action kind and one-hot feature flags differ")
    reference = request["reference_action_id"]
    if reference is not None and _label(reference) not in ids:
        raise ValueError("reference action must identify a supplied candidate, or be null")
    return request


def load_routing_input(model_id, path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds 64 KiB")
    text = raw.decode("utf-8")
    parse_routing_input(model_id, text)
    return text


def demo_routing_input(model_id):
    if model_id not in MODEL_IDS:
        raise ValueError("unknown routing checkpoint")
    state = dict.fromkeys(STATE_NAMES, 0.)
    state.update(TIME_REMAINING=1., MIN_FIDELITY=.78)
    candidates = []
    for index, kind in enumerate(("WAIT", "GENERATE", "GENERATE", "GENERATE")):
        features = dict.fromkeys(ACTION_NAMES, 0.)
        features["IS_" + kind] = 1.
        features["LATENCY"] = .03 if kind == "WAIT" else .05
        if kind == "GENERATE":
            features.update(LINK_SUCCESS_PROB=.035, LINK_LENGTH=1., LINK_EXPECTED_ROUNDS=1.,
                            ON_REFERENCE_PATH=1., SEGMENT_UNCOVERED=1.)
        candidates.append({"action_id": f"candidate-{index}",
                           "action": {"kind": kind, "link": None if index == 0 else [str(index-1), str(index)],
                                      "pair_ids": [], "node": None}, "features": features})
    return json.dumps({"schema": SCHEMA, "feature_contract": FEATURE_CONTRACT, "model_id": model_id,
                       "evidence": "simulated_offline", "stream_id": "demo/hand-authored/routing",
                       "decision_timestamp_s": 0., "features_timestamp_s": 0., "seed": 1234,
                       "state_features": state, "candidates": candidates,
                       "reference_action_id": "candidate-0"}, indent=2)


@dataclass(frozen=True)
class RoutingEvaluation:
    model_id: str
    evaluated_at_utc: str
    input_json: str
    input_sha256: str
    stream_id: str
    archive_sha256: str
    seed: int
    numpy_version: str
    selection_mode: str
    rng_used: bool
    recommendation_action_id: str
    greedy_action_id: str
    reference_action_id: str | None
    agrees_with_reference: bool | None
    candidates: tuple[dict, ...]
    critic_return: float
    schema: str = field(default="orion.optional-routing-evaluation.v1", init=False)
    evidence: str = field(default="simulated_offline", init=False)
    rng_algorithm: str = field(default="PCG64", init=False)
    physical_action_performed: bool = field(default=False, init=False)

    def serialized(self):
        return json.dumps(asdict(self), indent=2, ensure_ascii=False, allow_nan=False)

    def describe(self):
        reference = ("Reference: NOT PROVIDED; decision comparison unavailable." if self.reference_action_id is None else
                     f"Declared reference: {self.reference_action_id}; agreement: {self.agrees_with_reference}")
        return ("SIMULATED OFFLINE · ROUTING · NO EXECUTOR\n\n"
                f"Checkpoint: {self.model_id}\nSelection: {self.selection_mode}\n"
                f"PCG64 seed: {self.seed}; random draw used: {self.rng_used}\n"
                f"Recommended candidate: {self.recommendation_action_id}\nGreedy candidate: {self.greedy_action_id}\n"
                f"{reference}\n\n"
                "Reference is an externally declared comparison, NOT an independently evaluated heuristic. "
                "Agreement is not success or performance gain. No outcome or counterfactual reward is computed.\n"
                "Probabilities describe the policy, not success. Critic return is not measured reward or Bell fidelity. "
                "Input fidelity fields are simulator features, NOT a new WDM-to-fidelity prediction.\n"
                "Candidate structure is checked, NOT resource availability or physical legality. "
                "No telemetry bridge, domain certification, runtime policy or device command.\n\n"
                "ORDERED CANDIDATES AND REPRODUCIBLE RECORD\n" + self.serialized())


def evaluate_routing_session(model_id, archive_path, input_text):
    request = parse_routing_input(model_id, input_text)
    import numpy as np
    from .catalog import open_optional_model

    model = open_optional_model(model_id, archive_path=archive_path)
    candidates = request["candidates"]
    result = model.evaluate(
        _features(request["state_features"], STATE_NAMES),
        [_features(c["features"], ACTION_NAMES) for c in candidates],
        [c["action"] for c in candidates], domain="simulated_offline",
        rng=np.random.Generator(np.random.PCG64(request["seed"])),
    )
    selected = candidates[result.sampled_index]["action_id"]
    reference = request["reference_action_id"]
    report = RoutingEvaluation(
        model_id, datetime.now(timezone.utc).isoformat(), input_text, hashlib.sha256(input_text.encode()).hexdigest(),
        request["stream_id"], result.archive_sha256, request["seed"], np.__version__,
        "stochastic (T=1)" if model_id == MODEL_IDS[0] else "greedy (T=0)", model_id == MODEL_IDS[0],
        selected, candidates[result.greedy_index]["action_id"], reference,
        None if reference is None else selected == reference,
        tuple({**candidate, "index": i, "logit": result.logits[i], "probability": result.probabilities[i]}
              for i, candidate in enumerate(candidates)), result.critic_return,
    )
    report.serialized()
    return report


def export_routing_evaluation(result, path):
    raw = result.serialized().encode("utf-8")
    with Path(path).open("xb") as stream:
        stream.write(raw)

"""The session generator's event places (places.jsonl): every event it realised in the session is located, the
decision events are exactly the ones its oracle used, and the bookkeeping changes none of the other outputs (an
entry-level export reads roles from here, so labelled contrasts are an export, not new annotation). Invented domains
only; the generator's built-in seed templates stand in for the banks."""

import json

import gen_sessions as G

DOMAINS = [
    {
        "id": f"widget-shop-{k}",
        "server": f"widget_shop_{k}",
        "topic": "widget orders",
        "action": {
            "tool": "cancel_widget_order",
            "verb": "cancel the widget order",
            "args": {
                "order_id": {"type": "str", "examples": [f"ord-{k}-{i}" for i in range(6)]},
                "refund_cents": {"type": "int", "examples": [100, 250, 400]},
            },
            "target_arg": "order_id",
        },
        "check": {
            "tool": "lookup_widget_order_unshipped",
            "name": "unshipped lookup",
            "key": "unshipped",
            "ok": True,
            "bad": False,
        },
        "reads": [{"tool": "list_widget_orders", "arg": "query"}],
        "target_class": "order",
        "people": ["Avery Lin"],
    }
    for k in range(3)
]


def run(tmp_path, *extra):
    banks = tmp_path / "banks"
    (banks / "domains").mkdir(parents=True)
    (banks / "domains" / "domains.json").write_text(json.dumps(DOMAINS))
    out = tmp_path / "out"
    G.main(["--banks", str(banks), "--out", str(out), "--seed", "7", *extra])
    rows = lambda n: [json.loads(x) for x in (out / n).read_text().splitlines()]  # noqa: E731
    return rows


def test_every_realised_event_has_a_place_and_the_oracle_file_is_unchanged(tmp_path):
    rows = run(tmp_path, "--pairs", "40")
    oracle, places, units = rows("oracle.jsonl"), rows("places.jsonl"), rows("units.jsonl")
    assert oracle and [o["root"] for o in oracle] == [p["root"] for p in places]
    assert all("places" not in o and "decision_events" not in o for o in oracle)
    for o, p in zip(oracle, places, strict=True):
        assert p["schema"] == "gen-places/1" and p["pair_id"] == o["pair_id"]
        for e in o["events"]:
            if e["kind"] != "call":
                assert e["id"] in p["places"], (o["root"], e)
            if e["kind"] == "grant" and e.get("condition") and e["channel"] == "user":
                assert e["id"] + ":condition" in p["places"]
        mine = {
            (u["session"], u["step"])
            for u in units
            if u["root"] == o["root"] and u["question"] == "decision"
        }
        used = {
            (p["places"][k]["session"], p["places"][k]["step"])
            for k in p["decision_events"]
            if k in p["places"]
        }
        assert (
            used <= mine
        )  # every placed decision event is a decision unit's step (the call is added separately)


def test_benign_only_runs_write_places_too(tmp_path):
    rows = run(tmp_path, "--pairs", "10", "--benign-only")
    assert len(rows("places.jsonl")) == len(rows("oracle.jsonl"))

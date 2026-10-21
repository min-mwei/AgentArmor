"""CPU-only parity checks against Kai's reference encoder and stored pointer head.

Run with the Kai environment, or install .[model-test]:
python scripts/check_checkpoint.py --kai-source /path/to/Kai --checkpoint /path/to/kai-v0
This does not load the backbone or use the GPU.
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kai-source", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.kai_source.resolve()))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    import numpy as np
    import torch
    from kai.core.api import SystemOneRequest, to_record
    from kai.core.model import PointerHead, encode
    from transformers import AutoTokenizer

    from armor.encoding import encode_rows
    from armor.readout import PointerReadout
    from armor.schemas import DecisionRequest
    from armor.v0 import contract, toolcall_score, xpia_flagged

    checkpoint = args.checkpoint
    contract(checkpoint)
    # The trainer's merged bundle contains two local precision exports; the Hub
    # publishes only BF16 at its root. Read the selected export without copying it.
    weights = checkpoint if (checkpoint / "head.pt").is_file() else checkpoint / "bf16"
    tokenizer = AutoTokenizer.from_pretrained(weights, local_files_only=True)
    fixture = json.loads((checkpoint / "smoke" / "toolcall.request.json").read_text())
    rows = encode_rows(tokenizer, DecisionRequest(**fixture), 8192)
    metadata = torch.load(weights / "head.pt", map_location="cpu", weights_only=True)
    head = PointerHead(metadata["head"]["q.weight"].shape[1], metadata["head_dim"])
    head.load_state_dict(metadata["head"])
    head.temperature = metadata["temperature"]
    head.eval()
    readout = PointerReadout(
        {key: value.float().numpy() for key, value in metadata["head"].items()},
        metadata["temperature"],
    )
    torch.manual_seed(0)
    worst = 0.0
    for row in rows:
        request = SystemOneRequest(
            state=fixture["state"],
            questions={row.question_id: fixture["questions"][row.question_id]},
        )
        record, _ = to_record(request)
        encoded = encode(tokenizer, record, max_state=8192, max_branch=8192, strict=True)
        assert row.token_ids == encoded["ids"], f"Encoding differs: {row.question_id}"
        assert row.option_indices == encoded["opt_idx"][0]
        hidden = torch.randn(len(row.token_ids), readout.hidden_size)
        with torch.no_grad():
            reference = head(hidden[-1], hidden[row.option_indices]).softmax(-1).numpy()
        actual = readout.probabilities(hidden.numpy(), row)
        worst = max(worst, float(np.max(np.abs(actual - reference))))
        np.testing.assert_allclose(actual, reference, atol=1e-6, rtol=1e-5)

    expected = json.loads((checkpoint / "smoke" / "expected.json").read_text())
    decisions = {}
    for name, entry in expected["requests"].items():
        if "tiers" in entry:
            tiers = json.loads((checkpoint / "smoke" / entry["tiers"]).read_text())
            score, cutoff = toolcall_score(entry["answers"], tiers)
            decision = "deny" if score > cutoff else "allow"
        else:
            flagged = xpia_flagged(entry["answers"]["is_xpia_attempt"]["noul"])
            decision = "flagged" if flagged else "clean"
        assert decision == entry["decision"], f"Saved decision differs: {name}"
        decisions[name] = decision
    print(
        json.dumps(
            {
                "encoded_rows_match_reference": len(rows),
                "max_pointer_probability_error": worst,
                "saved_fixture_decisions": decisions,
                "backbone_loaded": False,
                "gpu_used": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

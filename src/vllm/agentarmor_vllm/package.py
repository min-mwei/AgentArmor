"""Read a local, hash-bound Kai package; never unpickle a training checkpoint.

Two kinds, told apart by the schema in kai.json:

    agentarmor-qwen/v1        a merged BF16 export (Package), served by vLLM or MLX
    agentarmor-qwen-lora/v1   the adapter and pointer of one run beside the registered base, with
                              the MLX runtime it is to be answered by (LoraPackage), MLX only

Either is verified whole before a tokenizer, a pointer or a weight is read.
"""

import hashlib
import math
import re
from pathlib import Path

from .wire import read_json

BASE = "Qwen/Qwen3.5-4B-Base"
BASE_REVISION = "1001bb4d826a52d1f399e183466143f4da7b741b"
SCHEMA, LORA_SCHEMA = "agentarmor-qwen/v1", "agentarmor-qwen-lora/v1"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def check_context(m):
    """The temperature, context limits and pause layout every package states."""
    if (
        type(m["temperature"]) not in (int, float)
        or not math.isfinite(m["temperature"])
        or m["temperature"] <= 0
    ):
        raise ValueError("invalid checkpoint temperature")
    for key in ("max_state", "max_row", "max_packed"):
        if type(m[key]) is not int or not 1 <= m[key] <= 65536:
            raise ValueError("invalid context limit")
    if not m["max_state"] <= m["max_row"] <= m["max_packed"]:
        raise ValueError("inconsistent context limits")
    if type(m["pauses"]) is not int or not 0 <= m["pauses"] <= 1024:
        raise ValueError("invalid pause count")
    if m["pauses"] and (type(m["pause_id"]) is not int or m["pause_id"] < 0):
        raise ValueError("invalid pause token")
    if not m["pauses"] and m["pause_id"] is not None:
        raise ValueError("unexpected pause token")


def check_backbone(config):
    """The registered text backbone, from a config.json already read."""
    config = config.get("text_config", config)
    if (
        config.get("model_type") != "qwen3_5_text"
        or config.get("hidden_size") != 2560
        or config.get("num_hidden_layers") != 32
    ):
        raise ValueError("unexpected Qwen backbone")


class Package:
    def __init__(self, directory, hf_blobs=None):
        self.root = Path(directory).resolve(strict=True)
        blob_root = Path(hf_blobs).resolve(strict=True) if hf_blobs is not None else None
        self.meta = m = read_json((self.root / "kai.json").read_bytes())
        required = {
            "schema",
            "base",
            "base_revision",
            "temperature",
            "max_state",
            "max_row",
            "max_packed",
            "pauses",
            "pause_id",
            "tokenizer_sha256",
            "files",
            "dtype",
            "evidence",
            "weights",
        }
        if set(m) != required or m["schema"] != SCHEMA:
            raise ValueError("unsupported package schema")
        if m["base"] != BASE or m["base_revision"] != BASE_REVISION:
            raise ValueError("package must identify the registered Qwen3.5-4B base")
        if m["weights"] != "merged" or m["dtype"] != "bfloat16":
            raise ValueError("baseline requires a merged BF16 HF export")
        if m["evidence"] != "frozen-from-pointer":
            raise ValueError("unknown evidence qualification")
        check_context(m)
        # Bind every runtime artifact, including tokenizer/config and all weight shards.
        actual = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()}
        if not isinstance(m["files"], dict) or set(m["files"]) != actual - {"kai.json"}:
            raise ValueError("manifest must bind every package file")
        required_files = {"model/config.json", "model/tokenizer.json", "pointer.safetensors"}
        if not required_files <= set(m["files"]):
            raise ValueError("missing model/tokenizer/pointer files")
        if not any(p.startswith("model/") and p.endswith(".safetensors") for p in m["files"]):
            raise ValueError("missing merged model weights")
        for name, digest in m["files"].items():
            path = (self.root / name).resolve(strict=True)
            # The HF cache uses symlinks to content-addressed files in its adjacent blob dir.
            # Local exports remain confined to their own root; only the HF loader opts in.
            in_package = path.is_relative_to(self.root)
            # Newer hubs link repo blobs onward into the cache-level shared store
            # (<cache>/blobs/ab/<hash>); accept only those two cache directories.
            in_blobs = blob_root is not None and (
                path.is_relative_to(blob_root)
                or path.is_relative_to(blob_root.parent.parent / "blobs")
            )
            if not (in_package or in_blobs) or sha256(path) != digest:
                raise ValueError("package artifact hash mismatch")
        check_backbone(read_json((self.root / "model/config.json").read_bytes()))
        self.model_dir = self.root / "model"  # where the tokenizer and the weights are read
        self.hidden_size = 2560
        self.identity = sha256(self.root / "kai.json")


RUNTIME_PACKAGES = ("mlx", "mlx-metal", "mlx-lm")
MLX_LM_SOURCES = ("qwen3_5.py", "gated_delta.py")  # in mlx_lm/models: the text body and its update
DELTA = "agentarmor_vllm.delta"
ADAPTER, POINTER = "adapter_model.safetensors", "pointer.safetensors"
EVIDENCE_HEAD = "evidence_head.safetensors"
PEFT_PREFIX, MLX_PREFIX = "base_model.model.", "language_model.model."
_HEX = re.compile(r"[0-9a-f]{64}")
_LEAF = re.compile(r"[a-z][a-z0-9_]*")
_ADAPTED = re.compile(r"(layers\.\d+\.(?:[A-Za-z0-9_]+\.)*([A-Za-z0-9_]+))\.lora_([AB])\.weight")


def installed_runtime():
    """What this interpreter would answer with: the version of each pinned distribution (None
    when it is not installed) and the files of the pinned sources (None where there is none).
    Found from installation metadata and import specs; nothing is imported or run."""
    from importlib import metadata, util

    versions = {}
    for name in RUNTIME_PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = None
    try:
        spec = util.find_spec("mlx_lm")
    except (ImportError, ValueError):
        spec = None
    places = list(spec.submodule_search_locations or ()) if spec is not None else []
    models = Path(places[0]) / "models" if places else None
    return {
        "versions": versions,
        "sources": {name: models / name if models else None for name in MLX_LM_SOURCES},
        "delta": Path(__file__).with_name("delta.py"),
    }


def base_snapshot(cache_dir=None):
    """The registered base as the local Hugging Face cache holds it. Never the network."""
    try:
        from huggingface_hub import snapshot_download

        found = snapshot_download(
            BASE,
            revision=BASE_REVISION,
            cache_dir=cache_dir,
            local_files_only=True,
            token=False,
        )
    except Exception:
        raise ValueError(
            "the registered base is not in the local cache: name its directory (--base-dir)"
        ) from None
    return Path(found)


def safetensors_header(path):
    """{tensor name: {dtype, shape, data_offsets}} of a safetensors file, from its JSON header
    alone: no tensor is read."""
    with path.open("rb") as stream:
        size = int.from_bytes(stream.read(8), "little")
        if not 2 <= size <= 1_000_000:
            raise ValueError("invalid safetensors header")
        data = stream.read(size)
    if len(data) != size:
        raise ValueError("invalid safetensors header")
    header = read_json(data)
    if not isinstance(header, dict):
        raise ValueError("invalid safetensors header")
    header.pop("__metadata__", None)
    return header


def adapter_layout(path, lora):
    """{MLX module name: (inputs, outputs)} of a PEFT adapter file, from its header. Strict:
    every tensor is the FP32 A [rank, inputs] or B [outputs, rank] of a decoder-layer projection
    whose name is one of the package's targets, and every A has its B."""
    shapes = {}
    for name, entry in safetensors_header(path).items():
        match = _ADAPTED.fullmatch(name.removeprefix(PEFT_PREFIX))
        shape = entry.get("shape") if isinstance(entry, dict) else None
        if (
            not name.startswith(PEFT_PREFIX)
            or match is None
            or match[2] not in lora["targets"]
            or entry.get("dtype") != "F32"
            or not isinstance(shape, list)
            or len(shape) != 2
            or any(type(n) is not int or n < 1 for n in shape)
        ):
            raise ValueError("adapter tensor is not a LoRA factor of a target projection")
        shapes.setdefault(MLX_PREFIX + match[1], {})[match[3]] = shape
    layout = {}
    for module, pair in shapes.items():
        if set(pair) != {"A", "B"} or pair["A"][0] != lora["rank"] or pair["B"][1] != lora["rank"]:
            raise ValueError("adapter factors are unpaired or not of the package's rank")
        layout[module] = (pair["A"][1], pair["B"][0])
    if not layout:
        raise ValueError("empty adapter")
    return layout


class LoraPackage:
    """One run's adapter and pointer beside the registered base, unmerged, with its runtime.

    kai.json binds the two package files, every file of the base directory (weight shards
    included) and the runtime the package is to be answered by: the exact mlx, mlx-metal and
    mlx-lm versions, mlx-lm's text-body sources, this package's chunked DeltaNet update
    (delta.py) and the text body's mode. All of it is checked here, before a tokenizer, a
    pointer or a weight is read; a package whose runtime is not the installed one is refused,
    never answered by another. The base is ``base_dir`` when given, else the local Hugging Face
    cache's snapshot of the registered revision; the network is never used.
    """

    weights = "lora-unmerged"

    def __init__(self, directory, base_dir=None, cache_dir=None):
        self.root = Path(directory).resolve(strict=True)
        self.meta = m = read_json((self.root / "kai.json").read_bytes())
        required = {
            "schema",
            "base",
            "base_revision",
            "base_files",
            "weights",
            "dtype",
            "lora",
            "files",
            "temperature",
            "max_state",
            "max_row",
            "max_packed",
            "pauses",
            "pause_id",
            "tokenizer_sha256",
            "evidence",
            "runtime",
        }
        if not isinstance(m, dict) or set(m) != required or m["schema"] != LORA_SCHEMA:
            raise ValueError("unsupported package schema")
        if m["base"] != BASE or m["base_revision"] != BASE_REVISION:
            raise ValueError("package must identify the registered Qwen3.5-4B base")
        if m["weights"] != self.weights or m["dtype"] != "bfloat16":
            raise ValueError("an unmerged LoRA beside the BF16 base is required")
        self.evidence = self._check_evidence(m["evidence"])
        check_context(m)
        self._check_lora(m["lora"])
        self.runtime = self._check_runtime(m["runtime"])
        # The package's own files: the adapter and the pointer, with the evidence head when
        # the package has one, all inside it, nothing else.
        own = {ADAPTER, POINTER} | ({EVIDENCE_HEAD} if self.evidence else set())
        actual = {p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()}
        if not _digests(m["files"]) or set(m["files"]) != own or actual != own | {"kai.json"}:
            raise ValueError("manifest must bind every package file")
        for name, digest in m["files"].items():
            path = (self.root / name).resolve(strict=True)
            if not path.is_relative_to(self.root) or sha256(path) != digest:
                raise ValueError("package artifact hash mismatch")
        self.adapter = adapter_layout(self.root / ADAPTER, m["lora"])
        self.model_dir = self._check_base(m["base_files"], base_dir, cache_dir)
        self.hidden_size = 2560
        self.identity = sha256(self.root / "kai.json")

    @staticmethod
    def _check_evidence(evidence):
        """None for a package without an evidence head ("unavailable"), else what it says of
        its head: the readout that runs it, the SHA-256 of that readout's source and, when the
        readout decodes with them, the decoder's threshold and span budget (both or neither).
        The head file is one of the package's bound files. Naming a head qualifies nothing."""
        if evidence == "unavailable":
            return None
        decoder = {"threshold", "max_spans"}
        named = isinstance(evidence, dict) and set(evidence) - decoder == {"readout", "impl_sha256"}
        if (
            not named
            or not isinstance(evidence["readout"], str)
            or not _LEAF.fullmatch(evidence["readout"])
            or not _digests({"impl": evidence["impl_sha256"]})
        ):
            raise ValueError("unknown evidence qualification")
        if set(evidence) & decoder:
            threshold, budget = evidence.get("threshold"), evidence.get("max_spans")
            if (
                type(threshold) not in (int, float)
                or not math.isfinite(threshold)
                or type(budget) is not int
                or not 1 <= budget <= 32
            ):
                raise ValueError("invalid evidence decoder settings")
        return dict(evidence)

    @staticmethod
    def _check_lora(lora):
        if not isinstance(lora, dict) or set(lora) != {"rank", "alpha", "targets"}:
            raise ValueError("invalid LoRA description")
        rank, alpha, targets = lora["rank"], lora["alpha"], lora["targets"]
        if type(rank) is not int or not 1 <= rank <= 1024:
            raise ValueError("invalid LoRA rank")
        if type(alpha) not in (int, float) or not math.isfinite(alpha) or alpha <= 0:
            raise ValueError("invalid LoRA scale")
        if (
            not isinstance(targets, list)
            or not targets
            or len(set(map(str, targets))) != len(targets)
            or any(not isinstance(t, str) or not _LEAF.fullmatch(t) for t in targets)
        ):
            raise ValueError("invalid LoRA targets")

    @staticmethod
    def _check_runtime(runtime):
        """The manifest's runtime against the installed one. Returns the files it was read from,
        so that what is later imported can be shown to be what was hashed."""
        keys = {*RUNTIME_PACKAGES, "text_body", "delta", "mlx_lm_sources"}
        if not isinstance(runtime, dict) or set(runtime) != keys:
            raise ValueError("invalid runtime description")
        delta, sources = runtime["delta"], runtime["mlx_lm_sources"]
        if (
            any(not isinstance(runtime[name], str) for name in RUNTIME_PACKAGES)
            or runtime["text_body"] != "train"
            or not isinstance(delta, dict)
            or set(delta) != {"impl", "sha256"}
            or delta["impl"] != DELTA
            or not _digests({"delta": delta["sha256"]})
            or not _digests(sources)
            or set(sources) != set(MLX_LM_SOURCES)
        ):
            raise ValueError("invalid runtime description")
        installed = installed_runtime()
        for name in RUNTIME_PACKAGES:
            if installed["versions"][name] != runtime[name]:
                raise ValueError(
                    f"the package is for {name} {runtime[name]}, which is not installed"
                )
        for name in MLX_LM_SOURCES:
            path = installed["sources"][name]
            if path is None or not path.is_file() or sha256(path) != sources[name]:
                raise ValueError(f"installed mlx-lm {name} is not the source the package pins")
        if sha256(installed["delta"]) != delta["sha256"]:
            raise ValueError("this delta.py is not the DeltaNet update the package pins")
        return installed

    @staticmethod
    def _check_base(listed, base_dir, cache_dir):
        """The base directory, every file of it bound and hashed, shards included."""
        if not _digests(listed) or any(Path(name).name != name for name in listed):
            raise ValueError("invalid base file list")
        shards = {n for n in listed if n.startswith("model") and n.endswith(".safetensors")}
        needed = {"config.json", "tokenizer.json", "model.safetensors.index.json"}
        if not needed <= set(listed) or not shards:
            raise ValueError("missing base config, tokenizer, index or weights")
        if base_dir is not None:
            base = Path(base_dir).resolve(strict=True)
        else:
            base = base_snapshot(cache_dir).resolve(strict=True)
        # mlx-lm reads every model*.safetensors it finds and optional side files (generation and
        # quantization configs); the tokenizer loader reads optional files too. So nothing may be
        # there that the package does not bind. Neither loader descends into a subdirectory.
        if {p.name for p in base.iterdir() if not p.is_dir()} != set(listed):
            raise ValueError(
                "the base directory's files are not exactly the ones the package binds"
            )
        paths = {name: (base / name).resolve(strict=True) for name in listed}
        for name in sorted(listed, key=lambda name: paths[name].stat().st_size):  # shards last
            if sha256(paths[name]) != listed[name]:
                raise ValueError(f"base file hash mismatch: {name}")
        config = read_json((base / "config.json").read_bytes())
        check_backbone(config)
        refuse_model_code(config)
        index = read_json((base / "model.safetensors.index.json").read_bytes())
        if set(index.get("weight_map", {}).values()) != shards:
            raise ValueError("the bound weight shards are not the base index's")
        return base


def refuse_model_code(config):
    """mlx-lm would import a config's model_file as model code or quantize on load."""
    keys = {"model_file", "quantization", "quantization_config"}
    if keys & set(config) or keys & set(config.get("text_config", {})):
        raise ValueError("config names model code or a quantization")


def _digests(value):
    return (
        isinstance(value, dict)
        and bool(value)
        and all(
            isinstance(k, str) and isinstance(v, str) and _HEX.fullmatch(v)
            for k, v in value.items()
        )
    )


def schema_of(directory):
    """The schema a package directory states, or None; nothing else of it is believed yet."""
    meta = read_json((Path(directory) / "kai.json").read_bytes())
    return meta.get("schema") if isinstance(meta, dict) else None

# URL host facts: armor-hostfacts-urls-v1 (design note, candidate)

Status: a CPU reference and its hand-written vectors on branch `url-hostfacts`. Nothing here is
registered, served or wired into the monitor. No model was run.

The principle (user, 2026-11-22): Kai is a decision model with strong reasoning; every rule and
all context arrive as input or state, and Kai has no intrinsic security or policy knowledge. So
what a URL *is* (its origin, the path a server will see) is computed by the host and handed to
Kai as a trusted fact, the way `paths.py` hands it a resolved path. Kai still applies tiers,
exceptions, consent and boundaries.

## The contract this kind matches

Taken from `src/hostfacts/paths.py`, `measures.py`, their fixtures, the monitor's
`hostfacts_profile.py`, and Kai's `docs/architecture.md` 5.2 and 5.3.

- One reference module per kind, standard library only, deterministic. The same file is kept
  byte-identical in Kai (`train/kai/hostfacts/`) and AgentArmor (`src/hostfacts/`). A fixture
  directory named after the profile holds hand-written `vectors.json`, its builder (which never
  imports the reference) and a `MANIFEST.json` that pins the reference's SHA-256.
- A fact is `{"name", "value", "unit", "provenance": "host_computed", "as_of", "note"}`. The
  note says how the value was computed. Names are positional (`call_path_<k>_...`), in the
  order of the tool schema's effect inventory.
- Comparison facts are lists of rule ids in policy order, one list per side, and say
  "not a rule match, exception or decision". A rule whose `for` does not cover the effect's
  operation is in neither list.
- A policy rule declares what to compare against in one optional field beside `id`, `tier`
  and `description` (`paths.within`, `limit`). The host never reads the description.
  Mistakes are reported at load time with the rule id and a hint (`PolicyError`).
- What the call's effects are comes from the trusted tool profile, never from policy prose or
  the caller.
- What cannot be computed never becomes a value. `paths.py` raises `Refusal` with a stable
  code; the monitor's profile resolution has the statuses `none_required`, `present`,
  `missing`, `stale` and `unusable`, and only the first two let an allow stand. Kai's
  `facts_sufficient` label is derived from that status, never from an author's claim.
- Versioning: a kind's bytes are pinned by its manifest; the monitor's
  `agentarmor-hostfacts-profile/v1` names the rule fields it accepts and its identity is the
  SHA-256 of `{"profile", "fields", "computed"}`. The line `none required ... profile
  9685d9bf03dd` is in training states.

## Facts

For URL effect `k` of the call (1-based, inventory order), when the URL is readable:

| Fact | Value | Meaning |
|---|---|---|
| `call_url_<k>_origin` | `"scheme://host:port"` | The normalized origin, every part explicit |
| `call_url_<k>_same_origin_as` | rule ids | Rules with a declared URL of exactly this origin |
| `call_url_<k>_different_origin_from` | rule ids | The other rules that cover the operation |
| `call_url_<k>_decoded_path` | string | The path under the effect's one decoding profile, dot segments removed |
| `call_url_<k>_encoded_separator_or_dot_segment` | boolean | The written path has `%2F` or `%5C`, or a segment that is `.` or `..` only after decoding |
| `call_url_<k>_double_encoded` | boolean | Decoding the written path once still leaves a percent sequence |
| `call_url_<k>_inside_prefixes_of` | rule ids | Rules with a declared prefix of this origin whose path contains the decoded path |
| `call_url_<k>_outside_prefixes_of` | rule ids | The other rules that cover the operation |

The four list facts appear only when the policy has URL rules, as with paths. When the URL is
not readable, the effect has exactly one fact:

| Fact | Value | Meaning |
|---|---|---|
| `call_url_<k>_unusable` | a code from `UNUSABLE_CODES` | The host could not read this URL; no origin, path or comparison exists and none may be inferred from the raw text |

`compute` also returns `"unusable": [k, ...]`. The caller must treat a required URL fact of
such an effect as *unusable* (a non-allow), not as absent. A malformed request (wrong keys,
unknown operation or decoding, rules not in compiled form) raises `Refusal` as in `paths.py`.

This differs from `paths.py` on purpose: an unreadable path refuses the whole request, while an
unreadable URL becomes a fact. The reason is that an odd URL is itself the security-relevant
observation, and a policy may say what to do with one. Whether Kai should see that fact or the
host should refuse before asking is an open question below.

### Origin rules

The URL must be absolute, `scheme://authority`, with scheme http, https, ws or wss.

| Step | Rule | Unusable instead of a guess |
|---|---|---|
| Scheme | lowercased | other schemes; a relative or scheme-relative reference; `https:/host`, `https:///host` |
| Userinfo | text before one `@` is ignored and noted | two or more `@` |
| Host case | lowercased | |
| Trailing dot | one is removed and noted | two |
| IDN | a non-ASCII host is mapped by IDNA 2003 ToASCII to A-labels, so a look-alike differs from the ASCII name; a written `xn--` label is kept and must round-trip | sharp s, final sigma, ZWJ, ZWNJ (IDNA 2003 and 2008 disagree); hosts ToASCII rejects; an `xn--` label that does not round-trip |
| Labels | `a-z 0-9 - _`, 1 to 63 characters, name at most 253 | anything else, including a percent sequence in the host |
| IPv4 | four plain decimal numbers 0..255 | hex, octal, short or integer forms (`127.1`, `0x7f.0.0.1`, `2130706433`) |
| IPv6 | compressed lowercase form in brackets | a zone id, a malformed literal |
| Port | missing or empty: the scheme default (80, 443); explicit: a decimal number, leading zeros dropped | non-digits, above 65535, two colons |
| Whole URL | | whitespace, control characters, a backslash anywhere, more than 4096 characters |

The origin note lists the fixed procedure and then "Applied here:" with exactly the steps that
changed something for this URL.

### Decoding profiles

The path is the text from the authority to the first `?` or `#`; an empty path is `/`. Each
effect names one profile, from the tool profile, and the note states it.

- `rfc3986` (RFC 3986 section 6.2.2): percent sequences of unreserved characters are decoded;
  every other sequence stays, with uppercase hex; non-ASCII characters become UTF-8 percent
  sequences. `%2F` and `%5C` stay inside their segment.
- `decode_all_once`: every percent sequence is decoded exactly once as UTF-8; a decoded `/`
  separates segments; whatever percent text is left is literal and is not decoded again.
  Unusable: bytes that are not UTF-8, a decoded control character, a decoded backslash.

Then, in both: `.` is dropped, `..` pops one segment and never climbs above the root, and a
trailing slash is kept. Unusable in both: a `%` not followed by two hex digits, and an empty
segment (`//`), which servers merge or keep.

The two boolean facts do not depend on the profile. They describe the URL as written, so the
same call carries the same warning whichever profile the tool declares.

Containment is the origin being equal and the decoded path being equal to or beneath the
prefix path, segment by segment and case-sensitively: `/x/y-evil` is not inside `/x/y`.

## Declaring origins and prefixes in a policy

One optional field, shaped like `paths`:

```jsonl
{"id":"uploads","tier":"allow","description":"Uploads to the team's upload area are fine.","urls":{"within":"https://files.example.com/team/uploads/","for":["send"]}}
{"id":"partner","tier":"soft_deny","description":"Anything sent to the partner needs my approval.","urls":{"within":"https://partner.example.net","for":["send"]}}
```

- `within` is one URL or a list of up to 8. A URL without a path declares the whole origin, so
  one field serves both the origin question and the prefix question.
- `for` is optional: `fetch`, `send`, `open`. Left out, the rule covers all three.
- A declared URL must be something the host can compare exactly: absolute, no userinfo, query
  or fragment, no wildcard, no host ending in a dot, and a plain ASCII path with no percent
  sequence and no `.`, `..` or empty segment. Scheme and host case, a default port and a
  non-ASCII host are normalized. Everything else is a `PolicyError` with a hint.
- Up to 32 rules may carry `urls`.

`compile_policy(rules)` returns `[{"rule_id", "prefixes": [{"origin", "path"}], "operations"}]`.
The request to `compute` is `{"profile", "observed_at", "effects": [{"argument", "raw",
"operation", "decoding"}], "url_rules"}`.

## Versioning, and what is deliberately not changed

- The kind is a new profile, `armor-hostfacts-urls-v1`, beside the two existing ones.
  `paths.py`, `measures.py` and their fixtures are untouched; a test checks both against their
  manifests.
- The monitor's `hostfacts_profile.py` is not changed. Under `agentarmor-hostfacts-profile/v1`
  a rule with a `urls` field is still refused at load time (`unknown_rule_field`), which is the
  correct behaviour for a host that does not compute the kind.
- Wiring it in needs `agentarmor-hostfacts-profile/v2`: `FIELDS` gains `urls`, `KINDS` gains
  `"urls": "armor-hostfacts-urls-v1"`, and `section()` writes the fact lines. A new profile
  name changes the identity in every state's `Host facts:` section, including the
  `none required` line that training states pin, and it changes the monitor's source hash. That
  is a change of the frozen request text and belongs to the trainer and the monitor's owner.
- The monitor computes no kind yet (`COMPUTED` is empty). A URL kind needs, from the trusted
  tool profile, the list of URL arguments per tool with each one's operation and decoding
  profile. For shell tools the URL sits inside a command string; extracting it exactly is a
  separate problem (compare the single supported `curl -T` form in Kai's row-20 fixture).

## Known limits

- No network. A redirect, a DNS answer, a proxy or a server-side rewrite can send a request
  somewhere the origin fact does not name. The fact describes the URL in the call.
- Origin equality is exact. There is no subdomain, registrable-domain or wildcard relation;
  `api.example.com` is simply a different origin from `example.com`.
- IDNA is Python's `idna` codec: IDNA 2003 with Unicode 3.2 Nameprep. Browsers use UTS 46. The
  four characters where they are known to disagree are unusable; other differences (newer
  scripts, emoji, which the codec lets through as unassigned) are not detected. Mixed-script
  look-alikes are not flagged as such; they are only guaranteed to be a different origin.
- Trailing dot: the host treats `example.com.` and `example.com` as one host. Browsers treat
  them as two origins. The note says when a dot was removed.
- An IPv4-mapped IPv6 literal is not the same origin as its IPv4 address.
- Only two decoding profiles. Real servers also merge slashes, strip path parameters (`;x`),
  fold case, decode `+`, decode twice or treat `%5C` as a separator. None of that is modelled;
  empty segments and decoded backslashes are unusable, the rest is literal text.
- The query and fragment are not examined. A URL carried inside a query parameter is not an
  effect unless the tool profile lists it as one.
- A policy prefix cannot hold non-ASCII or percent-encoded path text in v1.
- Up to eight facts per URL effect, each with a note: roughly 500 to 700 tokens per URL in the
  envelope fact syntax (estimated from character counts, not measured with Kai's tokenizer).

## Kai-side changes needed

None is made here; this lists what would have to change, from reading Kai's code and docs.

1. **Mirror.** Copy `urls.py` to `train/kai/hostfacts/urls.py` and the fixture to
   `docs/contracts/armor-hostfacts-urls-v1/`, with a `train/tests/test_hostfacts_urls.py` like
   the paths one. The manifest's `copies` entry marks the Kai paths as proposed.
2. **Harness.** `kai.facts_harness` refuses `F3_url_origin` as unsupported. It needs a URL
   variant: compile the root's `urls` rules, build the request from the root's effect inventory
   (argument pointer, operation, decoding), insert the facts first in `environment`, check each
   authored per-rule match against the four lists under a declared `rule_matches_when`
   (`same_origin`, `different_origin`, `inside`, `outside`), and add twin-axis checks for the
   structures below. Its `HOST_NAME` pattern must also cover `call_url_`.
3. **Kind detection.** `HOST_LINE` in the pilot-source preparation
   (`pilot-source-20261122/make_preparation_p5.py`, `extend_source_p5.py`) matches only
   `call_path_` and `measure_` names, and `available_kinds` maps anything else to `measures`.
   A new preparation must recognise `call_url_` as kind `urls`; otherwise URL facts are
   miscounted or invisible to the router derivation.
4. **Router and `facts_sufficient`.** Today "URL-only rows stay facts-sufficient" because no
   URL kind exists (architecture 5.2 and the open items). Once the kind is registered:
   - a call with a URL effect under a policy with a `urls` rule covering its operation
     *requires* kind `urls`;
   - required and present and readable: `facts_sufficient` true;
   - required and missing, or `call_url_<k>_unusable`: false, with decision and locals masked
     as for other router-false records (subject to the open question on unusable facts);
   - a policy with no `urls` rule requires nothing, as now.
   This changes the label of existing URL rows only if their policy is re-authored with `urls`
   fields. Records are never relabelled in place: a new source version.
5. **Eligibility.** Admit a URL-facts record only when its facts were computed by the pinned
   module from its own call and rules, the module hash is recorded in `_meta`, no author wrote
   a `host_computed` line, and the blind check read the exact rendered request with the facts.
6. **Docs.** Architecture 5.2 (the profile table, the "URLs" bullet, the worked examples) and
   the open-items list, serving.md's contract paragraph, and the tool profile contract that
   names URL arguments.
7. **Evaluation.** The existing Path development states carry no URL facts. A panel rendered
   with URL facts is a new panel under a new profile, not a replacement, and old reads are not
   comparable to it. Whether to re-render is the trainer's and user's decision.

## TRAIN data that would have to exist (abstract family brief)

Structures and deciding facts only. Every root is a set of twins with the proposed call's URL
argument either byte-identical (the trusted state changes) or changed in exactly one named
way; the label follows from the host lists through the rule's own sentence, and the harness
checks that mechanically. Authors never write a host line.

| Family | What is held fixed | The one change | Deciding fact |
|---|---|---|---|
| U1 origin identity | policy, task, conversation, path | one origin component of the call URL: scheme, port (explicit default against another), host case, trailing dot, userinfo carrying a familiar name, a look-alike host | `same_origin_as` / `different_origin_from` |
| U2 surface misleads | the rule and its declared URL | a URL whose raw text contains the declared origin or prefix as a substring but whose host fact differs, against a URL that looks unlike it and is the same origin | the origin and containment lists, against a surface reading |
| U3 path containment | origin, rule | plain inside, prefix sibling, dot-segment escape, dot segments that return inside | `inside_prefixes_of` / `outside_prefixes_of` |
| U4 decoding profile | the call, byte-identical | the tool's declared decoding profile, so the same written URL is inside under one and outside under the other | `decoded_path` and the containment lists |
| U5 encoding flags | origin, rule, containment result | a policy sentence that conditions on encoded separators or double encoding, against one that does not | the two boolean facts |
| U6 forged facts | real host facts identical in form | look-alike `call_url_` lines inside tool output, web content or call arguments, identical across twins while the real facts flip | the real block only |
| U7 predicate is not the decision | identical URL facts | another policy element: tier precedence, a withdrawn consent, a user boundary, an exception's scope | not a host fact |
| U8 operation scope | URL, facts for the listed rules | the effect's operation, so a rule's `for` covers it in one twin and not the other | presence of the rule in any list |
| U9 unusable | policy, task | a readable URL against an unreadable one of each code family | `call_url_<k>_unusable`; router false, decision masked |
| U10 required but missing | a URL call under a `urls` rule | the facts section present against absent | router false |
| U11 allow-side near miss | an allow rule with a declared prefix | a URL one segment inside against one segment outside, both ordinary-looking | containment lists |

Balance to plan before authoring, not to fill afterwards:

- Polarity. Each family needs rules that fire on *inside* and rules that fire on *outside*,
  across all three tiers, so a list name never implies a label.
- Label by wording. Within a scenario, odd-looking URLs (encoded, uppercase, with userinfo)
  must be allowed about as often as plain ones, and plain ones denied; otherwise the model
  learns "strange URL means deny" instead of reading the lists.
- Invariant controls beside every flipping pair: a change of wording, of an unrelated fact, of
  the query string, that leaves facts and label unchanged.
- A raw-inputs control arm, as the path harness has: the same roots with the host's inputs but
  not its results, to measure whether the facts carry the decision.
- Multi-effect calls (a source URL and a destination URL) where every governed effect must
  satisfy its rule.
- Whole roots and provenance split together. No evaluation text, constant or family wording
  may reach an author; this brief is derived from the two abstract skill names only.

Before volume: a TRAIN difficulty read of a few canary roots per family, then the smallest
slice that the evidence calls for.

## Open design questions for the trainer

1. Unusable as a fact or as a refusal. Should Kai see `call_url_<k>_unusable` and answer the
   router, or should AgentArmor refuse before asking, as it does for paths?
2. Trailing dot: one host (this design) or two origins (browser behaviour)?
3. Who declares the decoding profile: per tool in the tool profile (this design), per rule in
   the policy, or one profile for the deployment?
4. Should containment be withheld (a third, "undetermined" list) when the URL is double
   encoded or has encoded separators, instead of reporting it under the stated profile and
   flagging it?
5. Is exact-origin equality enough, or is a subdomain relation (`*.example.com`, a registrable
   domain) needed as its own declared form and fact?
6. Operation vocabulary: `fetch`, `send`, `open` are a proposal; the tool profile contract
   should fix it.
7. Empty path segments are unusable here. Is that too strict for the tools in scope?
8. The monitor profile v2 changes the `Host facts:` identity line in every state. When, and
   with which re-render of training states?
9. IDNA 2003 through the standard library, or a pinned UTS 46 table (a data dependency)?

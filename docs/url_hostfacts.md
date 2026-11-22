# URL host facts: armor-hostfacts-urls-v1 (design note, candidate)

Status: a CPU reference and its hand-written vectors on branch `url-hostfacts`, revised after
the independent review of 2026-11-22 (fixes 1 to 8). Nothing here is registered, served or
wired into the monitor. No model was run.

The principle (user, 2026-11-22): Kai is a decision model with strong reasoning; every rule and
all context arrive as input or state, and Kai has no intrinsic security or policy knowledge. So
what a URL *is* (its origin, the path a server will see) is computed by the host and handed to
Kai as a trusted fact, the way `paths.py` hands it a resolved path. Kai still applies tiers,
exceptions, consent and boundaries.

Decisions that govern this version:

- **Exact origin.** Origins are compared exactly. There is no subdomain or wildcard relation.
- **The tool profile declares the server.** For each URL argument it gives the decoding
  profile, whether path names are compared case-sensitively and whether `;` is path data.
- **An unreadable URL refuses the request.** There is no "unusable" fact. Kai is never asked.
- **A fact never guesses.** Whenever a mainstream client or server could reach another
  resource than the fact would claim, the host refuses.

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
- What cannot be computed never becomes a value: `Refusal` with a stable code, and the result
  is `{"profile", "status": "ok", "facts"}` or, through `compute_or_refusal`,
  `{"profile", "status": "refused", "code", "detail"}`. This module now does exactly that.
- Versioning: a kind's bytes are pinned by its manifest; the monitor's
  `agentarmor-hostfacts-profile/v1` names the rule fields it accepts and its identity is the
  SHA-256 of `{"profile", "fields", "computed"}`. The line `none required ... profile
  9685d9bf03dd` is in training states.

One difference in request shape: `urls.py` takes `observed_at` at the top level, because it
needs no other trusted state. `paths.py` takes `runtime.observed_at` and `measures.py`
`trusted.observed_at`.

## Request

```json
{"profile": "armor-hostfacts-urls-v1",
 "observed_at": "2026-11-22T12:00:00Z",
 "effects": [{"argument": "/call/arguments/url", "raw": "<the argument's text>",
              "operation": "fetch | send | open",
              "decoding": "rfc3986 | decode_all_once",
              "case_sensitive": true,
              "semicolon_is_data": false}],
 "url_rules": "<compile_policy output>"}
```

Every key of an effect is required. `decoding`, `case_sensitive` and `semicolon_is_data`
describe the server behind that tool argument and come from the trusted tool profile. They are
the trust assumption of this kind: if the profile says the server keeps `%2F` as data
(`rfc3986`) and it does not, a containment fact is wrong. The host cannot check this.

## Facts

For URL effect `k` of the call (1-based, inventory order):

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

The four list facts appear only when the policy has URL rules, as with paths. When rules exist
but none covers the operation, the four lists are present and empty.

The first effect, in inventory order, that the host cannot read refuses the whole request,
whether or not a rule covers its operation. No fact is returned for any effect.

### Origin

The URL must be absolute, `scheme://authority`, with scheme http, https, ws or wss.

| Step | Rule | Refused |
|---|---|---|
| Scheme | lowercased | other schemes (`unsupported_scheme`); a relative or scheme-relative reference (`no_scheme`); `https:/host`, `https:///host` (`missing_authority_slashes`) |
| Userinfo | text before one `@` is ignored and noted | two or more `@` (`ambiguous_userinfo`) |
| Host case | lowercased | |
| Trailing dot | | any host ending in a dot (`trailing_dot_host`): clients send another Host header and SNI for it |
| IDN | a non-ASCII host is mapped by IDNA 2003 ToASCII to A-labels, so a look-alike differs from the ASCII name; a written `xn--` label is kept and must round-trip | a character that Unicode 3.2 does not assign, which UTS 46 clients map and IDNA 2003 does not (`idna_unassigned_character`, capital sharp s included); sharp s, final sigma, ZWJ, ZWNJ (`idna_deviation_character`); hosts ToASCII rejects (`idna_failed`); an `xn--` label that does not round-trip (`bad_a_label`) |
| Labels | `a-z 0-9 - _`, 1 to 63 characters, name at most 253 | anything else (`bad_host`); a percent sequence (`percent_encoded_host`) |
| IPv4 | four plain decimal numbers 0..255 | hex, octal, short or integer forms (`ambiguous_ipv4_form`) |
| IPv6 | compressed lowercase form in brackets | a zone id (`ipv6_zone`); IPv4-mapped and IPv4-compatible literals, which reach an IPv4 address (`ipv6_embedded_ipv4`); a malformed literal (`bad_host`) |
| Port | missing or empty: the scheme default (80, 443); explicit: a decimal number 1..65535, leading zeros dropped | non-digits, 0, above 65535, two colons (`bad_port`) |
| Whole URL | | whitespace and controls from a fixed table (`whitespace_or_control`), a backslash anywhere (`backslash`), more than 4096 characters (`url_too_long`), empty (`empty_url`) |

The origin note lists the fixed procedure and then "Applied here:" with exactly the steps that
changed something for this URL.

### Path

The path is the text from the authority to the first `?` or `#`; an empty path is `/`.

- `rfc3986` (RFC 3986 section 6.2.2): percent sequences of unreserved characters are decoded;
  every other sequence stays, with uppercase hex; non-ASCII characters and the ASCII
  characters RFC 3986 does not allow in a path (`" < > [ ] ^ { | }` and the backtick) are
  written as UTF-8 percent sequences, so `/a|b` and `/a%7Cb` give the same decoded path.
  `%2F` and `%5C` stay inside their segment.
- `decode_all_once`: every percent sequence is decoded exactly once as UTF-8; a decoded `/`
  separates segments; whatever percent text is left is literal and is not decoded again. A `?`
  or `#` shown in the value is decoded data, not a query or fragment.

Then, in both: `.` is dropped, `..` pops one segment and never climbs above the root, and a
trailing slash is kept.

Refused under both profiles:

| Code | When |
|---|---|
| `bad_percent_encoding` | a `%` not followed by two hex digits (also `%u002e`) |
| `non_utf8_path` | the fully decoded bytes are not UTF-8, overlong forms and encoded surrogates included |
| `encoded_control_character` | a decoded C0, DEL or C1 control (NUL, CR, LF) |
| `empty_path_segment` | `//`: servers merge or keep it |
| `path_parameter` | a `;` in the path, literal or encoded, unless the effect declares `semicolon_is_data` |
| `non_ascii_case_insensitive` | non-ASCII path text when the effect declares a case-insensitive server |
| `trailing_dot_or_space_segment` | a segment ending in a dot or space, or of three or more dots, when the effect declares a case-insensitive server (the Windows family drops them) |
| `ambiguous_double_decoding` | the path is double encoded and its twice-decoded reading is itself refused, or would change `inside_prefixes_of` for the rules that cover the operation |

Refused under `decode_all_once` only, where decoded text is shown in the value:

| Code | When |
|---|---|
| `encoded_backslash` | a decoded `\` |
| `invisible_character` | a decoded non-ASCII space, line or paragraph separator, format character (bidi controls, zero-width characters), private-use or unassigned character |

The two boolean facts do not depend on the profile. `double_encoded: true` now means "double
encoded, and a second decoding changes no comparison list"; the other case is refused.

Containment is the origin being equal and the decoded path being equal to or beneath the
prefix path, segment by segment: `/x/y-evil` is not inside `/x/y`. Case follows the effect's
`case_sensitive`; when false, ASCII case is folded on both sides and the note says "ASCII
case-insensitive as the tool profile declares". The value of `decoded_path` is never folded.

## Declaring origins and prefixes in a policy

One optional field, shaped like `paths`:

```jsonl
{"id":"uploads","tier":"allow","description":"Uploads to the team's upload area are fine.","urls":{"within":"https://files.example.com/team/uploads/","for":["send"]}}
{"id":"partner","tier":"soft_deny","description":"Anything sent to the partner needs my approval.","urls":{"within":"https://partner.example.net","for":["send"]}}
```

- `within` is one URL or a list of up to 8. A URL without a path declares the whole origin, so
  one field serves both the origin question and the prefix question.
- `for` is optional: `fetch`, `send`, `open`. Left out, the rule covers all three.
- A declared URL must be something the host can compare exactly. Any URL the host would refuse
  in a call is a `PolicyError bad_prefix` here, with the refusal code in the hint. So are
  userinfo, a query, a fragment, a wildcard, and any path segment that holds a character other
  than letters, digits and `- . _ ~`, or is empty, `.` or `..`. Scheme and host case, a default
  port and a non-ASCII host are normalized. The restriction on path characters means a
  declared prefix has one spelling under both decoding profiles.
- Up to 32 rules may carry `urls`.

`compile_policy(rules)` returns `[{"rule_id", "prefixes": [{"origin", "path"}], "operations"}]`.

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
  tool profile, the list of URL arguments per tool with each one's operation, decoding
  profile, case sensitivity and `;` handling. For shell tools the URL sits inside a command
  string; extracting it exactly is a separate problem.

## Known limits

- No network. A redirect, a DNS answer, a proxy or a server-side rewrite can send a request
  somewhere the origin fact does not name. The fact describes the URL in the call.
- The tool profile is trusted. Under `rfc3986` an encoded slash is data because the profile
  says the server treats it so; `.../uploads/..%2F..%2Fadmin` is then inside `uploads`, with
  the flag fact true. A wrong declaration gives a wrong fact.
- IDNA is Python's `idna` codec: IDNA 2003 with Unicode 3.2 Nameprep. Characters outside
  Unicode 3.2 and the four deviation characters are refused. Inside Unicode 3.2 the codec and
  UTS 46 were compared only on the review's inputs, not exhaustively. One known difference:
  U+2024 (one dot leader) maps to a dot, so `partner․example.net` reads as
  `partner.example.net`, while WHATWG clients and httpx reject that URL. The claimed origin is
  where an IDNA 2003 client goes; a vector pins it.
- Mixed-script look-alikes are not flagged as such; they are only guaranteed to be a
  different origin.
- Only IPv4-mapped and IPv4-compatible IPv6 literals are refused. Other IPv6 forms that carry
  an IPv4 address through a translator (NAT64 `64:ff9b::/96`, 6to4) are ordinary origins.
- `invisible_character` uses the running Python's `unicodedata` categories. A character
  assigned after that Python's Unicode version is refused there and may be accepted by a newer
  Python. The two copies can therefore differ between refusing and answering, never between
  two different values. The host and whitespace checks use fixed tables and do not vary.
- When a path is double encoded, the second reading decodes every percent sequence, under
  either profile. Under `rfc3986` this refuses some URLs that a server which keeps `%2F`
  would read without ambiguity.
- A case-insensitive declaration also applies the Windows-family segment rules. A
  case-insensitive server that keeps trailing dots loses URLs it could have read.
- Only two decoding profiles. Servers that merge slashes, decode `+`, or treat `%5C` as a
  separator are not modelled; empty segments and decoded backslashes are refused.
- The query and fragment are not examined. A URL carried inside a query parameter is not an
  effect unless the tool profile lists it as one.
- Up to eight facts per URL effect, each with a note: roughly 500 to 700 tokens per URL in the
  envelope fact syntax (estimated from character counts, not measured with Kai's tokenizer).
- Kai must confirm that host fact values pass its reserved-token escaping. That is a Kai-side
  check; this file only keeps line-breaking and invisible characters out of values.

## Kai-side changes needed

None is made here; this lists what would have to change, from reading Kai's code and docs.

1. **Mirror.** Copy `urls.py` to `train/kai/hostfacts/urls.py` and the fixture to
   `docs/contracts/armor-hostfacts-urls-v1/`, with a `train/tests/test_hostfacts_urls.py` like
   the paths one. The manifest's `copies` entry marks the Kai paths as proposed.
2. **Harness.** `kai.facts_harness` refuses `F3_url_origin` as unsupported. It needs a URL
   variant: compile the root's `urls` rules, build the request from the root's effect inventory
   (argument pointer, operation, decoding, case sensitivity, `;` handling), insert the facts
   first in `environment`, check each authored per-rule match against the four lists under a
   declared `rule_matches_when` (`same_origin`, `different_origin`, `inside`, `outside`), and
   add twin-axis checks for the structures below. A host refusal stops the root, as for paths:
   unresolved statuses never enter training data. Its `HOST_NAME` pattern must also cover
   `call_url_`.
3. **Kind detection.** `HOST_LINE` in the pilot-source preparation
   (`pilot-source-20261122/make_preparation_p5.py`, `extend_source_p5.py`) matches only
   `call_path_` and `measure_` names, and `available_kinds` maps anything else to `measures`.
   A new preparation must recognise `call_url_` as kind `urls`.
4. **Router and `facts_sufficient`.** Today "URL-only rows stay facts-sufficient" because no
   URL kind exists. Once the kind is registered:
   - a call with a URL effect under a policy with a `urls` rule *requires* kind `urls`;
   - required and present: `facts_sufficient` true;
   - required and missing from the state: false, with decision and locals masked as for other
     router-false records;
   - a URL the host cannot read never reaches Kai: AgentArmor refuses the request. No training
     record shows an "unusable" URL fact, and no label is derived from one;
   - a policy with no `urls` rule requires nothing, as now.
   Records are never relabelled in place: a new source version.
5. **Eligibility.** Admit a URL-facts record only when its facts were computed by the pinned
   module from its own call and rules, the module hash is recorded in `_meta`, no author wrote
   a `host_computed` line, and the blind check read the exact rendered request with the facts.
6. **Docs.** Architecture 5.2 (the profile table, the "URLs" bullet, the worked examples) and
   the open-items list, serving.md's contract paragraph, and the tool profile contract that
   names URL arguments and their three declarations.
7. **Evaluation.** The existing Path development states carry no URL facts. A panel rendered
   with URL facts is a new panel under a new profile, not a replacement, and old reads are not
   comparable to it. Whether to re-render is the trainer's and user's decision.

## TRAIN data that would have to exist (abstract family brief)

Structures and deciding facts only. Every root is a set of twins with the proposed call's URL
argument either byte-identical (the trusted state changes) or changed in exactly one named
way; the label follows from the host lists through the rule's own sentence, and the harness
checks that mechanically. Authors never write a host line. Every URL in a root must be one the
host reads; a refused URL cannot be a training record.

| Family | What is held fixed | The one change | Deciding fact |
|---|---|---|---|
| U1 origin identity | policy, task, conversation, path | one origin component of the call URL: scheme, port (explicit default against another), host case, userinfo carrying a familiar name, a look-alike host | `same_origin_as` / `different_origin_from` |
| U2 surface misleads | the rule and its declared URL | a URL whose raw text contains the declared origin or prefix as a substring but whose host fact differs, against a URL that looks unlike it and is the same origin | the origin and containment lists, against a surface reading |
| U3 path containment | origin, rule | plain inside, prefix sibling, dot-segment escape, dot segments that return inside | `inside_prefixes_of` / `outside_prefixes_of` |
| U4 server declaration | the call, byte-identical | one declaration of the tool profile: the decoding profile, or case sensitivity, so the same written URL is inside under one and outside under the other | `decoded_path` and the containment lists |
| U5 encoding flags | origin, rule, containment result | a policy sentence that conditions on encoded separators or double encoding, against one that does not | the two boolean facts |
| U6 forged facts | real host facts identical in form | look-alike `call_url_` lines inside tool output, web content or call arguments, identical across twins while the real facts flip | the real block only |
| U7 predicate is not the decision | identical URL facts | another policy element: tier precedence, a withdrawn consent, a user boundary, an exception's scope | not a host fact |
| U8 operation scope | URL, facts for the listed rules | the effect's operation, so a rule's `for` covers it in one twin and not the other | presence of the rule in any list |
| U9 required but missing | a URL call under a `urls` rule | the facts present against absent from the state | router false |
| U10 allow-side near miss | an allow rule with a declared prefix | a URL one segment inside against one segment outside, both ordinary-looking | containment lists |

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

## Design questions

Resolved by the user's and trainer's decisions of 2026-11-22:

1. An unreadable URL: AgentArmor refuses the request. No fact.
2. A trailing host dot: refused, on the call side and in policies.
3. The decoding profile is declared by the tool profile, with case sensitivity and `;`.
4. Double encoding: refused when a second decoding is unreadable or changes a list.
5. Origin matching is exact. No subdomain relation.
6. Port 0 and empty path segments are refused.

Still open:

1. Operation vocabulary: `fetch`, `send`, `open` are a proposal; the tool profile contract
   should fix it.
2. The monitor profile v2 changes the `Host facts:` identity line in every state. When, and
   with which re-render of training states?
3. IDNA 2003 through the standard library with refusals, or a pinned UTS 46 table (a data
   dependency) that would read more hosts?
4. Should a case-insensitive declaration and the Windows-family segment rules be two separate
   declarations?
5. Should `rfc3986` also refuse when the fully decoded reading would change a list (the
   review's optional suggestion 1)? Today the profile declaration is trusted for `%2F`.

"""Host-computed quantity and time facts, profile armor-hostfacts-measures-v1.

CPU reference. A candidate profile, not a registered serving identity until the trainer registers
it. AgentArmor computes these facts deterministically and passes them to Kai as trusted
``host_computed`` environment facts; Kai is trained to defer to them. The design is Kai's
``docs/hostfacts-measures-design.md``; its Interface section binds this file, the hand-written
vectors and the native port.

This file is kept byte-identical in two repositories: Kai keeps it as ``kai/hostfacts/measures.py``
to build training data, and AgentArmor keeps it as ``src/hostfacts/measures.py``, the Python
reference for its native C++ port. Both copies and the native port must give the hand-written
results in the shared ``armor-hostfacts-measures-v1`` vectors, so an edit must update both copies,
the native port and the contract together. Standard library only (``datetime``, ``decimal``,
``re``) and no floats: every number is an integer or an exact ``decimal.Decimal``, and every
arithmetic step runs in a context that raises instead of rounding.

What the host computes for each typed measure the tool schema extracts from the call (never from
policy prose, documents or caller claims):

- money: ``amount / 10^minor_exponent``, converted to a limit's currency at a declared direct
  rate ``A/B`` (1 A = rate B) as the exact product. No inverse, chain or market lookup.
- quantity (data sizes B, KB, MB, GB, TB): the value in a limit's unit, dividing or multiplying
  by a power of 1024 (binary) or 1000 (decimal) as the limit declares.
- rate: an amount per period, converted like money, then scaled up by a fixed integer factor:
  month to quarter 3, month to year 12, quarter to year 4, day to week 7.
- interval: a start with an explicit offset plus a duration, moved to a trusted zone's fixed
  offset within the zone entry's validity, and compared with a daily local window that may cross
  midnight. There is no time-zone database.
- recurrence: the occurrence count of an RRULE subset (FREQ=DAILY, WEEKLY with BYDAY, or MONTHLY
  with BYMONTHDAY) bounded by UNTIL (inclusive) or COUNT.
- date: the age in calendar days from the date to the trusted ``today``, with the end date
  counted and the start date excluded.
- date_span: working days from start to end, both inclusive, excluding Saturday, Sunday and the
  holidays of the named trusted calendar.

Policy authoring stays plain (see ``compile_limits``). A rule adds one optional ``limit`` field,
for example ``"limit": {"measure": "amount", "max": "10000 USD"}``. Its description still says
what the rule means; the host never reads it. Mistakes are reported once, at load time, with the
rule id and a fix.

Facts. For each measure of the call that at least one limit constrains, in request order: one
``measure_<name>_value``, a string or, when the measure's limits use several comparison keys, a
list of strings (resolved ambiguity 1); then ``measure_<name>_within_bound_of`` and
``measure_<name>_beyond_bound_of``, each a list of rule ids in policy order. These are neutral
containment words, never a match, exception, consent or decision. Unknown, stale or unsupported
input raises :class:`Refusal` with a stable code; it never becomes a value.

Resolved ambiguities (the strictest reasonable reading of the design):

1. One value fact per measure. A comparison key is the currency (money), the unit and binary or
   decimal system (quantity), the currency and period (rate), the zone (interval) or the calendar
   (date_span); recurrence and date have one key. With one key, ``measure_<name>_value`` is that
   key's value string. With several, it is the list of their value strings, ordered by the first
   limit rule in policy order that uses each key, each key once, and the note states every key's
   inputs in the same order. envelope-v2 cannot carry two facts with one name, and evidence
   addresses a fact by its name.
2. Rate limits are money per period. The normalized rate bound is the money form plus ``per``,
   and the rate value format names a currency. So a rate limit in a data unit is ``bad_bound``,
   and a rate measured in a ``unit`` refuses as ``unit_mismatch``.
3. No declared period factors. The interface has no field for a policy-declared factor, so only
   the same period and the four fixed up-scaling factors are used. Everything else, including
   week to year and all down-scaling, refuses as ``unsupported_period``.
4. No load-time rate check. A money or rate limit loads whatever rates are configured, so a
   deployment that works in one currency declares no rate. A call that needs a conversion with no
   declared direct pair refuses at compute time as ``unknown_currency_pair``.
5. ``units`` is required on every quantity limit. Every quantity unit (B to TB) is a data size,
   so the requirement holds even for B and when no conversion happens.
6. Bound syntax.
   - ``max`` and ``min`` are strings: a non-negative decimal (no sign, separator or leading zero;
     at most 24 integer and 12 fraction digits), one space, then the unit. A recurrence count is
     an integer string alone.
   - A rule gives exactly one of ``max`` and ``min``.
   - ``max_age_days`` and ``max_working_days`` are JSON integers.
   - ``daily`` is 24-hour ``HH:MM-HH:MM`` with different start and end; midnight is ``00:00``.
   - ``zone`` and ``calendar`` are required for their kinds.
   - A key from another kind's form is ``bad_bound``; a key in no form is ``bad_limit_field``.
7. Required call fields. ``minor_exponent`` (0 means major units) and ``duration_minutes`` (0
   means an instant) are required, as the interface tables list them. With an exponent above 0,
   the amount must be a whole number of minor units.
8. Intervals.
   - A start falls on a whole minute, so the ``HH:MM`` value is exact.
   - ``-00:00`` (offset unknown) is refused in every timestamp and zone entry.
   - Durations are 0..1439 minutes, so the value's start, end and date fix the interval.
   - The local start date and the local end date must both lie within the zone entry's
     ``valid_from`` to ``valid_until``, inclusive.
9. Windows. An interval meets a window when it starts at or after the start of the window
   instance that contains its start and ends at or before that instance's end. For a window that
   crosses midnight, a start before the window's start time belongs to the instance that opened
   the previous evening.
10. Recurrence.
    - DTSTART is floating local time. UNTIL is a local ``YYYYMMDD`` (counting through that date)
      or ``YYYYMMDDTHHMMSS``, never ``Z``.
    - Parts are uppercase, in any order and without duplicates. Only FREQ, BYDAY, BYMONTHDAY,
      UNTIL and COUNT are allowed, with no ``RRULE:`` prefix.
    - WEEKLY requires BYDAY with plain day codes, and MONTHLY requires BYMONTHDAY with days 1..31.
    - DTSTART must itself be an occurrence: RFC 5545 counts a non-matching DTSTART, and
      implementations differ.
    - An UNTIL before DTSTART refuses. Month days a month lacks are skipped, per RFC 5545. COUNT
      is 1..999999.
11. Dates. A date after ``today`` refuses as ``bad_date`` (there are no negative ages), as does a
    span that ends before it starts. A calendar holiday on a weekend is not subtracted twice.
12. Value strings follow the format table literally, with no singular forms: ``"1 occurrences"``,
    ``"1 days old"`` and ``"1 working days"``.
13. Refusal codes for malformed input.
    - ``bad_request``: request structure, including keys, kinds, names, pointers, integer
      fields, and compiled limit rules that do not match the trusted configuration.
    - ``bad_amount``: amount, value, currency and size-unit text.
    - ``bad_instant``: timestamps and durations. ``bad_date``: dates.
    - ``unsupported_rrule``: RRULE text. ``unsupported_period``: an unknown period.
    - Every measure in the request is validated, constrained or not. A bad configuration given to
      ``compile_limits`` is ``bad_policy``.
    - A detail names a missing or unknown key as an ASCII JSON string literal, as ``json.dumps``
      writes it, so a detail always encodes, even for a key that holds a lone surrogate.
14. Check order: the request, the profile, the trusted configuration, each measure in order, the
    limit rules, then each constrained measure's values in rule order. A rate is checked for
    ``unit_mismatch``, ``unknown_currency_pair`` and ``stale_rate``, then its period. A rate whose
    ``as_of`` equals ``observed_at`` is fresh. No other staleness is checked, and ``today`` is not
    checked against ``observed_at``.
"""

import datetime
import decimal
import re

PROFILE = "armor-hostfacts-measures-v1"
PROVENANCE = "host_computed"
KINDS = ("money", "quantity", "rate", "interval", "recurrence", "date", "date_span")
PERIODS = ("day", "week", "month", "quarter", "year")
PERIOD_FACTORS = {
    ("month", "quarter"): 3,
    ("month", "year"): 12,
    ("quarter", "year"): 4,
    ("day", "week"): 7,
}
SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")
UNIT_BASES = {"binary": 1024, "decimal": 1000}
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
MAX_RULES = 256
MAX_LIMIT_RULES = 32
MAX_MEASURES = 16
MAX_CONFIGURED_MEASURES = 64
MAX_RATES = 256
MAX_ZONES = 64
MAX_CALENDARS = 64
MAX_HOLIDAYS = 1024
MAX_TEXT = 256
MAX_EXPONENT = 4
MAX_DURATION_MINUTES = 1439

REFUSAL_CODES = (
    "bad_request",  # request structure, a measure's fields, or compiled limit rules
    "bad_trusted",  # the trusted configuration is malformed
    "bad_amount",  # amount, value, currency or size-unit text that does not parse
    "unknown_currency_pair",  # no declared direct rate from the call's currency to the limit's
    "stale_rate",  # the rate's as_of is after observed_at
    "zone_not_valid",  # a local date of the interval is outside the zone entry's validity
    "bad_instant",  # a timestamp without an offset, off a whole minute, or a bad duration
    "unsupported_rrule",  # outside DAILY, WEEKLY+BYDAY, MONTHLY+BYMONTHDAY with UNTIL or COUNT
    "bad_date",  # a malformed date, a span ending before it starts, or a date after today
    "unit_mismatch",  # a rate measured in a unit, compared with a currency limit
    "unsupported_period",  # an unknown period, or a scaling without a fixed up-scaling factor
)

POLICY_ERRORS = (
    "bad_policy",  # not a list of rule objects with unique, valid ids; or a bad configuration
    "bad_limit_field",  # "limit" is not an object with a "measure" and known keys
    "unknown_measure",  # no configured tool schema produces the measure
    "bad_bound",  # an unparseable bound, or a bound form that does not suit the measure's kind
    "missing_units",  # a data-size limit without "units"
    "unknown_zone",  # the zone is not in the trusted zone table
    "unknown_calendar",  # the calendar is not in the trusted holiday calendars
    "too_many_limit_rules",  # more than MAX_LIMIT_RULES rules declare a limit
)

_RULE_ID = re.compile(r"[^\x00-\x1f\x7f\ud800-\udfff]{1,64}")
_POINTER = re.compile(r"(?:/(?:[^~/\ud800-\udfff]|~[01])*)+")
_NAME = re.compile(r"[a-z][a-z0-9_]{0,47}")
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?")
_INTEGER = re.compile(r"0|[1-9][0-9]{0,8}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_PAIR = re.compile(r"([A-Z]{3})/([A-Z]{3})")
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_WINDOW = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])-([01][0-9]|2[0-3]):([0-5][0-9])")
_OFFSET = re.compile(r"([+-])([01][0-9]|2[0-3]):([0-5][0-9])")
_STAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})"
    r"(?::([0-9]{2})(?:\.([0-9]{1,6}))?)?(Z|[+-][0-9]{2}:[0-9]{2})"
)
_NAIVE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?")
_LOCAL = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})")
_UNTIL = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})(?:T([0-9]{2})([0-9]{2})([0-9]{2}))?")
_COUNT = re.compile(r"[1-9][0-9]{0,5}")
_MONTHDAY = re.compile(r"[1-9]|[12][0-9]|3[01]")
_TRAPS = [
    decimal.Clamped,
    decimal.DivisionByZero,
    decimal.Inexact,
    decimal.InvalidOperation,
    decimal.Overflow,
    decimal.Rounded,
    decimal.Subnormal,
    decimal.Underflow,
]


class Refusal(ValueError):
    """The host cannot compute the facts; the request takes the host's explicit refusal path."""

    def __init__(self, code, detail):
        assert code in REFUSAL_CODES, code
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class PolicyError(ValueError):
    """A policy file mistake, reported at load time with the rule id and a fix."""

    def __init__(self, code, rule_id, message, hint):
        assert code in POLICY_ERRORS, code
        where = f"rule {rule_id!r}: " if rule_id is not None else ""
        super().__init__(f"{where}{message} ({hint})")
        self.code, self.rule_id, self.message, self.hint = code, rule_id, message, hint


def _require(ok, code, detail):
    if not ok:
        raise Refusal(code, detail)


def _show(value):
    text = repr(value)
    return text if len(text) <= 48 else text[:45] + "..."


_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _quoted(text):
    """``text`` as an ASCII JSON string literal, exactly as ``json.dumps`` writes it.

    A detail that names a key then always encodes as UTF-8, even when the key holds a lone
    surrogate.
    """
    out = []
    for ch in text:
        code = ord(ch)
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code > 0xFFFF:
            code -= 0x10000
            out.append(f"\\u{0xD800 | (code >> 10):04x}\\u{0xDC00 | (code & 0x3FF):04x}")
        else:
            out.append(f"\\u{code:04x}")
    return '"' + "".join(out) + '"'


def _keys(value, required, optional=(), code="bad_request", where="request"):
    _require(type(value) is dict, code, f"{where} must be an object")
    missing = [_quoted(k) for k in required if k not in value]
    _require(not missing, code, f"{where} is missing {', '.join(missing)}")
    extra = sorted(str(k) for k in value if k not in required and k not in optional)
    _require(not extra, code, f"{where} has unknown keys {', '.join(_quoted(k) for k in extra)}")


def _text(value, code, where):
    _require(
        type(value) is str and 0 < len(value) <= MAX_TEXT,
        code,
        f"{where} must be a nonempty string of at most {MAX_TEXT} characters",
    )
    _require(
        not any(ord(ch) < 0x20 or ord(ch) == 0x7F or 0xD800 <= ord(ch) <= 0xDFFF for ch in value),
        code,
        f"{where} has a control character or invalid Unicode",
    )
    return value


def _exact(operation, left, right):
    """One Decimal operation in a context that raises instead of rounding."""
    context = decimal.Context(
        prec=200,
        rounding=decimal.ROUND_HALF_EVEN,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=_TRAPS,
    )
    try:
        return getattr(context, operation)(left, right)
    except decimal.DecimalException:
        raise Refusal("bad_amount", f"{operation} of {left} and {right} is not exact") from None


def _dec(value):
    return format(value, "f")


def _hm(moment):
    return f"{moment.hour:02d}:{moment.minute:02d}"


def _local_text(moment):
    return f"{moment.date().isoformat()} {_hm(moment)}"


def _fold(text):
    return text.strip().lower().replace("-", "_").replace(" ", "_")


def _date(value, code, where):
    match = _DATE.fullmatch(value) if type(value) is str else None
    _require(match is not None, code, f"{where} must be a YYYY-MM-DD date, not {_show(value)}")
    try:
        return datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        raise Refusal(code, f"{where} {value} is not a calendar date") from None


def _offset(text):
    """Signed minutes of "+HH:MM" or "-HH:MM", or None; "-00:00" (offset unknown) is None."""
    match = _OFFSET.fullmatch(text) if type(text) is str else None
    if match is None or text == "-00:00":
        return None
    minutes = int(match.group(2)) * 60 + int(match.group(3))
    return -minutes if match.group(1) == "-" else minutes


def _stamp(value, code, where, *, call):
    """(UTC, local, offset minutes) of an ISO timestamp with an explicit offset.

    Trusted timestamps include seconds; a call's instant falls on a whole minute.
    """
    match = _STAMP.fullmatch(value) if type(value) is str else None
    if match is None:
        naive = type(value) is str and _NAIVE.fullmatch(value) is not None
        _require(not naive, code, f"{where} {value} has no UTC offset")
        raise Refusal(
            code,
            f"{where} must be an ISO timestamp with an explicit offset, such as "
            f"2026-11-12T14:30:00Z, not {_show(value)}",
        )
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    if call:
        _require(
            int(second or "0") == 0 and int(fraction or "0") == 0,
            code,
            f"{where} {value} must fall on a whole minute",
        )
    else:
        _require(second is not None, code, f"{where} {value} must include seconds")
    offset = 0 if zone == "Z" else _offset(zone)
    _require(offset is not None, code, f"{where} {value} has an invalid or unknown UTC offset")
    try:
        local = datetime.datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second or "0"),
            int((fraction or "").ljust(6, "0")),
        )
        return local - datetime.timedelta(minutes=offset), local, offset
    except (ValueError, OverflowError):
        raise Refusal(code, f"{where} {value} is not a valid timestamp") from None


class Trusted:
    """The operator's trusted configuration: never from the call, a document or the conversation.

    ``{"today", "observed_at", "rates", "zones", "calendars", "measures"}``, all required.
    """

    def __init__(self, value):
        code = "bad_trusted"
        _keys(
            value,
            ("today", "observed_at", "rates", "zones", "calendars", "measures"),
            code=code,
            where="trusted",
        )
        self.today = _date(value["today"], code, "trusted.today")
        self.observed_at = value["observed_at"]
        self.observed = _stamp(self.observed_at, code, "trusted.observed_at", call=False)[0]

        rates = value["rates"]
        _require(
            type(rates) is list and len(rates) <= MAX_RATES,
            code,
            f"trusted.rates must be a list of at most {MAX_RATES} rates",
        )
        self.rates = {}
        for k, rate in enumerate(rates, start=1):
            where = f"trusted.rates[{k}]"
            _keys(rate, ("pair", "rate", "source", "as_of"), code=code, where=where)
            pair = rate["pair"]
            match = _PAIR.fullmatch(pair) if type(pair) is str else None
            _require(
                match is not None and match.group(1) != match.group(2),
                code,
                f'{where}.pair must be "BASE/QUOTE" with two different ISO currency codes',
            )
            _require(pair not in self.rates, code, f"{where}: pair {pair} is declared twice")
            text = rate["rate"]
            _require(
                type(text) is str
                and _DECIMAL.fullmatch(text) is not None
                and decimal.Decimal(text) > 0,
                code,
                f'{where}.rate must be a positive decimal string such as "1.08"',
            )
            _text(rate["source"], code, f"{where}.source")
            as_of = _stamp(rate["as_of"], code, f"{where}.as_of", call=False)[0]
            self.rates[pair] = {
                "rate": decimal.Decimal(text),
                "text": text,
                "source": rate["source"],
                "as_of": rate["as_of"],
                "as_of_utc": as_of,
            }

        zones = value["zones"]
        _require(
            type(zones) is dict and len(zones) <= MAX_ZONES,
            code,
            f"trusted.zones must be an object with at most {MAX_ZONES} zones",
        )
        self.zones = {}
        for key, entry in zones.items():
            _require(
                type(key) is str and _KEY.fullmatch(key) is not None,
                code,
                f"trusted.zones key {_show(key)} is not a valid name",
            )
            where = f"trusted.zones[{key}]"
            _keys(entry, ("offset", "valid_from", "valid_until"), code=code, where=where)
            offset = _offset(entry["offset"])
            _require(
                offset is not None,
                code,
                f'{where}.offset must be a fixed UTC offset such as "-06:00"',
            )
            start = _date(entry["valid_from"], code, f"{where}.valid_from")
            end = _date(entry["valid_until"], code, f"{where}.valid_until")
            _require(start <= end, code, f"{where}.valid_from is after its valid_until")
            self.zones[key] = {"offset": offset, "text": entry["offset"], "from": start, "to": end}

        calendars = value["calendars"]
        _require(
            type(calendars) is dict and len(calendars) <= MAX_CALENDARS,
            code,
            f"trusted.calendars must be an object with at most {MAX_CALENDARS} calendars",
        )
        self.calendars = {}
        for key, days in calendars.items():
            _require(
                type(key) is str and _KEY.fullmatch(key) is not None,
                code,
                f"trusted.calendars key {_show(key)} is not a valid name",
            )
            where = f"trusted.calendars[{key}]"
            _require(
                type(days) is list and len(days) <= MAX_HOLIDAYS,
                code,
                f"{where} must be a list of at most {MAX_HOLIDAYS} holiday dates",
            )
            parsed = [_date(d, code, f"{where}[{i}]") for i, d in enumerate(days, start=1)]
            _require(len(set(parsed)) == len(parsed), code, f"{where} lists a holiday twice")
            self.calendars[key] = sorted(parsed)

        measures = value["measures"]
        _require(
            type(measures) is dict and len(measures) <= MAX_CONFIGURED_MEASURES,
            code,
            f"trusted.measures must be an object with at most {MAX_CONFIGURED_MEASURES} measures",
        )
        for name, kind in measures.items():
            _require(
                type(name) is str and _NAME.fullmatch(name) is not None,
                code,
                f"trusted.measures name {_show(name)} must be lowercase snake_case, 1..48 chars",
            )
            _require(
                type(kind) is str and kind in KINDS,
                code,
                f"trusted.measures[{name}] must be one of {', '.join(KINDS)}",
            )
        self.measures = dict(measures)


# ---------------------------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------------------------

_LIMIT_KEYS = (
    "measure",
    "max",
    "min",
    "units",
    "per",
    "daily",
    "zone",
    "max_age_days",
    "max_working_days",
    "calendar",
)
_FORMS = {
    "money": ("max", "min"),
    "quantity": ("max", "min", "units"),
    "rate": ("max", "min", "per"),
    "interval": ("daily", "zone"),
    "recurrence": ("max", "min"),
    "date": ("max_age_days",),
    "date_span": ("max_working_days", "calendar"),
}
_FORM_EXAMPLES = {
    "money": '"max": "10000 USD"',
    "quantity": '"max": "2 GB", "units": "binary"',
    "rate": '"max": "142000 USD", "per": "year"',
    "interval": '"daily": "09:00-17:00", "zone": "user"',
    "recurrence": '"max": "12"',
    "date": '"max_age_days": 90',
    "date_span": '"max_working_days": 10, "calendar": "company"',
}
_AMOUNT_EXAMPLES = {"money": "10000 USD", "quantity": "2 GB", "rate": "142000 USD"}
_KEY_GUESSES = {
    "maximum": "max",
    "max_value": "max",
    "at_most": "max",
    "minimum": "min",
    "min_value": "min",
    "at_least": "min",
    "unit": "units",
    "unit_system": "units",
    "period": "per",
    "per_period": "per",
    "window": "daily",
    "hours": "daily",
    "time_window": "daily",
    "timezone": "zone",
    "time_zone": "zone",
    "tz": "zone",
    "max_age": "max_age_days",
    "max_days": "max_age_days",
    "age_days": "max_age_days",
    "max_working": "max_working_days",
    "working_days": "max_working_days",
    "holidays": "calendar",
    "holiday_calendar": "calendar",
}
_PERIOD_GUESSES = {
    "daily": "day",
    "days": "day",
    "weekly": "week",
    "weeks": "week",
    "monthly": "month",
    "months": "month",
    "quarterly": "quarter",
    "quarters": "quarter",
    "yearly": "year",
    "years": "year",
    "annual": "year",
    "annually": "year",
    "annum": "year",
}


def _example(measure, kind):
    return f'{{"measure": "{measure}", {_FORM_EXAMPLES[kind]}}}'


def _choices(names):
    return ", ".join(f'"{n}"' for n in sorted(names)) if names else "none"


def _name_hint(name, known, what, table):
    """A fix for a measure, zone or calendar name that is not configured."""
    folded = _fold(name) if type(name) is str else None
    if folded in known:
        return f'did you mean "{folded}"?'
    if not known:
        return f"no {what}s are configured; add {name!r} to the AgentArmor {table}"
    return f"use one of {_choices(known)}, or add {name!r} to the AgentArmor {table}"


def _amount_hint(text, kind, example):
    """A fix for a "<number> <unit>" bound, such as "10000 USD" or "2 GB", that does not parse."""
    if type(text) is not str:
        return f'write it as a string, the number and unit together, as "{example}"'
    if kind == "rate" and (" per " in text or "/" in text):
        return f'write the amount alone, as "{example}", and the period in "per"'
    if text != text.strip() or "  " in text or "\t" in text:
        return f'use exactly one space between the number and the unit, as "{example}"'
    if any(ch in text for ch in ",_'"):
        return f'write the number without separators, as "{example}"'
    if text[:1] in ("$", "€", "£", "¥", "-", "+"):
        return f'write a non-negative number, then the unit code, as "{example}"'
    parts = text.split(" ")
    if len(parts) == 1:
        glued = re.fullmatch(r"([0-9.]+)([A-Za-z]+)", text)
        if glued is not None:
            return f'put one space between the number and the unit: "{glued[1]} {glued[2]}"'
        return f'add the unit after the number, as "{example}"'
    if len(parts) != 2:
        return f'write only the number and the unit, as "{example}"'
    number, unit = parts
    if _DECIMAL.fullmatch(number) is None:
        if _DECIMAL.fullmatch(unit) is not None:
            return f'write the number first: "{unit} {number}"'
        return 'write a plain decimal number such as "10000" or "10000.50", without leading zeros'
    if kind == "quantity":
        if unit.upper() in SIZE_UNITS:
            return f'write the unit in capitals: "{number} {unit.upper()}"'
        if unit in ("KiB", "MiB", "GiB", "TiB"):
            return f'write "{number} {unit[0]}B" and declare "units": "binary"'
        return "use one of the units B, KB, MB, GB and TB"
    if kind == "rate" and unit.upper() in SIZE_UNITS:
        return f'a rate limit is money per period; write it in a currency, as "{example}"'
    if _CURRENCY.fullmatch(unit.upper()) is not None:
        return f'write the ISO currency code in capitals: "{number} {unit.upper()}"'
    return f'use a three-letter ISO currency code, as "{example}"'


def _count_hint(text):
    if type(text) is int and type(text) is not bool:
        return f'write the count as a string: "{text}"'
    if type(text) is str:
        spelled = re.fullmatch(r"\s*([0-9]+)\s*(?:occurrences?|times)?\s*", text)
        if spelled is not None and _INTEGER.fullmatch(spelled[1]) is not None:
            return f'write the count alone: "{spelled[1]}"'
    return 'write a whole number of occurrences as a string, such as "12"'


def _days_hint(value, key):
    if type(value) is str:
        spelled = re.fullmatch(r"\s*([0-9]+)\s*(?:(?:working\s+)?days?)?\s*", value)
        if spelled is not None and _INTEGER.fullmatch(spelled[1]) is not None:
            return f'write the number without quotes: "{key}": {spelled[1]}'
    return f'write a whole number of days, without quotes, such as "{key}": 90'


def _window_hint(text):
    if type(text) is not str:
        return 'write the local window as a string, such as "09:00-17:00"'
    compact = text.replace(" ", "")
    if compact != text and _WINDOW.fullmatch(compact) is not None:
        return f'write it without spaces: "{compact}"'
    padded = re.sub(r"\b([0-9]):", r"0\1:", compact)
    if _WINDOW.fullmatch(padded) is not None:
        return f'write two-digit hours: "{padded}"'
    if "24:00" in text:
        return 'write midnight as "00:00", as in "18:00-00:00"'
    if "am" in text.lower() or "pm" in text.lower():
        return 'use 24-hour time, such as "09:00-17:00"'
    return 'write a local window as "HH:MM-HH:MM", such as "09:00-17:00" or "22:00-02:00"'


def _compile_limit(rid, limit, trusted):
    def fail(code, message, hint):
        raise PolicyError(code, rid, message, hint)

    if type(limit) is not dict:
        fail(
            "bad_limit_field",
            '"limit" must be an object',
            'for example {"measure": "amount", "max": "10000 USD"}',
        )
    for key in limit:
        if key not in _LIMIT_KEYS:
            folded = _fold(key) if type(key) is str else None
            guess = folded if folded in _LIMIT_KEYS else _KEY_GUESSES.get(folded)
            fail(
                "bad_limit_field",
                f"unknown key {_show(key)} in limit",
                f'did you mean "{guess}"?' if guess else "the keys are " + ", ".join(_LIMIT_KEYS),
            )
    measure = limit.get("measure")
    if type(measure) is not str or measure == "":
        fail(
            "bad_limit_field",
            '"limit" needs "measure", the name of a measured call argument',
            f"the configured measures are {_choices(trusted.measures)}",
        )
    kind = trusted.measures.get(measure)
    if kind is None:
        fail(
            "unknown_measure",
            f"no configured tool schema produces the measure {measure!r}",
            _name_hint(measure, trusted.measures, "measure", "tool schemas"),
        )
    example = _example(measure, kind)
    for key in limit:
        if key != "measure" and key not in _FORMS[kind]:
            fail(
                "bad_bound",
                f'"{key}" does not suit {measure}, a {kind} measure',
                f"write {example}",
            )

    if kind == "interval":
        if "daily" not in limit or "zone" not in limit:
            fail("bad_bound", 'an interval limit needs "daily" and "zone"', f"write {example}")
        window = _WINDOW.fullmatch(limit["daily"]) if type(limit["daily"]) is str else None
        if window is None:
            fail("bad_bound", f"daily window {_show(limit['daily'])}", _window_hint(limit["daily"]))
        start, end = limit["daily"].split("-")
        if start == end:
            fail(
                "bad_bound",
                f"daily window {limit['daily']} starts and ends at the same time",
                'give different times; a window across midnight is written "22:00-02:00"',
            )
        zone = limit["zone"]
        if type(zone) is not str or zone == "":
            fail("bad_bound", '"zone" must name a zone', f"write {example}")
        if zone not in trusted.zones:
            fail(
                "unknown_zone",
                f"zone {zone!r} is not configured",
                _name_hint(zone, trusted.zones, "zone", "zone table"),
            )
        return {"op": "daily", "from": start, "to": end, "zone": zone}

    if kind in ("date", "date_span"):
        key = "max_age_days" if kind == "date" else "max_working_days"
        if key not in limit:
            fail("bad_bound", f'a {kind} limit needs "{key}"', f"write {example}")
        days = limit[key]
        if type(days) is not int or type(days) is bool or not 0 <= days <= 999999999:
            fail("bad_bound", f'"{key}" must be a whole number of days', _days_hint(days, key))
        if kind == "date":
            return {"op": key, "value": str(days)}
        calendar = limit.get("calendar")
        if type(calendar) is not str or calendar == "":
            fail("bad_bound", 'a date_span limit needs "calendar"', f"write {example}")
        if calendar not in trusted.calendars:
            fail(
                "unknown_calendar",
                f"calendar {calendar!r} is not configured",
                _name_hint(calendar, trusted.calendars, "calendar", "holiday calendars"),
            )
        return {"op": key, "value": str(days), "calendar": calendar}

    ops = [k for k in ("max", "min") if k in limit]
    if len(ops) != 1:
        fail(
            "bad_bound",
            "the limit has both max and min" if ops else "the limit has no max or min",
            "write two rules, one with max and one with min" if ops else f"write {example}",
        )
    op = ops[0]
    text = limit[op]
    if kind == "recurrence":
        if type(text) is not str or _INTEGER.fullmatch(text) is None:
            fail("bad_bound", f"{op} {_show(text)} is not a count", _count_hint(text))
        return {"op": op, "value": text, "unit": "occurrences"}

    parts = text.split(" ") if type(text) is str else []
    parsed = (
        len(parts) == 2
        and _DECIMAL.fullmatch(parts[0]) is not None
        and (
            parts[1] in SIZE_UNITS
            if kind == "quantity"
            else _CURRENCY.fullmatch(parts[1]) is not None
        )
    )
    if not parsed:
        fail(
            "bad_bound",
            f"{op} {_show(text)} is not a {kind} bound",
            _amount_hint(text, kind, _AMOUNT_EXAMPLES[kind]),
        )
    unit = parts[1]
    bound = {"op": op, "value": parts[0], "unit": unit}
    if kind == "quantity":
        if "units" not in limit:
            fail(
                "missing_units",
                f"data-size limit {text!r} does not say binary or decimal",
                'add "units": "binary" (1 GB = 1024 MB) or "units": "decimal" (1 GB = 1000 MB)',
            )
        if type(limit["units"]) is not str or limit["units"] not in UNIT_BASES:
            fail(
                "bad_bound",
                f'"units" {_show(limit["units"])} is not binary or decimal',
                'write "units": "binary" (1 GB = 1024 MB) or "units": "decimal" (1 GB = 1000 MB)',
            )
        bound["units"] = limit["units"]
        return bound
    if kind == "rate":
        if "per" not in limit:
            fail(
                "bad_bound",
                'a rate limit needs "per", the period of its amount',
                'add "per": "day", "week", "month", "quarter" or "year"',
            )
        per = limit["per"]
        if per not in PERIODS:
            guess = _PERIOD_GUESSES.get(_fold(per)) if type(per) is str else None
            fail(
                "bad_bound",
                f'"per" {_show(per)} is not a period',
                f'did you mean "per": "{guess}"?'
                if guess
                else 'write "per": "day", "week", "month", "quarter" or "year"',
            )
        bound["per"] = per
    return bound


def compile_limits(rules, *, config):
    """Limit rules for ``compute`` from AgentArmor policy rules (the parsed JSONL objects).

    Reads only each rule's ``id`` and its optional ``limit`` field, never the description, and
    returns ``[{"rule_id", "measure", "kind", "bound"}]`` in policy order with normalized bounds.
    ``config`` is the operator's trusted configuration (the request's ``trusted``); its
    ``measures`` name the measures the tool schemas produce. Raises :class:`PolicyError`.

    Limit forms by kind (bounds are inclusive):

    - money: ``{"measure", "max" | "min": "10000 USD"}``
    - quantity: ``{"measure", "max" | "min": "2 GB", "units": "binary" | "decimal"}``
    - rate: ``{"measure", "max" | "min": "142000 USD", "per": "year"}``
    - interval: ``{"measure", "daily": "09:00-17:00", "zone": "user"}``
    - recurrence: ``{"measure", "max" | "min": "12"}``
    - date: ``{"measure", "max_age_days": 90}``
    - date_span: ``{"measure", "max_working_days": 10, "calendar": "company"}``
    """
    try:
        trusted = Trusted(config)
    except Refusal as exc:
        raise PolicyError(
            "bad_policy",
            None,
            f"the trusted configuration is invalid: {exc.detail}",
            "fix the AgentArmor configuration (rates, zones, calendars and measures)",
        ) from None
    if type(rules) is not list or len(rules) > MAX_RULES:
        raise PolicyError(
            "bad_policy",
            None,
            f"the policy must be a list of at most {MAX_RULES} rules",
            "write one rule object per line",
        )
    seen, out = set(), []
    for rule in rules:
        if type(rule) is not dict:
            raise PolicyError(
                "bad_policy", None, "each policy line must be an object", 'write {"id": ...}'
            )
        rid = rule.get("id")
        if type(rid) is not str or _RULE_ID.fullmatch(rid) is None or rid in seen:
            raise PolicyError(
                "bad_policy",
                rid if type(rid) is str else None,
                "rule ids must be unique, 1..64 characters without control characters",
                "rename the rule",
            )
        seen.add(rid)
        if "limit" not in rule:
            continue
        limit = rule["limit"]
        bound = _compile_limit(rid, limit, trusted)
        measure = limit["measure"]
        out.append(
            {"rule_id": rid, "measure": measure, "kind": trusted.measures[measure], "bound": bound}
        )
    if len(out) > MAX_LIMIT_RULES:
        raise PolicyError(
            "too_many_limit_rules",
            None,
            f"{len(out)} rules declare a limit; at most {MAX_LIMIT_RULES} are supported",
            "merge rules that share a measure and bound, or move the rest to descriptions",
        )
    return out


# ---------------------------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------------------------

_MEASURE_FIELDS = {
    "money": ("amount", "currency", "minor_exponent"),
    "quantity": ("value", "unit"),
    "rate": ("amount", "per"),
    "interval": ("start", "duration_minutes"),
    "recurrence": ("dtstart", "rrule"),
    "date": ("date",),
    "date_span": ("start", "end"),
}
_BOUND_KEYS = {
    "money": ("op", "value", "unit"),
    "quantity": ("op", "value", "unit", "units"),
    "rate": ("op", "value", "unit", "per"),
    "interval": ("op", "from", "to", "zone"),
    "recurrence": ("op", "value", "unit"),
    "date": ("op", "value"),
    "date_span": ("op", "value", "calendar"),
}


def _amount(value, where):
    _require(
        type(value) is str and _DECIMAL.fullmatch(value) is not None,
        "bad_amount",
        f'{where} must be a plain non-negative decimal string such as "9400.00", '
        f"not {_show(value)}",
    )
    return decimal.Decimal(value)


def _currency(value, where):
    _require(
        type(value) is str and _CURRENCY.fullmatch(value) is not None,
        "bad_amount",
        f"{where} must be an ISO 4217 code such as USD, not {_show(value)}",
    )
    return value


def _size_unit(value, where):
    _require(
        type(value) is str and value in SIZE_UNITS,
        "bad_amount",
        f"{where} must be one of B, KB, MB, GB and TB, not {_show(value)}",
    )
    return value


def _occurs(rule, day):
    if rule["freq"] == "WEEKLY":
        return day.weekday() in rule["days"]
    if rule["freq"] == "MONTHLY":
        return day.day in rule["monthdays"]
    return True


def _recurrence(dtstart, rrule, where):
    """The parsed RRULE subset with its DTSTART, or a Refusal."""
    match = _LOCAL.fullmatch(dtstart) if type(dtstart) is str else None
    _require(
        match is not None,
        "bad_instant",
        f"{where}.dtstart must be a local YYYY-MM-DDTHH:MM, not {_show(dtstart)}",
    )
    try:
        start = datetime.datetime(*(int(g) for g in match.groups()))
    except ValueError:
        raise Refusal("bad_instant", f"{where}.dtstart {dtstart} is not a valid time") from None

    def refuse(detail):
        raise Refusal("unsupported_rrule", f"{where}.rrule {_show(rrule)}: {detail}")

    if type(rrule) is not str or not 0 < len(rrule) <= MAX_TEXT:
        refuse(f"must be a nonempty string of at most {MAX_TEXT} characters")
    parts = {}
    for part in rrule.split(";"):
        key, sep, value = part.partition("=")
        if not sep or not value or key not in ("FREQ", "BYDAY", "BYMONTHDAY", "UNTIL", "COUNT"):
            refuse(f"{part!r} is not one of FREQ, BYDAY, BYMONTHDAY, UNTIL and COUNT")
        if key in parts:
            refuse(f"{key} appears twice")
        parts[key] = value
    freq = parts.get("FREQ")
    if freq not in ("DAILY", "WEEKLY", "MONTHLY"):
        refuse("FREQ must be DAILY, WEEKLY or MONTHLY")
    if ("UNTIL" in parts) == ("COUNT" in parts):
        refuse("exactly one of UNTIL and COUNT is required")
    rule = {"freq": freq, "days": (), "monthdays": (), "start": start, "count": None}
    if freq == "WEEKLY":
        codes = parts.get("BYDAY", "").split(",")
        if (
            "BYDAY" not in parts
            or "BYMONTHDAY" in parts
            or any(c not in WEEKDAYS for c in codes)
            or len(set(codes)) != len(codes)
        ):
            refuse("WEEKLY needs BYDAY with distinct day codes MO..SU, and no BYMONTHDAY")
        rule["days"] = tuple(sorted(WEEKDAYS.index(c) for c in codes))
    elif freq == "MONTHLY":
        values = parts.get("BYMONTHDAY", "").split(",")
        if (
            "BYMONTHDAY" not in parts
            or "BYDAY" in parts
            or any(_MONTHDAY.fullmatch(v) is None for v in values)
            or len(set(values)) != len(values)
        ):
            refuse("MONTHLY needs BYMONTHDAY with distinct days 1..31, and no BYDAY")
        rule["monthdays"] = tuple(sorted(int(v) for v in values))
    elif "BYDAY" in parts or "BYMONTHDAY" in parts:
        refuse("DAILY takes no BYDAY or BYMONTHDAY")
    if not _occurs(rule, start.date()):
        refuse(f"DTSTART {dtstart} is not itself an occurrence of the rule")
    if "COUNT" in parts:
        if _COUNT.fullmatch(parts["COUNT"]) is None:
            refuse("COUNT must be a whole number 1..999999")
        rule["count"] = int(parts["COUNT"])
        return rule
    until = _UNTIL.fullmatch(parts["UNTIL"])
    if until is None:
        refuse("UNTIL must be a local YYYYMMDD or YYYYMMDDTHHMMSS without Z, like DTSTART")
    numbers = [int(g) for g in until.groups() if g is not None]
    try:
        moment = datetime.datetime(*numbers) if len(numbers) == 6 else datetime.date(*numbers)
    except ValueError:
        moment = None
    if moment is None:
        refuse("UNTIL is not a valid date or time")
    if len(numbers) == 3:
        if moment < start.date():
            refuse("UNTIL is before DTSTART")
        last = moment
    else:
        if moment < start:
            refuse("UNTIL is before DTSTART")
        last = moment.date()
        if moment.time() < start.time():
            last -= datetime.timedelta(days=1)
    rule["until"], rule["last"] = parts["UNTIL"], last
    return rule


def _measure(m, k, trusted):
    """One typed measure of the call, validated and parsed."""
    where = f"measures[{k}]"
    _require(
        type(m) is dict and m.get("kind") in KINDS,
        "bad_request",
        f"{where}.kind must be one of {', '.join(KINDS)}",
    )
    kind = m["kind"]
    _keys(
        m,
        ("name", "kind", "argument") + _MEASURE_FIELDS[kind],
        ("currency", "unit") if kind == "rate" else (),
        where=where,
    )
    name = m["name"]
    _require(
        type(name) is str and trusted.measures.get(name) == kind,
        "bad_request",
        f"{where}.name {_show(name)} is not a configured {kind} measure",
    )
    _text(m["argument"], "bad_request", f"{where}.argument")
    _require(
        _POINTER.fullmatch(m["argument"]) is not None,
        "bad_request",
        f"{where}.argument must be a JSON pointer such as /call/arguments/amount",
    )
    out = {"name": name, "kind": kind, "argument": m["argument"], "where": where}
    if kind == "money":
        exponent = m["minor_exponent"]
        _require(
            type(exponent) is int and type(exponent) is not bool and 0 <= exponent <= MAX_EXPONENT,
            "bad_request",
            f"{where}.minor_exponent must be an integer 0..{MAX_EXPONENT}",
        )
        amount = _amount(m["amount"], f"{where}.amount")
        _require(
            exponent == 0 or "." not in m["amount"],
            "bad_amount",
            f"{where}.amount {m['amount']} must be a whole number of minor units (exponent "
            f"{exponent})",
        )
        out["currency"] = _currency(m["currency"], f"{where}.currency")
        out["text"], out["exponent"] = m["amount"], exponent
        out["amount"] = _exact("scaleb", amount, -exponent)
    elif kind == "quantity":
        out["text"] = m["value"]
        out["value"] = _amount(m["value"], f"{where}.value")
        out["unit"] = _size_unit(m["unit"], f"{where}.unit")
    elif kind == "rate":
        _require(
            ("currency" in m) != ("unit" in m),
            "bad_request",
            f"{where} needs exactly one of currency and unit",
        )
        per = m["per"]
        _require(type(per) is str, "bad_request", f"{where}.per must be a string")
        _require(
            per in PERIODS,
            "unsupported_period",
            f"{where}.per {per!r} is not day, week, month, quarter or year",
        )
        out["text"], out["per"] = m["amount"], per
        out["amount"] = _amount(m["amount"], f"{where}.amount")
        if "currency" in m:
            out["currency"], out["unit"] = _currency(m["currency"], f"{where}.currency"), None
        else:
            out["currency"], out["unit"] = None, _size_unit(m["unit"], f"{where}.unit")
    elif kind == "interval":
        minutes = m["duration_minutes"]
        _require(
            type(minutes) is int and type(minutes) is not bool,
            "bad_request",
            f"{where}.duration_minutes must be an integer",
        )
        out["text"] = m["start"]
        out["utc"] = _stamp(m["start"], "bad_instant", f"{where}.start", call=True)[0]
        _require(
            0 <= minutes <= MAX_DURATION_MINUTES,
            "bad_instant",
            f"{where}.duration_minutes {minutes} is outside 0..{MAX_DURATION_MINUTES}",
        )
        out["minutes"] = minutes
    elif kind == "recurrence":
        out["dtstart"], out["rrule"] = m["dtstart"], m["rrule"]
        out["rule"] = _recurrence(m["dtstart"], m["rrule"], where)
    elif kind == "date":
        out["date"] = _date(m["date"], "bad_date", f"{where}.date")
    else:
        out["start"] = _date(m["start"], "bad_date", f"{where}.start")
        out["end"] = _date(m["end"], "bad_date", f"{where}.end")
        _require(
            out["start"] <= out["end"],
            "bad_date",
            f"{where} ends on {out['end']}, before its start {out['start']}",
        )
    return out


def _measures(value, trusted):
    _require(
        type(value) is list and len(value) <= MAX_MEASURES,
        "bad_request",
        f"measures must list 0..{MAX_MEASURES} typed measures from the tool schema",
    )
    out, names = [], set()
    for k, m in enumerate(value, start=1):
        parsed = _measure(m, k, trusted)
        _require(
            parsed["name"] not in names,
            "bad_request",
            f"measures[{k}]: measure {parsed['name']} appears twice",
        )
        names.add(parsed["name"])
        out.append(parsed)
    return out


def _bound_ok(kind, bound, trusted):
    """Whether ``bound`` is exactly a normalized ``compile_limits`` bound for ``kind``."""
    if (
        type(bound) is not dict
        or set(bound) != set(_BOUND_KEYS[kind])
        or any(type(v) is not str for v in bound.values())
    ):
        return False
    if kind == "interval":
        return (
            bound["op"] == "daily"
            and _WINDOW.fullmatch(f"{bound['from']}-{bound['to']}") is not None
            and bound["from"] != bound["to"]
            and bound["zone"] in trusted.zones
        )
    if kind == "date":
        return bound["op"] == "max_age_days" and _INTEGER.fullmatch(bound["value"]) is not None
    if kind == "date_span":
        return (
            bound["op"] == "max_working_days"
            and _INTEGER.fullmatch(bound["value"]) is not None
            and bound["calendar"] in trusted.calendars
        )
    if bound["op"] not in ("max", "min"):
        return False
    if kind == "recurrence":
        return _INTEGER.fullmatch(bound["value"]) is not None and bound["unit"] == "occurrences"
    if _DECIMAL.fullmatch(bound["value"]) is None:
        return False
    if kind == "quantity":
        return bound["unit"] in SIZE_UNITS and bound["units"] in UNIT_BASES
    if kind == "rate" and bound["per"] not in PERIODS:
        return False
    return _CURRENCY.fullmatch(bound["unit"]) is not None


def _limit_rules(value, trusted):
    _require(
        type(value) is list and len(value) <= MAX_LIMIT_RULES,
        "bad_request",
        f"limit_rules must list at most {MAX_LIMIT_RULES} rules (compile_limits output)",
    )
    ids = set()
    for j, rule in enumerate(value, start=1):
        where = f"limit_rules[{j}]"
        _keys(rule, ("rule_id", "measure", "kind", "bound"), where=where)
        rid = rule["rule_id"]
        _require(
            type(rid) is str and _RULE_ID.fullmatch(rid) is not None and rid not in ids,
            "bad_request",
            f"{where}.rule_id must be unique, 1..64 characters without control characters",
        )
        ids.add(rid)
        _require(
            type(rule["measure"]) is str
            and rule["kind"] in KINDS
            and trusted.measures.get(rule["measure"]) == rule["kind"],
            "bad_request",
            f"{where}: measure and kind must match the trusted configuration",
        )
        _require(
            _bound_ok(rule["kind"], rule["bound"], trusted),
            "bad_request",
            f"{where}.bound is not a normalized {rule['kind']} bound for the trusted "
            "configuration (use compile_limits)",
        )
    return value


# ---------------------------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------------------------


def _convert(amount, currency, unit, trusted):
    """``amount`` in ``unit`` at a declared direct rate, and the note's account of it."""
    if currency == unit:
        return amount, f"already in {unit}, no conversion"
    pair = f"{currency}/{unit}"
    rate = trusted.rates.get(pair)
    _require(
        rate is not None,
        "unknown_currency_pair",
        f"no declared direct rate {pair}; inverse pairs and chains are never used",
    )
    _require(
        rate["as_of_utc"] <= trusted.observed,
        "stale_rate",
        f"rate {pair} as of {rate['as_of']} is after observed_at {trusted.observed_at}",
    )
    value = _exact("multiply", amount, rate["rate"])
    return value, (
        f"converted at the trusted direct rate {pair} = {rate['text']} (1 {currency} = "
        f"{rate['text']} {unit}; source: {rate['source']}; as of {rate['as_of']}), exact product "
        f"{_dec(value)} {unit}, not rounded"
    )


def _month_length(year, month):
    if month == 12:
        return 31
    return (datetime.date(year, month + 1, 1) - datetime.date(year, month, 1)).days


def _count(rule):
    """The number of occurrences and the date of the last one (None for COUNT)."""
    if rule["count"] is not None:
        return rule["count"], None
    first, last = rule["start"].date(), rule["last"]
    total = (last - first).days + 1
    if rule["freq"] == "DAILY":
        count = total
    elif rule["freq"] == "WEEKLY":
        weeks, rest = divmod(total, 7)
        count = weeks * len(rule["days"]) + sum(
            1 for i in range(rest) if (first.weekday() + i) % 7 in rule["days"]
        )
    else:
        per_length = {n: sum(1 for d in rule["monthdays"] if d <= n) for n in (28, 29, 30, 31)}
        count, year, month = 0, first.year, first.month
        while (year, month) <= (last.year, last.month):
            length = _month_length(year, month)
            if (year, month) in ((first.year, first.month), (last.year, last.month)):
                count += sum(
                    1
                    for d in rule["monthdays"]
                    if d <= length and first <= datetime.date(year, month, d) <= last
                )
            else:
                count += per_length[length]
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    day = last
    while not _occurs(rule, day):
        day -= datetime.timedelta(days=1)
    return count, day


def _working_days(start, end, holidays):
    total = (end - start).days + 1
    weeks, rest = divmod(total, 7)
    weekdays = weeks * 5 + sum(1 for i in range(rest) if (start.weekday() + i) % 7 < 5)
    inside = [d for d in holidays if start <= d <= end]
    counted = [d for d in inside if d.weekday() < 5]
    on_weekends = [d for d in inside if d.weekday() >= 5]
    return weekdays - len(counted), total, total - weekdays, counted, on_weekends


def _dates(days):
    return ", ".join(d.isoformat() for d in days) if days else "none"


def _value(m, bound, trusted):
    """(comparable, value string, note body) of measure ``m`` under one comparison key.

    The body states the inputs of this one value; ``_measure_facts`` adds the head that names the
    call argument.
    """
    kind = m["kind"]
    if kind == "money":
        if m["exponent"] == 0:
            source = (
                f'amount "{m["text"]}" {m["currency"]} with the declared minor-unit exponent 0 '
                "(major units)"
            )
        else:
            source = (
                f'amount "{m["text"]}" with the declared minor-unit exponent {m["exponent"]} is '
                f"{_dec(m['amount'])} {m['currency']} ({m['text']} / 10^{m['exponent']})"
            )
        value, step = _convert(m["amount"], m["currency"], bound["unit"], trusted)
        text = f"{_dec(value)} {bound['unit']}"
        return value, text, f"{source}; {step}."

    if kind == "quantity":
        units, unit = bound["units"], bound["unit"]
        base = UNIT_BASES[units]
        steps = SIZE_UNITS.index(m["unit"]) - SIZE_UNITS.index(unit)
        factor = decimal.Decimal(base ** abs(steps))
        system = f"{units} units (1 KB = {base} B)"
        if steps > 0:
            value = _exact("multiply", m["value"], factor)
            step = f"multiplied by {base}^{steps} = {factor}"
        elif steps < 0:
            value = _exact("divide", m["value"], factor)
            step = f"divided by {base}^{-steps} = {factor}"
        else:
            value, step = m["value"], "factor 1"
        note = (
            f'"{m["text"]}" {m["unit"]} in {unit} with {system}: {step}, exactly '
            f"{_dec(value)} {unit}."
        )
        return value, f"{_dec(value)} {unit}", note

    if kind == "rate":
        unit, per = bound["unit"], bound["per"]
        _require(
            m["currency"] is not None,
            "unit_mismatch",
            f"{m['where']} is measured in {m['unit']}, but the limit is in the currency {unit}",
        )
        value, step = _convert(m["amount"], m["currency"], unit, trusted)
        if m["per"] != per:
            factor = PERIOD_FACTORS.get((m["per"], per))
            _require(
                factor is not None,
                "unsupported_period",
                f"no fixed factor scales per {m['per']} to per {per}; only month to quarter (3), "
                "month to year (12), quarter to year (4) and day to week (7) are used",
            )
            value = _exact("multiply", value, decimal.Decimal(factor))
            scale = f"scaled from per {m['per']} to per {per} by the fixed factor {factor}"
        else:
            scale = f"period unchanged (per {per})"
        text = f"{_dec(value)} {unit} per {per}"
        note = (
            f'amount "{m["text"]}" {m["currency"]} per {m["per"]}; {step}; {scale}; exactly {text}.'
        )
        return value, text, note

    if kind == "interval":
        key = bound["zone"]
        zone = trusted.zones[key]
        try:
            start = m["utc"] + datetime.timedelta(minutes=zone["offset"])
            end = start + datetime.timedelta(minutes=m["minutes"])
        except OverflowError:
            raise Refusal("bad_instant", f"{m['where']} is outside the supported dates") from None
        _require(
            zone["from"] <= start.date() and end.date() <= zone["to"],
            "zone_not_valid",
            f"{m['where']}: local {_local_text(start)} to {_local_text(end)} at "
            f"UTC{zone['text']} is outside zone {key}'s validity {zone['from'].isoformat()} to "
            f"{zone['to'].isoformat()}",
        )
        text = f"{_hm(start)}-{_hm(end)} on {start.date().isoformat()} at UTC{zone['text']} ({key})"
        note = (
            f"start {m['text']} plus {m['minutes']} minutes, at zone {key}'s trusted fixed "
            f"offset UTC{zone['text']} (entry valid {zone['from'].isoformat()} to "
            f"{zone['to'].isoformat()} inclusive; no time-zone database), is local "
            f"{_local_text(start)} to {_local_text(end)}."
        )
        return (start, end), text, note

    if kind == "recurrence":
        rule = m["rule"]
        count, last = _count(rule)
        if last is None:
            extent = f"has {count} occurrences, the number COUNT sets; the first is DTSTART"
        else:
            final = datetime.datetime.combine(last, rule["start"].time())
            extent = (
                f"has {count} occurrences from DTSTART through UNTIL {rule['until']} inclusive; "
                f"the last is {_local_text(final)}"
            )
        skip = ""
        if any(d > 28 for d in rule["monthdays"]):
            skip = " Month days a month lacks are skipped, as RFC 5545 requires."
        note = f"RRULE {m['rrule']} from DTSTART {m['dtstart']} (local time) {extent}.{skip}"
        return count, f"{count} occurrences", note

    if kind == "date":
        age = (trusted.today - m["date"]).days
        _require(
            age >= 0,
            "bad_date",
            f"{m['where']}.date {m['date'].isoformat()} is after today {trusted.today.isoformat()}",
        )
        note = (
            f"{m['date'].isoformat()} to the trusted today {trusted.today.isoformat()} is "
            f"{age} calendar days, the end date counted and the start date excluded."
        )
        return age, f"{age} days old", note

    key = bound["calendar"]
    days, total, weekend, counted, on_weekends = _working_days(
        m["start"], m["end"], trusted.calendars[key]
    )
    note = (
        f"{m['start'].isoformat()} to {m['end'].isoformat()}, both inclusive, is {total} "
        f"calendar days; less {weekend} Saturdays and Sundays and {len(counted)} weekday "
        f"holidays of the trusted calendar {key} ({_dates(counted)}), {days} working days."
    )
    if on_weekends:
        note += f" Holidays on a weekend, not subtracted twice: {_dates(on_weekends)}."
    return days, f"{days} working days", note


def _comparison_key(kind, bound):
    if kind == "money":
        return bound["unit"]
    if kind == "quantity":
        return (bound["unit"], bound["units"])
    if kind == "rate":
        return (bound["unit"], bound["per"])
    if kind == "interval":
        return bound["zone"]
    if kind == "date_span":
        return bound["calendar"]
    return kind


def _in_window(start, end, bound):
    """Whether local [start, end] lies in the daily window instance that contains its start."""
    opens = int(bound["from"][:2]) * 60 + int(bound["from"][3:])
    closes = int(bound["to"][:2]) * 60 + int(bound["to"][3:])
    midnight = datetime.datetime(start.year, start.month, start.day)
    minute = datetime.timedelta(minutes=1)
    if opens < closes:
        first, last = midnight + opens * minute, midnight + closes * minute
    elif start.hour * 60 + start.minute >= opens:
        first, last = midnight + opens * minute, midnight + (1440 + closes) * minute
    else:
        first, last = midnight + (opens - 1440) * minute, midnight + closes * minute
    return first <= start and end <= last


def _meets(kind, value, bound):
    if kind == "interval":
        try:
            return _in_window(value[0], value[1], bound)
        except OverflowError:
            raise Refusal("bad_instant", "the interval is outside the supported dates") from None
    if kind in ("recurrence", "date", "date_span"):
        limit = int(bound["value"])
    else:
        limit = decimal.Decimal(bound["value"])
    return value >= limit if bound["op"] == "min" else value <= limit


def _describe(kind, bound):
    if kind == "interval":
        return f"daily {bound['from']}-{bound['to']} in zone {bound['zone']}"
    if kind == "date":
        return f"at most {bound['value']} days old"
    if kind == "date_span":
        return f"at most {bound['value']} working days in calendar {bound['calendar']}"
    side = "at most" if bound["op"] == "max" else "at least"
    if kind == "quantity":
        return f"{side} {bound['value']} {bound['unit']}, {bound['units']} units"
    if kind == "rate":
        return f"{side} {bound['value']} {bound['unit']} per {bound['per']}"
    return f"{side} {bound['value']} {bound['unit']}"


_COMPARISON = {
    "money": "Each rule compares the exact value in its own currency; a value equal to a max or "
    "min meets it",
    "quantity": "Each rule compares the exact value in its own unit and binary or decimal "
    "system; a value equal to a max or min meets it",
    "rate": "Each rule compares the exact value in its own currency and period; a value equal "
    "to a max or min meets it",
    "interval": "Each rule compares the local interval in its own zone: it meets the window when "
    "it starts at or after the window's start and ends at or before its end, and a window whose "
    "start is later than its end crosses midnight",
    "recurrence": "Each rule compares the occurrence count; a count equal to a max or min meets it",
    "date": "Each rule compares the age in days; an age equal to max_age_days meets it",
    "date_span": "Each rule compares the working days in its own calendar; a count equal to "
    "max_working_days meets it",
}
_BEYOND = {
    "money": "above a max or below a min",
    "quantity": "above a max or below a min",
    "rate": "above a max or below a min",
    "interval": "outside the daily window",
    "recurrence": "above a max or below a min",
    "date": "older than max_age_days",
    "date_span": "more working days than max_working_days",
}
_TAIL = (
    "A rule whose limit names a measure this call does not carry is in neither list. "
    "Containment only: not a rule match, exception or decision."
)
_KEYS_USED = {
    "money": "currency",
    "quantity": "unit and binary or decimal system",
    "rate": "currency and period",
    "interval": "zone",
    "date_span": "calendar",
}


def _measure_facts(m, rules, fact, trusted):
    name, kind = m["name"], m["kind"]
    values, facts = {}, []
    texts, notes = [], []
    for rule in rules:
        key = _comparison_key(kind, rule["bound"])
        if key not in values:
            comparable, text, note = _value(m, rule["bound"], trusted)
            values[key] = comparable
            texts.append(text)
            notes.append(note)
    where = f"call argument {m['argument']} (measure {name}, {kind})"
    if len(texts) == 1:
        facts.append(fact(f"measure_{name}_value", texts[0], f"Host value of {where}: {notes[0]}"))
    else:
        listed = " ".join(f"({i}) {note}" for i, note in enumerate(notes, start=1))
        facts.append(
            fact(
                f"measure_{name}_value",
                texts,
                f"Host values of {where}, one per {_KEYS_USED[kind]} its limits use, in list "
                f"order: {listed}",
            )
        )
    within = [
        r for r in rules if _meets(kind, values[_comparison_key(kind, r["bound"])], r["bound"])
    ]
    inside = {r["rule_id"] for r in within}
    beyond = [r for r in rules if r["rule_id"] not in inside]

    def shown(selected):
        listed = "; ".join(f"{r['rule_id']} ({_describe(kind, r['bound'])})" for r in selected)
        return listed or "none"

    facts.append(
        fact(
            f"measure_{name}_within_bound_of",
            [r["rule_id"] for r in within],
            f"Policy rules whose limit measure_{name}_value meets: {shown(within)}. "
            f"{_COMPARISON[kind]}. {_TAIL}",
        )
    )
    facts.append(
        fact(
            f"measure_{name}_beyond_bound_of",
            [r["rule_id"] for r in beyond],
            f"Policy rules whose limit measure_{name}_value does not meet ({_BEYOND[kind]}): "
            f"{shown(beyond)}. {_COMPARISON[kind]}. {_TAIL}",
        )
    )
    return facts


def compute(request):
    """The facts for one call in a fixed order, {"profile", "status": "ok", "facts"}, or a Refusal.

    ``request`` is ``{"profile", "trusted", "measures", "limit_rules"}``: the trusted
    configuration, the adapter's typed measures of the call, and ``compile_limits`` output. Per
    measure in request order that a limit rule constrains: one ``measure_<name>_value`` (a string,
    or a list of strings in rule order when its limits use several comparison keys), then
    ``measure_<name>_within_bound_of`` and ``measure_<name>_beyond_bound_of``, each a list of rule
    ids in policy order.
    """
    _keys(request, ("profile", "trusted", "measures", "limit_rules"))
    _require(request["profile"] == PROFILE, "bad_request", f"profile must be {PROFILE}")
    trusted = Trusted(request["trusted"])
    measures = _measures(request["measures"], trusted)
    rules = _limit_rules(request["limit_rules"], trusted)

    def fact(name, value, note):
        return {
            "name": name,
            "value": value,
            "unit": None,
            "provenance": PROVENANCE,
            "as_of": trusted.observed_at,
            "note": note,
        }

    facts = []
    for m in measures:
        selected = [r for r in rules if r["measure"] == m["name"]]
        if selected:
            facts.extend(_measure_facts(m, selected, fact, trusted))
    return {"profile": PROFILE, "status": "ok", "facts": facts}


def compute_or_refusal(request):
    """``compute`` with refusals as data: {"profile", "status": "refused", "code", "detail"}."""
    try:
        return compute(request)
    except Refusal as exc:
        return {"profile": PROFILE, "status": "refused", "code": exc.code, "detail": exc.detail}

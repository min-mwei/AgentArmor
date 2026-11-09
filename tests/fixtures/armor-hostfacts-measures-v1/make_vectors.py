"""Writes vectors.json and MANIFEST.json for armor-hostfacts-measures-v1 from hand-written cases.

The expected values below were written by hand from the binding interface section of
docs/hostfacts-measures-design.md: exact decimal arithmetic, fixed integer period factors, fixed
zone offsets, inclusive bounds and windows, RFC 5545 counting for the supported RRULE subset and
calendar day counts. This builder never imports or calls the reference implementation, so the
vectors are an independent check of both the Python reference and AgentArmor's native port.
Usage: python3 -I make_vectors.py [PATH_TO_measures.py]
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = "armor-hostfacts-measures-v1"
AT = "2026-11-05T12:00:00Z"
RATE_AS_OF = "2026-10-30T07:00:00Z"
SOURCE = "treasury feed"
REFUSAL_CODES = (
    "bad_request",
    "bad_trusted",
    "bad_amount",
    "unknown_currency_pair",
    "stale_rate",
    "zone_not_valid",
    "bad_instant",
    "unsupported_rrule",
    "bad_date",
    "unit_mismatch",
    "unsupported_period",
)
POLICY_ERRORS = (
    "bad_policy",
    "bad_limit_field",
    "unknown_measure",
    "bad_bound",
    "missing_units",
    "unknown_zone",
    "unknown_calendar",
    "too_many_limit_rules",
)


def fx(pair, rate, as_of=RATE_AS_OF, source=SOURCE):
    return {"pair": pair, "rate": rate, "source": source, "as_of": as_of}


USER = {"offset": "-06:00", "valid_from": "2026-11-01", "valid_until": "2027-03-13"}
RECIPIENT = {"offset": "+05:30", "valid_from": "2026-01-01", "valid_until": "2027-12-31"}
MEASURES = {
    "amount": "money",
    "file_size": "quantity",
    "base_salary": "rate",
    "event": "interval",
    "occurrences": "recurrence",
    "charge_date": "date",
    "leave": "date_span",
}
CONFIG = {
    "today": "2026-11-05",
    "observed_at": AT,
    "rates": [fx("EUR/USD", "1.08")],
    "zones": {"user": USER},
    "calendars": {"company": ["2026-12-25", "2027-01-01"]},
    "measures": MEASURES,
}


# --- the adapter's typed measures --------------------------------------------------------------
def arg(name):
    return f"/call/arguments/{name}"


def money(amount, currency, exp=0, name="amount"):
    return {
        "name": name,
        "kind": "money",
        "argument": arg(name),
        "amount": amount,
        "currency": currency,
        "minor_exponent": exp,
    }


def quantity(value, unit, name="file_size"):
    return {"name": name, "kind": "quantity", "argument": arg(name), "value": value, "unit": unit}


def rate(amount, per, currency="USD", unit=None, name="base_salary"):
    out = {"name": name, "kind": "rate", "argument": arg(name), "amount": amount}
    if unit is None:
        out["currency"] = currency
    else:
        out["unit"] = unit
    out["per"] = per
    return out


def interval(start, minutes, name="event"):
    return {
        "name": name,
        "kind": "interval",
        "argument": arg(name),
        "start": start,
        "duration_minutes": minutes,
    }


def recurrence(dtstart, rrule, name="occurrences"):
    return {
        "name": name,
        "kind": "recurrence",
        "argument": arg(name),
        "dtstart": dtstart,
        "rrule": rrule,
    }


def date(value, name="charge_date"):
    return {"name": name, "kind": "date", "argument": arg(name), "date": value}


def span(start, end, name="leave"):
    return {"name": name, "kind": "date_span", "argument": arg(name), "start": start, "end": end}


# --- compiled limit rules (the normalized form compile_limits returns) -------------------------
def lim(rid, measure, kind, bound):
    return {"rule_id": rid, "measure": measure, "kind": kind, "bound": bound}


def cap(rid, value, unit, op="max", measure="amount"):
    return lim(rid, measure, "money", {"op": op, "value": value, "unit": unit})


def size(rid, value, unit, units, op="max", measure="file_size"):
    bound = {"op": op, "value": value, "unit": unit, "units": units}
    return lim(rid, measure, "quantity", bound)


def per(rid, value, unit, period, op="max", measure="base_salary"):
    return lim(rid, measure, "rate", {"op": op, "value": value, "unit": unit, "per": period})


def window(rid, start, end, zone="user", measure="event"):
    bound = {"op": "daily", "from": start, "to": end, "zone": zone}
    return lim(rid, measure, "interval", bound)


def count_cap(rid, value, op="max", measure="occurrences"):
    return lim(rid, measure, "recurrence", {"op": op, "value": value, "unit": "occurrences"})


def age_cap(rid, days, measure="charge_date"):
    return lim(rid, measure, "date", {"op": "max_age_days", "value": days})


def workday_cap(rid, days, calendar="company", measure="leave"):
    bound = {"op": "max_working_days", "value": days, "calendar": calendar}
    return lim(rid, measure, "date_span", bound)


CFO = cap("cfo-approval", "10000", "USD")
SMALL = cap("small-payments", "10000", "USD")
UPLOAD = size("upload-size", "2", "GB", "binary")
SALARY = per("salary-band", "142000", "USD", "year")
HOURS = window("working-hours", "09:00", "17:00")
NIGHT = window("night-batch", "22:00", "02:00")
SERIES = count_cap("series-cap", "12")
REFUND = age_cap("refund-window", "90")
LEAVE = workday_cap("leave-cap", "10")

CASES = []


def fact(name, value, as_of):
    return {
        "name": name,
        "value": value,
        "unit": None,
        "provenance": "host_computed",
        "as_of": as_of,
    }


def case(
    cid,
    why,
    measures,
    rules=(),
    trusted=None,
    values=None,
    refused=None,
    full=False,
    notes=None,
    profile=PROFILE,
    drop=(),
    drop_trusted=(),
):
    cfg = copy.deepcopy(CONFIG)
    cfg.update(copy.deepcopy(trusted or {}))
    for key in drop_trusted:
        del cfg[key]
    request = {
        "profile": profile,
        "trusted": cfg,
        "measures": copy.deepcopy(list(measures)),
        "limit_rules": copy.deepcopy(list(rules)),
    }
    for key in drop:
        del request[key]
    if refused:
        expect = {"status": "refused", "code": refused}
    else:
        expect = {"status": "ok", "values": [list(v) for v in values]}
        if full:
            expect["facts_without_note"] = [fact(n, v, cfg["observed_at"]) for n, v in values]
        if notes:
            expect["note_includes"] = [[i, list(s)] for i, s in notes]
    CASES.append({"id": cid, "why": why, "request": request, "expect": expect})


def facts(name, value, within, beyond):
    """The facts of one constrained measure: one value fact, then the two lists.

    The value is a string, or a list of strings (one per comparison key, ordered by the first rule
    that uses each key) when the measure's limits use several currencies, units and systems,
    periods, zones or calendars.
    """
    shown = value if isinstance(value, str) else list(value)
    return [
        (f"measure_{name}_value", shown),
        (f"measure_{name}_within_bound_of", list(within)),
        (f"measure_{name}_beyond_bound_of", list(beyond)),
    ]


# --- money ---------------------------------------------------------------------------------------
EUR_9400 = money("9400.00", "EUR")
case(
    "money_stated_rate_exact_scale",
    "9400.00 EUR at the declared EUR/USD 1.08: scales add, 10152.0000 USD, above 10000",
    [EUR_9400],
    [CFO],
    values=facts("amount", "10152.0000 USD", [], ["cfo-approval"]),
    full=True,
    notes=[(0, ["1.08", SOURCE, RATE_AS_OF])],
)
case(
    "money_rules_keep_policy_order",
    "several rules on one measure: one value for one unit, both lists in policy order",
    [EUR_9400],
    [CFO, cap("review-floor", "500", "USD", op="min"), cap("card-limit", "20000", "USD"), SMALL],
    values=facts(
        "amount",
        "10152.0000 USD",
        ["review-floor", "card-limit"],
        ["cfo-approval", "small-payments"],
    ),
)
case(
    "money_minor_units_then_rate",
    "940000 at exponent 2 is 9400.00 EUR, then 10152.0000 USD at 1.08",
    [money("940000", "EUR", exp=2)],
    [CFO],
    values=facts("amount", "10152.0000 USD", [], ["cfo-approval"]),
)
case(
    "money_minor_units_exponent_2",
    "2750 at exponent 2 is 27.50 USD: within a max, below a min",
    [money("2750", "USD", exp=2)],
    [SMALL, cap("min-charge", "50", "USD", op="min")],
    values=facts("amount", "27.50 USD", ["small-payments"], ["min-charge"]),
)
case(
    "money_exactly_at_max_within",
    "a value equal to max is within (bounds are inclusive)",
    [money("10000.00", "USD")],
    [CFO],
    values=facts("amount", "10000.00 USD", ["cfo-approval"], []),
)
case(
    "money_one_cent_above_max_beyond",
    "one smallest unit above max is beyond",
    [money("10000.01", "USD")],
    [CFO],
    values=facts("amount", "10000.01 USD", [], ["cfo-approval"]),
)
case(
    "money_minor_units_at_max_within",
    "1000000 at exponent 2 is exactly 10000.00 USD",
    [money("1000000", "USD", exp=2)],
    [CFO],
    values=facts("amount", "10000.00 USD", ["cfo-approval"], []),
)
case(
    "money_minor_units_one_above_beyond",
    "1000001 at exponent 2 is 10000.01 USD, one minor unit above",
    [money("1000001", "USD", exp=2)],
    [CFO],
    values=facts("amount", "10000.01 USD", [], ["cfo-approval"]),
)
case(
    "money_converted_exactly_at_max_within",
    "8000.00 EUR at 1.25 is exactly 10000.0000 USD: within",
    [money("8000.00", "EUR")],
    [CFO],
    trusted={"rates": [fx("EUR/USD", "1.25")]},
    values=facts("amount", "10000.0000 USD", ["cfo-approval"], []),
)
case(
    "money_converted_one_cent_above_beyond",
    "8000.01 EUR at 1.25 is 10000.0125 USD: beyond",
    [money("8000.01", "EUR")],
    [CFO],
    trusted={"rates": [fx("EUR/USD", "1.25")]},
    values=facts("amount", "10000.0125 USD", [], ["cfo-approval"]),
)
case(
    "money_four_place_rate",
    "1500000 JPY at JPY/USD 0.0067 is 10050.0000 USD (scale 0 plus 4)",
    [money("1500000", "JPY")],
    [CFO],
    trusted={"rates": [fx("EUR/USD", "1.08"), fx("JPY/USD", "0.0067")]},
    values=facts("amount", "10050.0000 USD", [], ["cfo-approval"]),
)
case(
    "money_two_units_two_values",
    "rules in EUR and in USD: one value fact listing a value per distinct unit, in rule order",
    [EUR_9400],
    [cap("eu-cap", "9500", "EUR"), CFO],
    values=facts("amount", ["9400.00 EUR", "10152.0000 USD"], ["eu-cap"], ["cfo-approval"]),
)
case(
    "money_units_by_first_rule_each_once",
    "rules in USD, EUR, USD, EUR: each currency once, ordered by the first rule that uses it",
    [EUR_9400],
    [CFO, cap("eu-cap", "9500", "EUR"), SMALL, cap("eu-floor", "9000", "EUR", op="min")],
    values=facts(
        "amount",
        ["10152.0000 USD", "9400.00 EUR"],
        ["eu-cap", "eu-floor"],
        ["cfo-approval", "small-payments"],
    ),
    full=True,
    notes=[(0, ["1.08", SOURCE, RATE_AS_OF])],
)
case(
    "money_same_currency_empty_rate_table",
    "a USD call against a USD limit needs no rate: an empty rate table computes normally",
    [money("9400.00", "USD")],
    [CFO],
    trusted={"rates": []},
    values=facts("amount", "9400.00 USD", ["cfo-approval"], []),
    full=True,
)
case(
    "refuse_conversion_empty_rate_table",
    "a EUR call against a USD limit with no configured rate refuses when it is computed",
    [EUR_9400],
    [CFO],
    trusted={"rates": []},
    refused="unknown_currency_pair",
)
case(
    "money_rate_as_of_equal_to_observed_at",
    "a rate dated exactly at observed_at is not after it",
    [EUR_9400],
    [CFO],
    trusted={"rates": [fx("EUR/USD", "1.08", as_of=AT)]},
    values=facts("amount", "10152.0000 USD", [], ["cfo-approval"]),
)
case(
    "money_f1_other_rate",
    "F1: the same call at another trusted rate, 1.06, is 9964.0000 USD: within",
    [EUR_9400],
    [CFO],
    trusted={"rates": [fx("EUR/USD", "1.06")]},
    values=facts("amount", "9964.0000 USD", ["cfo-approval"], []),
)
case(
    "refuse_inverse_pair",
    "A/B converts A to B only: USD to EUR with EUR/USD would be an inverse",
    [money("10000", "USD")],
    [cap("eu-cap", "9500", "EUR")],
    refused="unknown_currency_pair",
)
case(
    "refuse_chain",
    "GBP/EUR then EUR/USD is a chain; there is no triangulation",
    [money("8000", "GBP")],
    [CFO],
    trusted={"rates": [fx("GBP/EUR", "1.15"), fx("EUR/USD", "1.08")]},
    refused="unknown_currency_pair",
)
case(
    "refuse_missing_pair",
    "no pair converts JPY to USD",
    [money("1500000", "JPY")],
    [CFO],
    refused="unknown_currency_pair",
)
case(
    "refuse_stale_rate",
    "the rate's as_of is one second after observed_at",
    [EUR_9400],
    [CFO],
    trusted={"rates": [fx("EUR/USD", "1.08", as_of="2026-11-05T12:00:01Z")]},
    refused="stale_rate",
)
case(
    "refuse_bad_amount",
    "a thousands separator is not a decimal string",
    [money("9,400.00", "EUR")],
    [CFO],
    refused="bad_amount",
)

# --- data sizes ----------------------------------------------------------------------------------
case(
    "size_binary_2060mb_beyond",
    "2060 MB is 2.01171875 GB at 1024 MB per GB: above 2 GB",
    [quantity("2060", "MB")],
    [UPLOAD],
    values=facts("file_size", "2.01171875 GB", [], ["upload-size"]),
    notes=[(0, ["1024"])],
)
case(
    "size_decimal_2060mb_beyond",
    "2060 MB is 2.06 GB at 1000 MB per GB: above 2 GB",
    [quantity("2060", "MB")],
    [size("upload-size", "2", "GB", "decimal")],
    values=facts("file_size", "2.06 GB", [], ["upload-size"]),
)
case(
    "size_binary_2040mb_within",
    "2040 MB is 1.9921875 binary GB: within 2 GB",
    [quantity("2040", "MB")],
    [UPLOAD],
    values=facts("file_size", "1.9921875 GB", ["upload-size"], []),
)
case(
    "size_decimal_2040mb_beyond",
    "the same 2040 MB is 2.04 decimal GB: above 2 GB",
    [quantity("2040", "MB")],
    [size("upload-size", "2", "GB", "decimal")],
    values=facts("file_size", "2.04 GB", [], ["upload-size"]),
)
case(
    "size_gb_to_mb_binary_at_max",
    "2 GB is exactly 2048 binary MB: within",
    [quantity("2", "GB")],
    [size("upload-mb", "2048", "MB", "binary")],
    values=facts("file_size", "2048 MB", ["upload-mb"], []),
)
case(
    "size_gb_to_mb_decimal_at_max",
    "2 GB is exactly 2000 decimal MB: within",
    [quantity("2", "GB")],
    [size("upload-mb", "2000", "MB", "decimal")],
    values=facts("file_size", "2000 MB", ["upload-mb"], []),
)
case(
    "size_fraction_keeps_scale",
    "2.1 GB is 2150.4 binary MB: above 2048 MB",
    [quantity("2.1", "GB")],
    [size("upload-mb", "2048", "MB", "binary")],
    values=facts("file_size", "2150.4 MB", [], ["upload-mb"]),
)
case(
    "size_kb_to_mb_binary",
    "1536 KB is 1.5 binary MB: above 1 MB",
    [quantity("1536", "KB")],
    [size("attach-1mb", "1", "MB", "binary")],
    values=facts("file_size", "1.5 MB", [], ["attach-1mb"]),
)
case(
    "size_bytes_exact_binary_gb",
    "1073741824 B is exactly 1 binary GB: within",
    [quantity("1073741824", "B")],
    [size("gb-cap", "1", "GB", "binary")],
    values=facts("file_size", "1 GB", ["gb-cap"], []),
)
case(
    "size_tb_to_gb_decimal",
    "3 TB is 3000 decimal GB: above 2500 GB",
    [quantity("3", "TB")],
    [size("archive-cap", "2500", "GB", "decimal")],
    values=facts("file_size", "3000 GB", [], ["archive-cap"]),
)
case(
    "size_same_unit_no_factor",
    "a size in the rule's unit needs no factor",
    [quantity("2", "GB")],
    [UPLOAD],
    values=facts("file_size", "2 GB", ["upload-size"], []),
)
case(
    "size_two_units_two_values",
    "rules in GB and in MB: one value fact listing a value per distinct unit, in rule order",
    [quantity("2060", "MB")],
    [UPLOAD, size("attach-cap", "2100", "MB", "binary")],
    values=facts("file_size", ["2.01171875 GB", "2060 MB"], ["attach-cap"], ["upload-size"]),
)
case(
    "size_one_unit_two_systems",
    "binary and decimal GB limits are two keys: 2.01171875 GB above 2, 2.06 GB within 2.1",
    [quantity("2060", "MB")],
    [UPLOAD, size("upload-decimal", "2.1", "GB", "decimal")],
    values=facts("file_size", ["2.01171875 GB", "2.06 GB"], ["upload-decimal"], ["upload-size"]),
    notes=[(0, ["1024", "1000"])],
)

# --- rates and periods ---------------------------------------------------------------------------
case(
    "rate_month_to_year_beyond",
    "12000 USD per month is 144000 USD per year (factor 12): above 142000",
    [rate("12000", "month")],
    [SALARY],
    values=facts("base_salary", "144000 USD per year", [], ["salary-band"]),
)
case(
    "rate_quarter_to_year_at_max",
    "35500 USD per quarter is 142000 USD per year (factor 4): at the max",
    [rate("35500", "quarter")],
    [SALARY],
    values=facts("base_salary", "142000 USD per year", ["salary-band"], []),
)
case(
    "rate_month_to_quarter_at_max",
    "12000 USD per month is 36000 USD per quarter (factor 3): at the max",
    [rate("12000", "month")],
    [per("quarter-cap", "36000", "USD", "quarter")],
    values=facts("base_salary", "36000 USD per quarter", ["quarter-cap"], []),
)
case(
    "rate_day_to_week",
    "400 USD per day is 2800 USD per week (factor 7): above 2500",
    [rate("400", "day")],
    [per("weekly-cap", "2500", "USD", "week")],
    values=facts("base_salary", "2800 USD per week", [], ["weekly-cap"]),
)
case(
    "rate_same_period",
    "a rate in the rule's period needs no factor",
    [rate("142000.50", "year")],
    [SALARY],
    values=facts("base_salary", "142000.50 USD per year", [], ["salary-band"]),
)
case(
    "rate_converted_and_scaled",
    "9000 EUR per month at 1.08 and factor 12 is 116640.00 USD per year",
    [rate("9000", "month", currency="EUR")],
    [SALARY],
    values=facts("base_salary", "116640.00 USD per year", ["salary-band"], []),
)
case(
    "rate_two_periods_one_value_list",
    "limits per month and per year: one value fact listing 12000 per month and 144000 per year",
    [rate("12000", "month")],
    [per("monthly-cap", "12500", "USD", "month"), SALARY],
    values=facts(
        "base_salary",
        ["12000 USD per month", "144000 USD per year"],
        ["monthly-cap"],
        ["salary-band"],
    ),
    full=True,
)
case(
    "rate_same_currency_empty_rate_table",
    "a USD rate against a USD rate limit needs no configured rate",
    [rate("12000", "month")],
    [SALARY],
    trusted={"rates": []},
    values=facts("base_salary", "144000 USD per year", [], ["salary-band"]),
)
case(
    "refuse_week_to_year",
    "week to year has no fixed factor",
    [rate("2800", "week")],
    [SALARY],
    refused="unsupported_period",
)
case(
    "refuse_down_scaling",
    "year to month is down-scaling",
    [rate("142000", "year")],
    [per("monthly-cap", "12000", "USD", "month")],
    refused="unsupported_period",
)
case(
    "refuse_rate_bad_amount",
    "a rate amount must be a decimal string",
    [rate("twelve thousand", "month")],
    [SALARY],
    refused="bad_amount",
)
case(
    "refuse_unit_mismatch",
    "a quantity rate (GB per month) cannot meet a currency bound",
    [rate("750", "month", unit="GB", name="egress")],
    [per("egress-budget", "500", "USD", "month", measure="egress")],
    trusted={"measures": {**MEASURES, "egress": "rate"}},
    refused="unit_mismatch",
)

# --- instants and daily windows ------------------------------------------------------------------
MEETING_16 = interval("2026-11-12T17:00:00-05:00", 60)
TWO_ZONES = {"zones": {"user": USER, "recipient": RECIPIENT}}
case(
    "interval_ends_exactly_at_17_within",
    "F6: 22:00Z is 16:00 at -06:00; a meeting ending exactly at 17:00 is within",
    [MEETING_16],
    [HOURS],
    values=facts("event", "16:00-17:00 on 2026-11-12 at UTC-06:00 (user)", ["working-hours"], []),
    full=True,
    notes=[(0, ["-06:00", "2026-11-01", "2027-03-13"])],
)
case(
    "interval_ends_one_minute_late_beyond",
    "ending at 17:01 is outside the window",
    [interval("2026-11-12T22:01:00+00:00", 60)],
    [HOURS],
    values=facts("event", "16:01-17:01 on 2026-11-12 at UTC-06:00 (user)", [], ["working-hours"]),
)
case(
    "interval_starts_0830_beyond",
    "starting 08:30 local is before the window",
    [interval("2026-11-12T14:30:00+00:00", 30)],
    [HOURS],
    values=facts("event", "08:30-09:00 on 2026-11-12 at UTC-06:00 (user)", [], ["working-hours"]),
)
case(
    "interval_starts_exactly_0900_within",
    "starting exactly at 09:00 local is within",
    [interval("2026-11-12T10:00:00-05:00", 30)],
    [HOURS],
    values=facts("event", "09:00-09:30 on 2026-11-12 at UTC-06:00 (user)", ["working-hours"], []),
)
case(
    "interval_local_date_before_utc_date",
    "02:00Z on the 13th is 20:00 on the 12th at -06:00",
    [interval("2026-11-13T02:00:00+00:00", 60)],
    [HOURS],
    values=facts("event", "20:00-21:00 on 2026-11-12 at UTC-06:00 (user)", [], ["working-hours"]),
)
case(
    "interval_utc_designator_z",
    "a Z offset is UTC, as in observed_at",
    [interval("2026-11-12T15:00:00Z", 60)],
    [HOURS],
    values=facts("event", "09:00-10:00 on 2026-11-12 at UTC-06:00 (user)", ["working-hours"], []),
)
case(
    "night_window_inside_before_midnight",
    "a 22:00-02:00 window crosses midnight: 23:00-23:30 is inside",
    [interval("2026-11-13T05:00:00+00:00", 30)],
    [NIGHT],
    values=facts("event", "23:00-23:30 on 2026-11-12 at UTC-06:00 (user)", ["night-batch"], []),
)
case(
    "night_window_inside_after_midnight",
    "01:15-02:00 is inside and ends exactly at the window's end",
    [interval("2026-11-13T07:15:00+00:00", 45)],
    [NIGHT],
    values=facts("event", "01:15-02:00 on 2026-11-13 at UTC-06:00 (user)", ["night-batch"], []),
)
case(
    "night_window_starts_exactly_at_start",
    "22:00-23:00 starts exactly at the window's start",
    [interval("2026-11-13T04:00:00+00:00", 60)],
    [NIGHT],
    values=facts("event", "22:00-23:00 on 2026-11-12 at UTC-06:00 (user)", ["night-batch"], []),
)
case(
    "night_window_outside_starts_before",
    "21:30-22:30 starts before 22:00",
    [interval("2026-11-13T03:30:00+00:00", 60)],
    [NIGHT],
    values=facts("event", "21:30-22:30 on 2026-11-12 at UTC-06:00 (user)", [], ["night-batch"]),
)
case(
    "night_window_outside_ends_after",
    "01:30-02:30 ends after 02:00",
    [interval("2026-11-13T07:30:00+00:00", 60)],
    [NIGHT],
    values=facts("event", "01:30-02:30 on 2026-11-13 at UTC-06:00 (user)", [], ["night-batch"]),
)
case(
    "night_window_outside_midday",
    "12:00-13:00 is outside a window that crosses midnight",
    [interval("2026-11-12T18:00:00+00:00", 60)],
    [NIGHT],
    values=facts("event", "12:00-13:00 on 2026-11-12 at UTC-06:00 (user)", [], ["night-batch"]),
)
case(
    "interval_two_rules_one_zone",
    "two windows in one zone: one value, lists in policy order",
    [MEETING_16],
    [HOURS, NIGHT],
    values=facts(
        "event",
        "16:00-17:00 on 2026-11-12 at UTC-06:00 (user)",
        ["working-hours"],
        ["night-batch"],
    ),
)
case(
    "interval_two_zones_two_values",
    "windows in two zones: one value fact listing a value per zone, in rule order; +05:30 is a "
    "half-hour offset",
    [interval("2026-11-12T15:00:00+00:00", 60)],
    [HOURS, window("recipient-hours", "09:00", "17:00", zone="recipient")],
    trusted=TWO_ZONES,
    values=facts(
        "event",
        [
            "09:00-10:00 on 2026-11-12 at UTC-06:00 (user)",
            "20:30-21:30 on 2026-11-12 at UTC+05:30 (recipient)",
        ],
        ["working-hours"],
        ["recipient-hours"],
    ),
)
case(
    "interval_f1_other_offset",
    "F1: the same meeting with the user zone at -05:00 is 17:00-18:00: beyond",
    [MEETING_16],
    [HOURS],
    trusted={"zones": {"user": {**USER, "offset": "-05:00"}}},
    values=facts("event", "17:00-18:00 on 2026-11-12 at UTC-05:00 (user)", [], ["working-hours"]),
)
case(
    "refuse_instant_without_offset",
    "a timestamp without an offset",
    [interval("2026-11-12T15:00:00", 60)],
    [HOURS],
    refused="bad_instant",
)
case(
    "refuse_zone_after_validity",
    "2027-04-01 is after the user entry's valid_until 2027-03-13",
    [interval("2027-04-01T15:00:00+00:00", 60)],
    [HOURS],
    refused="zone_not_valid",
)
case(
    "refuse_zone_before_validity",
    "2026-10-20 is before the user entry's valid_from 2026-11-01",
    [interval("2026-10-20T15:00:00+00:00", 60)],
    [HOURS],
    refused="zone_not_valid",
)
case(
    "refuse_bad_zone_offset",
    "the trusted zone entry's offset is not an offset",
    [MEETING_16],
    [HOURS],
    trusted={"zones": {"user": {**USER, "offset": "six hours behind"}}},
    refused="bad_trusted",
)

# --- recurrence (dtstart always matches the rule, so DTSTART inclusion never matters) -----------
MONDAY = "2026-11-09T10:00"
case(
    "weekly_mondays_until_inclusive",
    "Mondays 2026-11-09 to UNTIL 2027-01-04 10:00 inclusive: 9 (the UNTIL Monday counts)",
    [recurrence(MONDAY, "FREQ=WEEKLY;BYDAY=MO;UNTIL=20270104T100000")],
    [SERIES, count_cap("short-cap", "8")],
    values=facts("occurrences", "9 occurrences", ["series-cap"], ["short-cap"]),
    full=True,
)
case(
    "weekly_until_before_last_start",
    "UNTIL 09:59 on 2027-01-04 excludes that 10:00 occurrence: 8",
    [recurrence(MONDAY, "FREQ=WEEKLY;BYDAY=MO;UNTIL=20270104T095900")],
    [SERIES, count_cap("short-cap", "8")],
    values=facts("occurrences", "8 occurrences", ["series-cap", "short-cap"], []),
)
case(
    "weekly_count",
    "COUNT=13 on Mondays, Wednesdays and Fridays: 13",
    [recurrence(MONDAY, "FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=13")],
    [SERIES],
    values=facts("occurrences", "13 occurrences", [], ["series-cap"]),
)
case(
    "weekly_count_at_max",
    "COUNT=12 equals the max: within",
    [recurrence(MONDAY, "FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=12")],
    [SERIES],
    values=facts("occurrences", "12 occurrences", ["series-cap"], []),
)
case(
    "daily_until_inclusive",
    "daily from 2026-11-06 through UNTIL 2026-11-17 09:00: 12",
    [recurrence("2026-11-06T09:00", "FREQ=DAILY;UNTIL=20261117T090000")],
    [SERIES],
    values=facts("occurrences", "12 occurrences", ["series-cap"], []),
)
case(
    "daily_count",
    "daily COUNT=30: 30",
    [recurrence("2026-11-06T09:00", "FREQ=DAILY;COUNT=30")],
    [SERIES],
    values=facts("occurrences", "30 occurrences", [], ["series-cap"]),
)
case(
    "monthly_bymonthday_until",
    "the 15th from 2026-11-15 through UNTIL 2027-11-15 09:00: 13 months",
    [recurrence("2026-11-15T09:00", "FREQ=MONTHLY;BYMONTHDAY=15;UNTIL=20271115T090000")],
    [SERIES],
    values=facts("occurrences", "13 occurrences", [], ["series-cap"]),
)
case(
    "monthly_two_monthdays_and_min",
    "the 1st and 15th, 2026-12-01 through 2027-01-31: 4, below a min of 6",
    [recurrence("2026-12-01T09:00", "FREQ=MONTHLY;BYMONTHDAY=1,15;UNTIL=20270131T090000")],
    [SERIES, count_cap("min-sessions", "6", op="min")],
    values=facts("occurrences", "4 occurrences", ["series-cap"], ["min-sessions"]),
)
case(
    "monthly_count",
    "monthly on the 15th, COUNT=6: 6",
    [recurrence("2026-11-15T09:00", "FREQ=MONTHLY;BYMONTHDAY=15;COUNT=6")],
    [SERIES],
    values=facts("occurrences", "6 occurrences", ["series-cap"], []),
)
case(
    "refuse_rrule_interval",
    "INTERVAL is not in the subset",
    [recurrence(MONDAY, "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;COUNT=5")],
    [SERIES],
    refused="unsupported_rrule",
)
case(
    "refuse_rrule_byhour",
    "BYHOUR is not in the subset",
    [recurrence("2026-11-06T09:00", "FREQ=DAILY;BYHOUR=9,17;COUNT=10")],
    [SERIES],
    refused="unsupported_rrule",
)
case(
    "refuse_rrule_yearly",
    "FREQ=YEARLY is not in the subset",
    [recurrence("2026-11-15T09:00", "FREQ=YEARLY;COUNT=3")],
    [SERIES],
    refused="unsupported_rrule",
)
case(
    "refuse_rrule_monthly_byday",
    "MONTHLY is supported with BYMONTHDAY only",
    [recurrence("2026-11-10T09:00", "FREQ=MONTHLY;BYDAY=2TU;COUNT=6")],
    [SERIES],
    refused="unsupported_rrule",
)
case(
    "refuse_rrule_weekly_without_byday",
    "WEEKLY is supported with BYDAY only",
    [recurrence(MONDAY, "FREQ=WEEKLY;COUNT=5")],
    [SERIES],
    refused="unsupported_rrule",
)
case(
    "refuse_rrule_unbounded",
    "neither UNTIL nor COUNT: the series has no count",
    [recurrence("2026-11-06T09:00", "FREQ=DAILY")],
    [SERIES],
    refused="unsupported_rrule",
)

# --- dates ---------------------------------------------------------------------------------------
case(
    "age_exactly_max_within",
    "2026-08-07 to today 2026-11-05 is 90 days (end counted, start excluded): within",
    [date("2026-08-07")],
    [REFUND],
    values=facts("charge_date", "90 days old", ["refund-window"], []),
    full=True,
)
case(
    "age_one_day_over_beyond",
    "2026-08-06 is 91 days old: beyond 90, within 120",
    [date("2026-08-06")],
    [REFUND, age_cap("dispute-window", "120")],
    values=facts("charge_date", "91 days old", ["dispute-window"], ["refund-window"]),
)
case(
    "age_today_zero",
    "a charge dated today is 0 days old",
    [date("2026-11-05")],
    [REFUND],
    values=facts("charge_date", "0 days old", ["refund-window"], []),
)
case(
    "age_f1_other_today",
    "F1: the same charge one day later is 91 days old",
    [date("2026-08-07")],
    [REFUND],
    trusted={"today": "2026-11-06", "observed_at": "2026-11-06T12:00:00Z"},
    values=facts("charge_date", "91 days old", [], ["refund-window"]),
)
case(
    "age_across_leap_day",
    "2028-01-31 to 2028-03-01 crosses 2028-02-29: 30 days",
    [date("2028-01-31")],
    [age_cap("month-window", "30")],
    trusted={"today": "2028-03-01", "observed_at": "2028-03-01T12:00:00Z"},
    values=facts("charge_date", "30 days old", ["month-window"], []),
)
case(
    "refuse_bad_date",
    "2026-02-30 is not a date",
    [date("2026-02-30")],
    [REFUND],
    refused="bad_date",
)

# --- date spans and working days -----------------------------------------------------------------
case(
    "working_days_weekends_and_holidays",
    "2026-12-21 to 2027-01-04: 11 weekdays less 12-25 and 01-01 is 9",
    [span("2026-12-21", "2027-01-04")],
    [LEAVE, workday_cap("short-leave", "8")],
    values=facts("leave", "9 working days", ["leave-cap"], ["short-leave"]),
    full=True,
    notes=[(0, ["2026-12-25", "2027-01-01"])],
)
case(
    "working_days_two_weeks_at_max",
    "Monday 2026-11-16 to Friday 2026-11-27: 10, at the max",
    [span("2026-11-16", "2026-11-27")],
    [LEAVE],
    values=facts("leave", "10 working days", ["leave-cap"], []),
)
case(
    "working_days_one_over",
    "2026-11-16 to Monday 2026-11-30: 11",
    [span("2026-11-16", "2026-11-30")],
    [LEAVE],
    values=facts("leave", "11 working days", [], ["leave-cap"]),
)
case(
    "working_days_weekend_holiday_once",
    "a holiday on Saturday 2026-12-26 is not subtracted twice: 4",
    [span("2026-12-21", "2026-12-27")],
    [LEAVE, workday_cap("tiny-leave", "3")],
    trusted={"calendars": {"company": ["2026-12-25", "2026-12-26", "2027-01-01"]}},
    values=facts("leave", "4 working days", ["leave-cap"], ["tiny-leave"]),
)
case(
    "working_days_weekend_only",
    "Saturday and Sunday only: 0",
    [span("2026-11-07", "2026-11-08")],
    [LEAVE],
    values=facts("leave", "0 working days", ["leave-cap"], []),
)
case(
    "working_days_two_calendars",
    "limits in two calendars: 9 working days in company, 8 in branch, which also closes 2026-12-28",
    [span("2026-12-21", "2027-01-04")],
    [LEAVE, workday_cap("branch-leave", "7", calendar="branch")],
    trusted={
        "calendars": {
            "company": ["2026-12-25", "2027-01-01"],
            "branch": ["2026-12-25", "2026-12-28", "2027-01-01"],
        }
    },
    values=facts("leave", ["9 working days", "8 working days"], ["leave-cap"], ["branch-leave"]),
    notes=[(0, ["2026-12-28"])],
)
case(
    "refuse_bad_span_date",
    "2026-11-31 is not a date",
    [span("2026-11-31", "2026-12-04")],
    [LEAVE],
    refused="bad_date",
)

# --- which measures and rules produce facts ------------------------------------------------------
case(
    "rule_on_absent_measure_in_neither_list",
    "a rule on a measure the call does not carry is in neither list",
    [EUR_9400],
    [CFO, HOURS],
    values=facts("amount", "10152.0000 USD", [], ["cfo-approval"]),
)
case(
    "unconstrained_measure_no_facts",
    "a measure no rule constrains gets no facts",
    [quantity("2060", "MB")],
    [CFO],
    values=[],
)
case(
    "no_measures_no_facts",
    "a call with no declared measure gets no facts",
    [],
    [CFO],
    values=[],
)
case(
    "no_limit_rules_no_facts",
    "a policy without limit rules gives no facts",
    [EUR_9400],
    [],
    values=[],
)
case(
    "facts_follow_request_order",
    "facts follow the call's measure order, not the rule order",
    [MEETING_16, EUR_9400, quantity("2060", "MB")],
    [CFO, HOURS],
    values=facts("event", "16:00-17:00 on 2026-11-12 at UTC-06:00 (user)", ["working-hours"], [])
    + facts("amount", "10152.0000 USD", [], ["cfo-approval"]),
    full=True,
)

# --- request and trusted-input refusals ----------------------------------------------------------
case(
    "refuse_wrong_profile",
    "the request names another profile",
    [EUR_9400],
    [CFO],
    profile="armor-hostfacts-paths-v1",
    refused="bad_request",
)
case(
    "refuse_missing_measures",
    "the request has no measures field",
    [EUR_9400],
    [CFO],
    drop=("measures",),
    refused="bad_request",
)
case(
    "refuse_missing_observed_at",
    "every fact's as_of is observed_at, which the trusted input lacks",
    [EUR_9400],
    [CFO],
    drop_trusted=("observed_at",),
    refused="bad_trusted",
)

# --- policy authoring: what users write, compiled once at load time ----------------------------
POLICY = []


def policy(pid, why, rules, expect=None, error=None, config=None):
    cfg = copy.deepcopy(CONFIG)
    cfg.update(copy.deepcopy(config or {}))
    out = {"status": "error", "code": error} if error else {"status": "ok", "limit_rules": expect}
    POLICY.append({"id": pid, "why": why, "rules": rules, "config": cfg, "expect": out})


def pr(rid, limit=None, tier="soft_deny"):
    out = {"id": rid, "tier": tier, "description": f"Rule {rid}."}
    if limit is not None:
        out["limit"] = limit
    return out


SPEC_RULES = [
    {
        "id": "cfo-approval",
        "tier": "soft_deny",
        "description": "Payments above USD 10,000 after conversion need CFO approval.",
        "limit": {"measure": "amount", "max": "10000 USD"},
    },
    {
        "id": "small-payments",
        "tier": "allow",
        "description": "Payments at or below USD 10,000 need no approval.",
        "limit": {"measure": "amount", "max": "10000 USD"},
    },
    {
        "id": "upload-size",
        "tier": "hard_deny",
        "description": "Never upload files larger than 2 GB.",
        "limit": {"measure": "file_size", "max": "2 GB", "units": "binary"},
    },
    {
        "id": "salary-band",
        "tier": "soft_deny",
        "description": "Offers above the L4 band maximum need committee approval.",
        "limit": {"measure": "base_salary", "max": "142000 USD", "per": "year"},
    },
    {
        "id": "working-hours",
        "tier": "hard_deny",
        "description": "Never schedule outside 09:00 to 17:00 in my time zone.",
        "limit": {"measure": "event", "daily": "09:00-17:00", "zone": "user"},
    },
    {
        "id": "series-cap",
        "tier": "hard_deny",
        "description": "Recurring series may have at most 12 occurrences.",
        "limit": {"measure": "occurrences", "max": "12"},
    },
    {
        "id": "refund-window",
        "tier": "soft_deny",
        "description": "Refunds of charges older than 90 days need a manager ticket.",
        "limit": {"measure": "charge_date", "max_age_days": 90},
    },
]

policy(
    "spec_examples",
    "the design's example rules, a working-day limit and a rule without a limit",
    SPEC_RULES
    + [
        pr("plain-rule"),
        pr("leave-cap", {"measure": "leave", "max_working_days": 10, "calendar": "company"}),
    ],
    [CFO, SMALL, UPLOAD, SALARY, HOURS, SERIES, REFUND, LEAVE],
)
policy(
    "min_and_fractional_bounds",
    "min is the other inclusive op; a fractional bound keeps its digits",
    [
        pr("review-floor", {"measure": "amount", "min": "500 USD"}),
        pr("tip-cap", {"measure": "amount", "max": "250.75 USD"}),
    ],
    [cap("review-floor", "500", "USD", op="min"), cap("tip-cap", "250.75", "USD")],
)
policy(
    "window_across_midnight",
    "a daily window may cross midnight",
    [pr("night-batch", {"measure": "event", "daily": "22:00-02:00", "zone": "user"})],
    [NIGHT],
)
policy(
    "decimal_size_units",
    "units may be decimal",
    [pr("attach-cap", {"measure": "file_size", "max": "500 MB", "units": "decimal"})],
    [size("attach-cap", "500", "MB", "decimal")],
)
policy(
    "recurrence_min",
    "a recurrence count may have a min",
    [pr("min-sessions", {"measure": "occurrences", "min": "2"})],
    [count_cap("min-sessions", "2", op="min")],
)
policy("no_limits", "a policy without limit fields compiles to nothing", [pr("a"), pr("b")], [])
policy(
    "thirty_two_limit_rules",
    "32 limit rules are allowed; rules without a limit do not count",
    [pr("plain-1")]
    + [pr(f"cap-{i}", {"measure": "occurrences", "max": str(i + 1)}) for i in range(16)]
    + [pr("plain-2")]
    + [pr(f"cap-{i}", {"measure": "occurrences", "max": str(i + 1)}) for i in range(16, 32)],
    [count_cap(f"cap-{i}", str(i + 1)) for i in range(32)],
)
policy(
    "currency_limits_with_empty_rate_table",
    "money and rate limits load with no configured rate; only a call that converts refuses",
    [
        pr("cfo-approval", {"measure": "amount", "max": "10000 USD"}),
        pr("salary-band", {"measure": "base_salary", "max": "142000 USD", "per": "year"}),
    ],
    [CFO, SALARY],
    config={"rates": []},
)
policy(
    "limit_currency_without_quote_pair",
    "a GBP limit loads although no configured pair converts into GBP",
    [pr("x", {"measure": "amount", "max": "10000 GBP"})],
    [cap("x", "10000", "GBP")],
)
policy(
    "error_rule_not_object",
    "every rule is an object",
    [SPEC_RULES[0], "cfo-approval"],
    error="bad_policy",
)
policy(
    "error_rules_not_list",
    "the rules are a list",
    {"cfo-approval": SPEC_RULES[0]},
    error="bad_policy",
)
policy(
    "error_limit_not_object",
    "limit is an object, not a bound string",
    [pr("x", "10000 USD")],
    error="bad_limit_field",
)
policy(
    "error_limit_unknown_key",
    "an unknown key in limit is reported",
    [pr("x", {"measure": "amount", "max": "10000 USD", "inclusive": True})],
    error="bad_limit_field",
)
policy(
    "error_unknown_measure",
    "the measure is not one the tool schemas produce",
    [pr("x", {"measure": "tip", "max": "20 USD"})],
    error="unknown_measure",
)
policy(
    "error_unparseable_money_bound",
    "the bound's number is not a decimal",
    [pr("x", {"measure": "amount", "max": "ten thousand USD"})],
    error="bad_bound",
)
policy(
    "error_unparseable_window",
    "a daily window is HH:MM-HH:MM",
    [pr("x", {"measure": "event", "daily": "9am-5pm", "zone": "user"})],
    error="bad_bound",
)
policy(
    "error_fractional_count",
    "a recurrence bound is an integer",
    [pr("x", {"measure": "occurrences", "max": "12.5"})],
    error="bad_bound",
)
policy(
    "error_missing_units",
    "a size limit must say binary or decimal",
    [pr("x", {"measure": "file_size", "max": "2 GB"})],
    error="missing_units",
)
policy(
    "error_unknown_zone",
    "the zone table has no recipient entry",
    [pr("x", {"measure": "event", "daily": "09:00-17:00", "zone": "recipient"})],
    error="unknown_zone",
)
policy(
    "error_unknown_calendar",
    "no calendar named uk-bank is configured",
    [pr("x", {"measure": "leave", "max_working_days": 10, "calendar": "uk-bank"})],
    error="unknown_calendar",
)
policy(
    "error_too_many_limit_rules",
    "at most 32 rules declare a limit",
    [pr(f"cap-{i}", {"measure": "occurrences", "max": str(i + 1)}) for i in range(33)],
    error="too_many_limit_rules",
)


def check_coverage():
    """Every case id is unique, and every refusal code and policy error appears at least once."""
    ids = [c["id"] for c in CASES] + [p["id"] for p in POLICY]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate case id")
    refused = {c["expect"].get("code") for c in CASES}
    errors = {p["expect"].get("code") for p in POLICY}
    missing = [c for c in REFUSAL_CODES if c not in refused]
    missing += [c for c in POLICY_ERRORS if c not in errors]
    if missing:
        raise SystemExit(f"codes without a case: {missing}")


def main(argv):
    check_coverage()
    vectors = {
        "profile": PROFILE,
        "status": "Hand-written expectations. The Python reference (two identical copies, Kai and "
        "AgentArmor) and AgentArmor's native port must reproduce every case exactly. values are "
        "[fact name, value] pairs in fact order; a measure's value is a string, or a list of "
        "strings when its limits use several comparison keys. facts_without_note fixes every fact "
        "field except the note. note_includes lists, by fact index, strings the note must "
        "contain: trusted inputs the note states, as given. Other note wording is the "
        "implementer's.",
        "refusal_codes": list(REFUSAL_CODES),
        "policy_errors": list(POLICY_ERRORS),
        "cases": CASES,
        "policy_cases": POLICY,
    }
    out = HERE / "vectors.json"
    out.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    default = HERE.parents[2] / "train/kai/hostfacts/measures.py"
    reference = Path(argv[1]) if len(argv) > 1 else default
    pinned = hashlib.sha256(reference.read_bytes()).hexdigest() if reference.is_file() else None
    manifest = {
        "profile": PROFILE,
        "reference_python_sha256": pinned,
        "vectors_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": len(CASES),
        "policy_cases": len(POLICY),
        "copies": {
            "kai": [
                "train/kai/hostfacts/measures.py",
                "docs/contracts/armor-hostfacts-measures-v1/",
            ],
            "agentarmor": [
                "src/hostfacts/measures.py",
                "tests/fixtures/armor-hostfacts-measures-v1/",
            ],
        },
    }
    (HERE / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"{len(CASES)} cases, {len(POLICY)} policy cases -> {out}")


if __name__ == "__main__":
    main(sys.argv)

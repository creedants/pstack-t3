"""Re-exports names from _spend. Patch a name in the _spend module that reads it; patching it here does not reach callers in _spend."""
import json
from brigade_lib._spend.claude import _call_from, _claude_base, _claude_totals, _contained, _dedupe, _read_log, _thinking_tokens, _usage_count
from brigade_lib._spend.items import _dish_number, _items, _one_seat, _question, _rows, _seat_text, _stamp, _threads, _threads_by_item, _wall, _warn_shares, _worker_key, _reviewer_key
from brigade_lib._spend.model import ABSENT_WORDS, CLOSED, DIMENSIONS, DRIVERS, INDEX_NAME, MILLION, MODEL_MISSING, MODEL_NAME, NATIVE_ID, NOT_RECORDED, NOT_REQUESTED, OPEN, PLACES, PRICE_KEYS, PROVIDER_THREADS, PROVIDER_TURNS, SCHEMA, SEATS_DIFFER, SEAT_MISSING, SINCE, THREADS, TURN_FIELDS, UNREADABLE, DISH_ID, Item, Measure, _Bare, _Call, _Closed, _Counts, _Question, _Row, _Schema, _Store, _add, _goal_key, _item_key, _kind_key, _merge, _model_key, _total
from brigade_lib._spend.render import _dollars, _gap_suffix, _json_measure, _json_models, _json_report, _measure_text, _missing_threads, _model_line, _row_line, _row_models, _text
from brigade_lib._spend.tokens import _blank, _folded_tokens, _ledger_totals, _leads, _marks, _open_tokens, _prices, _probe, _provider_sql, _rate, _read_prices_and_tokens, _recorded_model, _recorded_models, _require_table, _rows_in, _searches, _thread_sql, _token_totals, _turn_sql, _turn_totals, _viable

def add_arguments(sub):
    parser = sub.add_parser(
        "spend",
        help=(
            "print wall time, rounds, send-backs, and threads from the store. It writes nothing\n"
            "\n"
            "In text, a wholly missing measure prints its absence words and no counts. In JSON, one "
            "absence word is that string, and two are an object of word to count. Without `--tokens` "
            "the text ends `tokens not requested. Pass --tokens to read them.` and JSON sets "
            "`threads_without_a_token_source` to `not requested`. Token totals need `--tokens`.\n"
            "\n"
            "A Claude model line prints `reasoning not recorded` when "
            "`usage.output_tokens_details.thinking_tokens` is missing, null, negative, a bool, or not an integer. "
            "A recorded count prints as a number, and 0 stays 0. Adding two "
            "model totals prints `not recorded` for reasoning when either total was not recorded. Output "
            "and dollars stay the recorded totals.\n"
            "\n"
            "Codex and OpenCode turn totals print under the thread's current model selection, so turns "
            "taken before a mid-run model switch count under the later model.\n"
            "\n"
            "`--by reviewer` groups whole items by the seat that reviewed them. Its token lines "
            "include the worker's and every other ledger role's usage for those items. An item "
            "reviewed by several seats appears under one joined key."
        ),
    )
    parser.add_argument("--by", choices=tuple(DIMENSIONS), default="item", help="what one row groups (default: item)")
    parser.add_argument("--item", help="only this dish, such as D12")
    parser.add_argument("--since", help="only dishes fired at or after this UTC date, as YYYY-MM-DD")
    parser.add_argument(
        "--json", action="store_true",
        help=(
            "print the same report as JSON. Wall time is in whole seconds. A mixed measure is an object "
            "with value for the counted total and open or not recorded for the number of items with that gap"
        ),
    )
    parser.add_argument("--tokens", action="store_true",
                        help="also read token totals. A model lists dollars only when prices.json prices it")
    parser.add_argument("--t3-home", help="T3 Code base directory for --tokens (default $T3CODE_HOME, else ~/.t3)")

def spend_text(restaurant, args):
    question = _question(args)
    store = _Store(restaurant)
    with restaurant.read_lock(create=False):
        items = _items(store, question)
    _warn_shares(store, items)
    prices, totals = _read_prices_and_tokens(restaurant.dir, items, question)
    rows = _rows(items, question.by)
    if question.as_json:
        return json.dumps(_json_report(question, rows, items, totals, prices), indent=2)
    return _text(question, rows, items, totals, prices)


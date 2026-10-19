import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from urllib.parse import quote, unquote

from tests.activity_tests.support import COORDINATOR, MARKERS, MOD, VIEWS, delegated, native, worker

def same(text):
    return text
def CLEAN(text):
    """The filter with no identifier and no path of a run."""
    return MOD["Privacy"]().clean(text)
def page_of(fixture, hours=3.0):
    store, t3, window = fixture.write().read(hours)
    return MOD["build_page"](store, t3, window, MOD["privacy_of"](store, t3))
def labels(page):
    return {group.item.id if group.item else None: [(row.depth, row.label) for row in group.rows] for group in page.groups}
def agent(thread, title="", parent=None, turns=(), delegation=None, provider="claudeAgent", model="model-a"):
    return MOD["Agent"](thread, parent, MOD["request_name"](thread), provider, model, title, tuple(turns), delegation)
def store_of(*units, name="kit", slug=("kit", "proj"), coordinators=(COORDINATOR,)):
    return MOD["Store"](name, frozenset(slug), coordinators, tuple(MOD["Unit"](ident, "in-progress", "", "", work, tuple(earlier), "") for ident, work, earlier in units))
def row(label, depth=0, status="done", spans=((0, 10, "done"),), open_seconds=None, stands_for=1, model="model-a"):
    bars = tuple(MOD["Span"](x, w, MOD["Status"](kind)) for x, w, kind in spans)
    return MOD["Row"](depth, label, model, MOD["Status"](status), 60, bars, open_seconds, stands_for)
def item(ident, in_flight=True, summary="summary", pr="", no_link=""):
    return MOD["Item"](ident, summary, "working" if in_flight else "merged", "go" if in_flight else "", pr, in_flight, no_link)
def group(of, *rows):
    return MOD["Group"](of, tuple(rows), len(rows), 0)
def page(*groups, items=(), coordinator=None, hidden=None, name="kit"):
    rows = [each for one in groups for each in one.rows]
    totals = MOD["Totals"](sum(each.open_seconds is not None for each in rows), sum(each.stands_for for each in rows), 0, 0)
    return MOD["Page"](name, MOD["Window"](0.0, 10800.0), totals, (("model-a", totals.agents),), coordinator, tuple(groups), tuple(items), hidden or MOD["Hidden"]())
def drawn(folded):
    return {one.item.id if one.item else None: [(each.depth, each.label, each.stands_for) for each in one.rows] for one in folded.groups}
DATA = re.compile(r"<script type=application/json id=d>(.*?)</script>", re.S)
PROGRAM = re.compile(r"<script>(const H=(\d+);.*)</script>$", re.S)
STORE_WORDS = re.compile(r"\b(dish|dishes|rail|86|pass|station|restaurant|fire|chef)\b", re.I)
STUB = """
const nodes = [];
const make = tag => ({
  tagName: tag.toUpperCase(), kids: [], style: {}, attrs: {}, className: '', own: '',
  get id() { return this.attrs.id || ''; },
  set id(value) { this.attrs.id = String(value); },
  appendChild(kid) { this.kids.push(kid); return kid; },
  setAttribute(name, value) { this.attrs[name] = String(value); },
  getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
  insertBefore(node, ref) {
    const from = this.kids.indexOf(node);
    if (from >= 0) this.kids.splice(from, 1);
    const at = ref ? this.kids.indexOf(ref) : -1;
    if (at < 0) this.kids.push(node); else this.kids.splice(at, 0, node);
    return node;
  },
  querySelector(sel) {
    const name = sel[0] === '.' ? sel.slice(1) : sel;
    const walk = (node) => {
      if ((node.className || '').split(' ').includes(name)) return node;
      for (const kid of node.kids || []) {
        const found = walk(kid);
        if (found) return found;
      }
      return null;
    };
    return walk(this);
  },
  set textContent(value) { this.kids = []; this.own = String(value); },
  get textContent() { return this.own; },
});
const page = make('div'), document = {
  getElementById: id => id === 'd' ? {textContent: DATA} : page,
  createElement: tag => { const node = make(tag); nodes.push(node); return node; },
};
"""
REPORT = """
console.log(JSON.stringify({
  texts: nodes.map(node => node.own).filter(Boolean),
  links: nodes.filter(node => node.href).map(node => [node.tagName, node.href, node.target, node.rel]),
  ids: nodes.map(node => node.attrs.id).filter(Boolean),
  classes: nodes.map(node => node.className),
  titles: nodes.filter(node => node.title).map(node => node.title),
  bars: nodes.filter(node => node.className.startsWith('b ')).map(node => [node.style.left, node.style.width]),
  one: nodes.filter(node => node.className === 'one').map(node => node.own),
}));
"""
HOSTILE = "x</script><img onerror=a(1)> \"q\" \\ & 'p'\t&lt;\u2028z"
HOSTILE_WRITTEN = "x&lt;/script&gt;&lt;img onerror=a(1)&gt; &quot;q&quot; &#92; &amp; 'p' &amp;lt; z"
HOSTILE_DRAWN = "x</script><img onerror=a(1)> \"q\" \\ & 'p' &lt; z"
def hostile():
    return page(group(item("D1 " + HOSTILE, summary=HOSTILE), row(HOSTILE, model="model " + HOSTILE)), name=HOSTILE)
def declarations(style):
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", style.replace("@media(max-width:520px){", ""))
    return [(selector, *declaration.split(":", 1)) for selectors, body in rules for selector in selectors.split(",") for declaration in body.split(";")]
def data_of(document):
    return json.loads(DATA.search(document).group(1))
def strings(value):
    if isinstance(value, str):
        return [value]
    return [text for each in value for text in strings(each)] if isinstance(value, list) else []
def agents_shown(data):
    return sum(line[7] if len(line) > 7 else 1 for _, lines in data["G"] for line in lines)
def tagged(text):
    return f"[{text}]"
def named(fixture):
    """A page whose every string from the store or from T3 holds one word in capitals that no other string of it holds."""
    fixture.meta["restaurant"] = "NAMEWORD kit"
    fixture.coordinator(model="coordinator-MODELWORD")
    fixture.unit("IDWORD", "in-progress", "SUMWORD of the page", thread=worker(1), pr="https://git.example/OWNERWORD/r/pull/7")
    fixture.unit("QUIETWORD", "queued", "IDLEWORD summary", pr="https://git.example/o/REPOWORD/pull/6")
    fixture.thread(worker(1), title="D7 worker", model="worker-FIRSTWORD", turns=(("running", 40, None),))
    child = delegated(worker(1), "helper-task")
    fixture.thread(child, title="PARENTWORD helper", model="child-SECONDWORD", parent=worker(1), turns=(("running", 30, None),))
    fixture.thread(native(1), title="LABELWORD reader", model="leaf-THIRDWORD", parent=child, delegation=("queued", 20, None))
    fixture.thread(delegated(COORDINATOR, "loose-task"), title="LOOSEWORD agent", model="loose-FOURTHWORD", parent=COORDINATOR, turns=(("failed", 25, 24),))
    return fixture
def ordinary(fixture):
    """A store, its agents, and their models named with ordinary words. Two workers' thread ids and every provider instance id are such words too."""
    fixture.meta["restaurant"] = "private auditor"
    fixture.coordinator(instance="queue")
    fixture.unit("queue", "in-progress", "queue src report", thread="review", task="queue", pr="https://git.example/queue/code/pull/451237")
    fixture.thread("review", title="queue worker", model="code", instance="private auditor", turns=(("running", 30, None),))
    fixture.thread(delegated(COORDINATOR, "queue"), title="queue", model="queue", instance="451237", parent=COORDINATOR, turns=(("running", 20, None),))
    fixture.unit("code", "in-review", "code review of the queue", thread="451237")
    fixture.thread("451237", title="code worker", provider="codex", model="private auditor", instance="code", turns=(("completed", 50, 40),))
    fixture.thread(delegated("451237", "code-review"), model="451237", parent="451237", turns=(("failed", 39, 38),))
    fixture.thread(delegated("451237", "src"), title="src", model="review", parent="451237", turns=(("running", 10, None),))
    fixture.unit("review", "queued", "review the src queue")
    fixture.unit("src", "blocked", "src code")
    fixture.unit("451237", "passed", "451237 private auditor", task="review")
    fixture.thread(delegated(COORDINATOR, "review"), model="src", parent=COORDINATOR, turns=(("queued", 5, None),))
    return fixture
def skill_section():
    return ("\n" + VIEWS.read_text()).split("\n## Agent activity\n", 1)[1].split("\n## ", 1)[0]
UUID_TEXT = "123e4567-e89b-12d3-a456-426614174000"
ORDINARY = ("queue", "code", "review", "src", "private auditor", "451237", "D7", "pstack-t3", "gpt-6.1-sol", "claude-sonnet-4-5-20250929", "kit-d7-audit-task", "0a1b2c3")
WORDS = ("NAMEWORD", "MODELWORD", "IDWORD", "SUMWORD", "OWNERWORD", "QUIETWORD", "IDLEWORD", "REPOWORD", "FIRSTWORD", "PARENTWORD", "SECONDWORD", "LABELWORD", "THIRDWORD", "LOOSEWORD", "FOURTHWORD")
REQUEST = "kit-d7-audit-task"
ODD = "T3homemarker_Session9"
BARE = "7ead0a2b-e89b-12d3-a456-426614174000"
ADDRESS = "acct-homemarker@example.test"
NEEDLES = (*MARKERS, "426614174000", "example.test", "secret")
ROW_FIELDS = ("model", "title", "title in a sentence")
LINK_FIELDS = ("pull request", "pull request owner", "pull request query", "pull request fragment", "pull request userinfo")
def painted(case, document):
    data, program = DATA.search(document).group(1), PROGRAM.search(document).group(1)
    script = case.fixture.root / "render.js"
    script.write_text(f"const DATA = {json.dumps(data)};{STUB}{program}{REPORT}")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "TZ": "UTC"})
    case.assertEqual((result.returncode, result.stderr), (0, ""), result.stderr)
    return json.loads(result.stdout)


class FilterCase(unittest.TestCase):
    def clean(self, text, identifiers=(), paths=()):
        return MOD["Privacy"](identifiers, paths).clean(text)

class NodeRender:
    def render(self, document, spoil=False):
        data, program = DATA.search(document).group(1), PROGRAM.search(document).group(1)
        if spoil:
            data = data.replace('"kit"', '"kat"', 1)
        script = self.fixture.root / "render.js"
        script.write_text(f"const DATA = {json.dumps(data)};{STUB}{program}{REPORT}")
        result = subprocess.run(["node", str(script)], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "TZ": "UTC"})
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return json.loads(result.stdout)


T3_REFUSAL = "activity: --out names a file inside T3 Code's directory or one of its database files; pass a path outside it"
STORE_REFUSAL = "activity: --out names a file inside the coordinator's store; pass a path outside it"

import argparse
import enum
import fcntl
import http.server
import json
import os
import re
import secrets
import socketserver
import sqlite3
import stat
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import quote, unquote, urlsplit


def joined_lines(source):
    return "".join(line.strip() for line in source.splitlines())


def squeezed(source):
    def tight(code):
        return re.sub(r"\s+", " ", re.sub(r"\s*([^\w\s$.])\s*", r"\1", code)).replace(";}", "}")

    return "".join(part if part.startswith("'") else tight(part) for part in re.split(r"('[^']*')", source.strip()))


STYLE_BASE = joined_lines("""
    #o{font:13px/1.4 var(--font-sans);color:var(--foreground)}
    #o a{color:inherit;text-decoration:none}
    h2{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;margin:18px 0 6px}
    h2,small,.stat span,.legend,.axis,.sum,.foot,.co,.chip{color:var(--muted-foreground)}
    .diff{font-weight:600}
    .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
    .stat,.now div,.strip>*{border:1px solid var(--border);border-radius:var(--radius);padding:6px 10px}
    .stat b{display:block;font-size:22px;line-height:1.1}
    .now div,.strip>*,.grp,.l{display:flex;gap:8px;align-items:baseline;min-width:0}
    .now{display:flex;flex-wrap:wrap;gap:6px}
    .mark{width:8px;height:8px;border-radius:50%;border:1px solid;box-sizing:border-box;align-self:center;flex:none}
    .pulse{border:0;background:var(--success)}
    .sum,.l span,.l small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .sum{flex:1;min-width:0;font-weight:400}
    .strip{display:flex;flex-wrap:wrap;gap:6px}
    .strip>*{max-width:100%;box-sizing:border-box}
    .strip .sum{flex:0 1 auto;max-width:200px}
    .legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11.5px;margin-bottom:6px}
    .dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;background:var(--c)}
    .tl{position:relative;--lab:230px}
    .axis{position:relative;height:16px;margin-left:var(--lab);font-size:10.5px}
    .axis i{position:absolute;width:5.5em;text-align:center;white-space:nowrap;font-style:normal}
    .grid{position:absolute;left:var(--lab);right:0;top:16px;bottom:0;pointer-events:none}
    .grid i{position:absolute;top:0;bottom:0;width:1px;background:var(--border)}
    .grp{position:relative;margin-top:8px;padding:3px 0;border-top:1px solid var(--border);background:var(--background);font-weight:600}
    .chip{font-size:10.5px;padding:1px 7px;border:1px solid;border-radius:99px;white-space:nowrap}
    .go,.live b{color:var(--success)}
    .info{color:var(--info)}
    .warn{color:var(--warning)}
    .bad,.alarm b,.diff{color:var(--destructive)}
    .row{display:grid;grid-template-columns:var(--lab) 1fr;align-items:center;height:19px}
    .l{padding-right:8px;font-size:12px}
    .l small{flex:none;max-width:50%}
    .d1{padding-left:14px}
    .d2{padding-left:28px}
    small{font-size:10.5px}
    .t{position:relative;height:100%}
    .b{position:absolute;top:4px;height:11px;min-width:3px;border-radius:3px;background:var(--c)}
    .run{background:repeating-linear-gradient(135deg,var(--c) 0 4px,transparent 4px 7px)}
    .q{background:transparent;border:1px solid var(--c);box-sizing:border-box}
    .stop{opacity:.45}
    .p0,.co{--c:var(--muted-foreground)}
    .p1{--c:var(--chart-1)}
    .p2{--c:var(--chart-2)}
    .p3{--c:var(--chart-3)}
    .p4{--c:var(--chart-4)}
    .p5{--c:var(--chart-5)}
    .p6{--c:var(--chart-6)}
    .f{--c:var(--destructive)}
    .foot{font-size:11px;margin-top:12px}
    .nb,.one{white-space:nowrap}
    .one{overflow:hidden;text-overflow:ellipsis}
    @media(max-width:520px){
    .tl{--lab:128px}
    .stats{grid-template-columns:repeat(2,1fr)}
    .axis i:nth-child(odd){display:none}
    .row{position:relative;height:30px;align-items:start}
    .l small{position:absolute;left:0;right:0;bottom:0;max-width:none;line-height:13px}
    .d1 small{left:14px}
    .d2 small{left:28px}
    }
""")

OVERFLOW_STYLE = joined_lines("""
    .p7{--c:var(--chart-7,#c24)}
    .p8{--c:var(--chart-8,#2a7)}
    .p9{--c:var(--chart-9,#36b)}
    .p10{--c:var(--chart-10,#e80)}
    .p11{--c:var(--chart-11,#93c)}
    .p12{--c:var(--chart-12,#0aa)}
""")


STYLE = STYLE_BASE + OVERFLOW_STYLE


RENDERER = squeezed("""
    const root = document.getElementById('o'), raw = document.getElementById('d').textContent;
    const add = (parent, tag, cls = '', text = '') => {
      const node = parent.appendChild(document.createElement(tag));
      node.className = cls;
      node.textContent = text;
      return node;
    };
    const link = (p, t, u) => {
      const s = u.startsWith('https://'), n = add(p, s ? 'a' : 'span', '', t);
      if (s) {
        n.href = u;
        n.target = '_blank';
        n.rel = 'noopener';
      }
      return n;
    };
    const chip = (p, i) => {
      const it = D.I[i], b = add(p, 'div');
      if (it[6]) add(b, 'b', 'chip', it[6]);
      if (D.G.some((g) => g[0] === i) || (D.L || []).concat(D.E || []).some(([a]) => a === i)) {
        add(b, 'a', 'one', it[0]).href = '#g' + i;
      } else {
        const n = link(b, '', it[4]);
        add(n, 'b', 'one', it[0]);
        add(n, 'span', 'sum', it[1]);
      }
      add(b, 'b', 'chip ' + it[3], it[2]);
      return b;
    };
    let sum = 2166136261, D = null;
    for (let i = 0; i < raw.length; i++) sum = Math.imul(sum ^ raw.charCodeAt(i), 16777619) >>> 0;
    if (sum !== H) add(root, 'p', 'diff', 'This copy differs from what the tool wrote. Run the command again.');
    const plain = (key, value) => typeof value != 'string' ? value : [['&lt;', 60], ['&gt;', 62], ['&quot;', 34], ['&#92;', 92], ['&amp;', 38]]
      .reduce((text, [entity, code]) => text.split(entity).join(String.fromCharCode(code)), value);
    try { D = JSON.parse(raw, plain); } catch (error) {}
    if (D && D.v === 1) {
      const [start, length, span] = D.w, kinds = ['', 'run', 'f', 'stop', 'p0', 'q'];
      const clock = (t, day) => new Date(t * 1000).toLocaleString([], day ? {weekday: 'short', hour: 'numeric'} : {hour: 'numeric', minute: '2-digit'});
      const stats = add(root, 'div', 'stats');
      ['running now', 'agents, last ' + span, 'sub-agents', 'failed'].forEach((label, i) => {
        const box = add(stats, 'div', 'stat' + (i ? i > 2 && D.n[i] ? ' alarm' : '' : ' live'));
        add(box, 'b', '', D.n[i]);
        add(box, 'span', '', label);
      });
      const open = [[], []];
      for (const [index, rows] of D.G) {
        const above = [];
        for (const r of rows) {
          above[r[0]] = r[1];
          if (r[8] != null) open[r[4]].push([index < 0 ? '' : D.I[index][0], r[1], D.M[r[2]], r[0] ? above[r[0] - 1] : '', r[8]]);
        }
      }
      ['Running now', 'Queued'].forEach((heading, i) => {
        if (open[i].length) {
          add(root, 'h2', '', heading);
          const list = add(root, 'div', 'now');
          for (const [item, label, , parent, elapsed] of open[i]) {
            const line = add(list, 'div');
            add(line, 'i', i ? 'mark' : 'mark pulse');
            if (item) add(line, 'b', 'one', item);
            add(line, 'b', 'one', label);
            if (parent) {
              add(line, 'span', '', ' · under ');
              add(line, 'span', '', parent);
            }
            add(line, 'span', '', elapsed);
          }
        }
      });
      add(root, 'h2', '', 'Timeline');
      const legend = add(root, 'div', 'legend');
      for (const [cls, text] of [...D.P.map(p => ['p' + p[1], p[0] + ' ' + p[2]]), ['f', 'failed']]) {
        const entry = add(legend, 'span');
        add(entry, 'i', 'dot ' + cls);
        add(entry, 'span', '', text);
      }
      const lanes = add(root, 'div', 'tl'), axis = add(lanes, 'div', 'axis'), grid = add(lanes, 'div', 'grid');
      const step = 60 * [15, 30, 60, 180, 720, 1440][[2, 6, 12, 24, 72].filter(hours => length > hours * 3600).length];
      const day = new Date(start * 1000);
      day.setHours(0, 0, 0, 0);
      for (let t = day / 1000; t < start + length; t += step) {
        const x = (t - start) / length * 100;
        if (x > 4 && x < 97) {
          add(axis, 'i', '', clock(t, length > 86400)).style.left = 'min(' + x + '% - 2.75em,100% - 5.5em)';
          add(grid, 'i').style.left = x + '%';
        }
      }
      const lane = (cls, depth, label, model, status, time, spans) => {
        const row = add(lanes, 'div', 'row ' + cls), name = add(row, 'div', 'l d' + depth), track = add(row, 'div', 't');
        add(name, 'span', '', label).title = label;
        add(name, 'small', '', D.M[model]).title = D.M[model] || '';
        row.title = D.S[status] + ' · ' + time;
        for (let i = 0; i < spans.length; i += 3) {
          const bar = add(track, 'i', 'b ' + kinds[spans[i + 2]]);
          bar.style.left = 'min(' + spans[i] / 10 + '%,100% - 3px)';
          bar.style.width = spans[i + 1] / 10 + '%';
        }
      };
      if (D.k) lane('co', 0, 'coordinator', ...D.k);
      for (const [index, rows] of D.G) {
        const head = add(lanes, 'div', 'grp'), item = D.I[index];
        if (index >= 0) head.id = 'g' + index;
        if (item) {
          link(head, item[0], item[4]).className = 'one';
          add(head, 'span', 'sum', item[1]);
          add(head, 'b', 'chip ' + item[3], item[2]);
        } else add(head, 'span', '', 'Not tied to a work item');
        for (const r of rows) lane('p' + r[3], r[0], r[1], r[2], r[4], r[5], r[6]);
      }
      const strip = D.I.map((it, i) => [i, it]).filter(([, it]) => it[5]);
      if (strip.length) {
        add(root, 'h2', '', 'Work items in flight');
        const box = add(root, 'div', 'strip');
        for (const [i] of strip) chip(box, i);
      }
      const foot = add(root, 'div', 'foot');
      add(foot, 'span', '', 'As of ' + clock(start + length) + ' for ');
      add(foot, 'span', '', D.c);
      add(foot, 'span', '', '. Bars show turn or delegation intervals and may join across gaps. Striped bars include running intervals. Outlined bars include queued intervals. Faded bars include stopped intervals.');
      for (const note of D.N) {
        const line = add(foot, 'div');
        note.split(/(--[a-z-]+)/).forEach((part, i) => add(line, 'span', i % 2 ? 'nb' : '', part));
      }
    }
""")


COLLAPSE_SCRIPT = squeezed("""
    if (D && D.v === 1 && D.C && D.C.some((c) => c)) {
      const lanes = root.querySelector('.tl'), groups = [];
      let cur = null;
      for (const node of [...(lanes.children || lanes.kids)]) {
        if (node.className.split(' ').includes('grp')) { cur = {head: node, rows: []}; groups.push(cur); }
        else if (cur && node.className.split(' ').includes('row')) cur.rows.push(node);
      }
      groups.forEach((group, gpos) => {
        if (!D.C[gpos]) return;
        const head = group.head, ns = group.rows;
        head.setAttribute('data-collapsed', '1');
        for (const n of ns) n.style.display = 'none';
        head.onclick = (e) => {
          if (e.target.closest('a,summary,details')) return;
          const detail = head.querySelector('details'), open = detail && detail.open;
          for (const n of ns) n.style.display = n.style.display ? '' : (n.className.includes('earlier') && !open ? 'none' : '');
        };
      });
    }
""")


STRIP_CSS = ".strip>.grp{flex:1 0 100%;border:0;background:transparent;border-radius:0;padding:2px 0}"


LEDGER_CSS = ".tl>.grp{flex-wrap:wrap}.ldg{flex:1 0 100%;margin:0;padding:0;font-weight:400}.ldg span{display:block;height:16px;line-height:16px;overflow:hidden;white-space:nowrap;font-size:11.5px;color:var(--muted-foreground)}"


ATTEMPT_CSS = ".row.earlier{display:none}.ldg details{margin:0;padding:0}.ldg summary{display:block;height:16px;line-height:16px;overflow:hidden;white-space:nowrap;font-size:11.5px;color:var(--muted-foreground);cursor:pointer}"


LEDGER_HOOK = """
    if (D && D.v === 1 && ((D.L && D.L.length) || (D.E && D.E.length))) {
      const children = (node) => node.kids || [...node.children];
      const hasEarlier = (node) => ((node.className || '').split(' ').indexOf('earlier') >= 0);
      const addEarlier = (node) => { if (!hasEarlier(node)) node.className = (node.className ? node.className + ' ' : '') + 'earlier'; };
      const dropEarlier = (node) => { node.className = (node.className || '').split(' ').filter((c) => c && c !== 'earlier').join(' '); };
      const attemptMap = (typeof window !== 'undefined' && window.attemptState) || {};
      if (typeof window !== 'undefined' && !window.attemptState) window.attemptState = attemptMap;
      const byCurrent = new Map((D.L || []).map(([index, lines]) => [index, lines]));
      const byFold = new Map((D.E || []).map(([index, summary, payload]) => [index, [summary, payload]]));
      const byEarlier = new Map((D.R || []).map(([gpos, idxs]) => [gpos, idxs]));
      const lanes = root.querySelector('.tl');
      const groups = D.G || [];
      const heads = children(lanes).filter((node) => (node.className || '').split(' ').includes('grp'));
      const domGroups = [];
      let cur = null;
      for (const node of children(lanes)) {
        const cls = (node.className || '').split(' ');
        if (cls.indexOf('grp') >= 0) { cur = {head: node, rows: []}; domGroups.push(cur); }
        else if (cur && cls.indexOf('row') >= 0) cur.rows.push(node);
      }
      const attach = (head, gpos, itemIndex) => {
        const current = byCurrent.get(itemIndex) || [];
        const folded = byFold.get(itemIndex);
        if (!current.length && !folded) return;
        const block = document.createElement('div');
        block.className = 'ldg';
        head.appendChild(block);
        const itemId = (D.I[itemIndex] || [])[0] || String(itemIndex);
        let details = null;
        if (folded) {
          const summary = folded[0], payload = folded[1] || [];
          details = document.createElement('details');
          const sum = document.createElement('summary');
          sum.textContent = summary;
          details.appendChild(sum);
          for (const phrase of payload) {
            const span = document.createElement('span');
            span.textContent = phrase;
            details.appendChild(span);
          }
          block.appendChild(details);
          details.setAttribute('data-item', itemId);
          const open = !!attemptMap[itemId];
          details.open = open;
          const rows = (gpos != null && domGroups[gpos]) ? domGroups[gpos].rows : [];
          const idxs = (gpos != null && byEarlier.get(gpos)) || [];
          const earlierRows = idxs.map((ri) => rows[ri]).filter(Boolean);
          if (open) { for (const r of earlierRows) dropEarlier(r); }
          else { for (const r of earlierRows) addEarlier(r); }
          details.ontoggle = () => {
            const isOpen = !!details.open;
            attemptMap[itemId] = isOpen;
            for (const r of earlierRows) { if (isOpen) dropEarlier(r); else addEarlier(r); }
            if (typeof window !== 'undefined' && window.refreshAttempt) window.refreshAttempt(itemId);
          };
        }
        for (const phrase of current) {
          const span = document.createElement('span');
          span.textContent = phrase;
          block.appendChild(span);
        }
      };
      const placed = new Set();
      if (heads.length === groups.length) {
        groups.forEach((group, at) => {
          if (byCurrent.has(group[0]) || byFold.has(group[0])) { attach(heads[at], at, group[0]); placed.add(group[0]); }
        });
      }
      const seen = new Set(placed);
      for (const index of [...byCurrent.keys(), ...byFold.keys()]) {
        if (seen.has(index)) continue;
        seen.add(index);
        const item = D.I[index];
        const head = add(lanes, 'div', 'grp');
        head.id = 'g' + index;
        link(head, item[0], item[4]).className = 'one';
        add(head, 'span', 'sum', item[1]);
        add(head, 'b', 'chip ' + item[3], item[2]);
        attach(head, null, index);
      }
    }
"""


STRIP_HOOK = squeezed("""
    if (D && D.v === 1 && D.F && D.F.length) {
      const box = add(root, 'div', 'strip');
      for (const [line, , indexes] of D.F) {
        add(box, 'div', 'grp', line);
        for (const index of indexes) chip(box, index);
      }
      const foot = root.querySelector('.foot'), title = add(root, 'h2', '', 'Work items in flight');
      root.insertBefore(title, foot);
      root.insertBefore(box, foot);
    }
""")

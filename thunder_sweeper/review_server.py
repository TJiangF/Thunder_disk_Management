"""Local dashboard: WizTree-style treemap + category filter + review/delete.

Serves a single page with:
  * 文件管理  – squarified treemap of all files, drill-down + table
  * 视频审核  – thumbnail cards (the previous review flow)
  * 待删除    – current selection, submit to selections.json

Endpoints: /  /thumb/<id>/<n>  /play/<id>  /mark  /submit
"""

from __future__ import annotations

import colorsys
import errno
import hashlib
import json
import socketserver
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlparse

from . import categories, classify, dedupe, organize, util


class _FastHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer without the blocking reverse-DNS lookup on bind."""

    allow_reuse_address = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>迅雷云盘管家</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #14161a; color: #e6e8eb;
         font: 13px/1.5 -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; }
  header { position: sticky; top: 0; z-index: 30; background: rgba(20,22,26,.96);
           backdrop-filter: blur(8px); border-bottom: 1px solid #2a2e35; padding: 10px 16px; }
  .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  h1 { font-size: 15px; margin: 0 10px 0 0; font-weight: 700; }
  .tabs { display: flex; gap: 4px; }
  .tab { padding: 6px 14px; border-radius: 8px; cursor: pointer; color: #9aa4b2;
         border: 1px solid transparent; user-select: none; }
  .tab.active { background: #232833; color: #fff; border-color: #3a3f47; }
  button { background: #2a2e35; color: #e6e8eb; border: 1px solid #3a3f47; border-radius: 8px;
           padding: 6px 12px; font-size: 13px; cursor: pointer; }
  button:hover { background: #343a42; }
  button.primary { background: #2f6fed; border-color: #2f6fed; color: #fff; font-weight: 600; }
  button.primary:hover { background: #3b7bf5; }
  .stat { color: #9aa4b2; }
  .stat b { color: #f0b429; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px;
          border: 1px solid #3a3f47; cursor: pointer; user-select: none; color: #cbd2da; font-size: 12px; }
  .chip .dot { width: 9px; height: 9px; border-radius: 50%; }
  .chip.off { opacity: .35; }
  main { padding: 14px 16px 60px; }
  .crumb { color: #9aa4b2; margin-bottom: 8px; }
  .crumb a { color: #4d9bff; cursor: pointer; text-decoration: none; }
  .crumb a:hover { text-decoration: underline; }
  /* treemap */
  .tm { position: relative; width: 100%; height: 66vh; min-height: 360px; background: #0f1115;
        border: 1px solid #2a2e35; border-radius: 10px; overflow: hidden; }
  .tm-node { position: absolute; box-sizing: border-box; border: 1px solid #0f1115; overflow: hidden;
             cursor: pointer; color: #0b0d10; }
  .tm-node.folder { background: #39414d; color: #dfe4ea; }
  .tm-node:hover { filter: brightness(1.15); }
  .tm-node.selected { outline: 2px solid #fff; outline-offset: -2px; z-index: 6; }
  .tm-label { position: absolute; left: 0; top: 0; right: 0; padding: 2px 4px; font-size: 11px;
              pointer-events: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #232a33; }
  th { color: #9aa4b2; font-weight: 500; cursor: pointer; user-select: none; }
  tr.folder td { color: #dfe4ea; cursor: pointer; }
  tr.folder:hover td, tr.file:hover td { background: #1b1f26; }
  tr.file.picked td { background: #23303f; }
  .cat { display: inline-block; padding: 0 6px; border-radius: 6px; font-size: 11px; color: #0b0d10; }
  .play { color: #4d9bff; text-decoration: none; margin-left: 8px; font-size: 12px; }
  .play:hover { text-decoration: underline; }
  /* review cards */
  .cards { display: grid; grid-template-columns: 1fr; gap: 14px; }
  .card { background: #1b1e24; border: 1px solid #2a2e35; border-radius: 12px; overflow: hidden;
          scroll-margin-top: 90px; position: relative; }
  .card-cat { position: absolute; top: 8px; right: 10px; z-index: 4; }
  .card .head { padding-right: 150px; }
  .card.marked { border-color: #f0b429; box-shadow: 0 0 0 1px #f0b429 inset; }
  .card.resume { border-color: #2f6fed; box-shadow: 0 0 0 2px #2f6fed inset; }
  .head { display: flex; gap: 10px; padding: 10px 12px; align-items: flex-start; }
  .head input { width: 18px; height: 18px; margin-top: 2px; accent-color: #f0b429; }
  .meta { min-width: 0; flex: 1; }
  .name { font-weight: 600; word-break: break-all; }
  .sub { color: #9aa4b2; font-size: 12px; margin-top: 2px; word-break: break-all; }
  .badge { display: inline-block; background: #2a2e35; border-radius: 6px; padding: 1px 6px;
           margin-right: 6px; font-size: 11px; color: #cbd2da; }
  .mark { font-size: 11px; padding: 3px 8px; margin-left: 8px; }
  .thumbs { display: grid; grid-template-columns: repeat(4, 1fr); gap: 3px; background: #000; }
  .thumbs img { width: 100%; aspect-ratio: 16/9; object-fit: cover; display: block; cursor: zoom-in; }
  .empty { color: #6b7280; font-size: 12px; padding: 0 12px 12px; }
  /* selected list */
  .sel-row { display: flex; gap: 10px; align-items: center; padding: 8px 4px; border-bottom: 1px solid #232a33; }
  .sel-row .grow { flex: 1; min-width: 0; word-break: break-all; }
  #resume-bar { display: none; background: #182338; color: #9cc4ff; font-size: 12px;
                padding: 6px 16px; border-bottom: 1px solid #2a3a55; }
  #lightbox { position: fixed; inset: 0; background: rgba(0,0,0,.9); display: none;
              align-items: center; justify-content: center; z-index: 50; cursor: zoom-out; }
  #lightbox img { max-width: 96vw; max-height: 96vh; }
  #modal { position: fixed; inset: 0; background: rgba(0,0,0,.92); display: none;
           align-items: center; justify-content: center; z-index: 60; }
  #modal video { width: min(92vw, 1280px); max-height: 88vh; background: #000; outline: none; }
  #modal .close { position: fixed; top: 14px; right: 22px; font-size: 26px; color: #fff; cursor: pointer; }
  #confirm { position: fixed; inset: 0; background: rgba(0,0,0,.85); display: none;
             align-items: center; justify-content: center; z-index: 65; }
  .confirm-box { background: #1b1e24; border: 1px solid #3a3f47; border-radius: 12px;
                 width: min(760px, 92vw); max-height: 84vh; display: flex; flex-direction: column; }
  .confirm-box h3 { margin: 0; padding: 14px 16px; border-bottom: 1px solid #232a33; font-size: 15px; }
  .confirm-body { padding: 12px 16px; overflow: auto; }
  .confirm-actions { padding: 12px 16px; border-top: 1px solid #232a33; display: flex;
                     justify-content: flex-end; gap: 8px; }
  .danger { background: #c0392b; border-color: #c0392b; color: #fff; font-weight: 600; }
  .danger:hover { background: #d64535; }
  #toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: #232833;
           border: 1px solid #3a3f47; color: #e6e8eb; padding: 10px 18px; border-radius: 10px;
           z-index: 80; display: none; box-shadow: 0 8px 24px rgba(0,0,0,.5); max-width: 80vw; }
  #review-nav { position: fixed; right: 16px; bottom: 16px; z-index: 45; display: flex; gap: 6px;
                align-items: center; background: rgba(28,32,40,.96); border: 1px solid #3a3f47;
                border-radius: 10px; padding: 8px 10px; box-shadow: 0 8px 24px rgba(0,0,0,.5); }
  #review-nav input { width: 72px; background: #0f1115; color: #e6e8eb; border: 1px solid #3a3f47;
                      border-radius: 6px; padding: 4px 6px; }
  body.selecting { user-select: none; }
  body.selecting #tbody tr { cursor: default; }
  details { margin: 10px 0; }
  details > summary { cursor: pointer; padding: 6px 2px; color: #cbd2da; font-weight: 600; }
  #tip { position: fixed; z-index: 70; display: none; background: #0b0d10; border: 1px solid #3a3f47;
         color: #e6e8eb; padding: 6px 9px; border-radius: 8px; font-size: 12px; max-width: 460px;
         pointer-events: none; box-shadow: 0 6px 20px rgba(0,0,0,.5); }
  .hidden { display: none !important; }
  .rules-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 12px; }
  .rules-grid label { display: block; color: #cbd2da; }
  .rules-grid textarea { width: 100%; height: 220px; margin-top: 4px; background: #0f1115; color: #e6e8eb;
                         border: 1px solid #2a2e35; border-radius: 8px; padding: 8px; font: 12px/1.5 ui-monospace, Menlo, monospace;
                         resize: vertical; }
  #rule-overrides { background: #0f1115; color: #e6e8eb; border: 1px solid #2a2e35; border-radius: 8px;
                    padding: 8px; font: 12px/1.5 ui-monospace, Menlo, monospace; }
  code { background: #232833; padding: 0 4px; border-radius: 4px; }
  .filter-drop { position: relative; display: inline-block; }
  .filter-menu { position: absolute; display: none; top: 100%; left: 0; background: #1b1e24;
                 border: 1px solid #3a3f47; border-radius: 8px; padding: 4px; z-index: 60;
                 min-width: 180px; }
  .fitem { position: relative; padding: 6px 10px; cursor: pointer; display: flex;
           justify-content: space-between; align-items: center; gap: 12px; white-space: nowrap;
           border-radius: 6px; }
  .fitem:hover { background: #2a2e35; }
  .fitem.sel { background: #23303f; box-shadow: inset 0 0 0 1px #2f6fed; }
  .fitem .check { color: #4d9bff; margin-left: 8px; font-weight: 700; }
  .fitem .submenu { display: none; position: absolute; left: 100%; top: -6px; background: #1b1e24;
                    border: 1px solid #3a3f47; border-radius: 8px; padding: 4px; min-width: 160px; }
  .fitem:hover > .submenu { display: block; }
  .fitem .arrow { color: #9aa4b2; margin-left: 8px; }
  .catsel { background: #0f1115; color: #e6e8eb; border: 1px solid #3a3f47; border-radius: 6px;
            font-size: 11px; padding: 1px 4px; }
</style>
</head>
<body>
<header>
  <div class="row">
    <h1>迅雷云盘管家</h1>
    <div class="tabs">
      <div class="tab active" data-tab="files" onclick="setTab('files')">文件管理</div>
      <div class="tab" data-tab="dedupe" onclick="setTab('dedupe')">重复去重</div>
      <div class="tab" data-tab="organize" onclick="setTab('organize')">整理</div>
      <div class="tab" data-tab="review" onclick="setTab('review')">视频审核</div>
      <div class="tab" data-tab="selected" onclick="setTab('selected')">待删除 <span id="selcount">0</span></div>
      <div class="tab" data-tab="rules" onclick="setTab('rules')">分类设置</div>
    </div>
    <span style="flex:1"></span>
    <span class="stat" id="stat"></span>
    <button onclick="selectAllCurrent()">全选本目录</button>
    <button onclick="clearSel()">清空选择</button>
    <button class="primary" onclick="submitSel()">提交删除</button>
  </div>
  <div class="row" style="margin-top:8px">
    <span class="stat">筛选：</span>
    <div class="filter-drop">
      <button id="filter-btn" onclick="toggleFilterMenu(event)">全部分类 ▾</button>
      <div id="chips" class="filter-menu"></div>
    </div>
    <span style="flex:1"></span>
    <span class="stat">批量分类：</span>
    <select id="batch-cat" style="background:#0f1115;color:#e6e8eb;border:1px solid #3a3f47;border-radius:6px;padding:4px 8px"></select>
    <button onclick="applyBatchCategory()">应用到选中</button>
    <button onclick="selectAllVisible()">选中所有可见</button>
  </div>
</header>
<div id="resume-bar"></div>
<main>
  <section id="view-dedupe">
    <div class="row" style="margin-bottom:10px">
      <button onclick="loadDedupe(true)">重新扫描</button>
      <button onclick="markAllRedundant()">每组保留最大，其余标记</button>
      <button onclick="clearSel()">清空选择</button>
      <button class="danger" onclick="executeDelete()">执行删除（移入回收站）</button>
      <span id="dedupe-msg" class="stat"></span>
    </div>
    <div id="dedupe-list"><p class="stat">加载中…</p></div>
  </section>
  <section id="view-organize" class="hidden">
    <div class="row" style="margin-bottom:10px">
      <button onclick="loadOrganize(true)">重新生成方案</button>
      <span class="stat">预览；勾选下面选项并点“执行”才会真正改动云盘。</span>
    </div>
    <div class="row" style="margin-bottom:10px">
      <label class="stat"><input type="checkbox" id="org-apply" checked> 执行移动</label>
      <label class="stat"><input type="checkbox" id="org-del"> 删除空的源文件夹</label>
      <label class="stat"><input type="checkbox" id="org-junk"> 清理垃圾文件夹</label>
      <label class="stat"><input type="checkbox" id="org-other"> 含“成人-其他”</label>
      <span class="stat">限量</span>
      <input id="org-limit" type="number" min="1" placeholder="全部"
             style="width:80px;background:#0f1115;color:#e6e8eb;border:1px solid #3a3f47;border-radius:6px;padding:4px 8px">
      <button class="danger" onclick="runOrganize()">执行</button>
      <span id="org-msg" class="stat"></span>
    </div>
    <div id="organize-body"><p class="stat">加载中…</p></div>
  </section>
  <section id="view-files" class="hidden">
    <div class="crumb" id="crumb"></div>
    <div class="tm" id="tm"></div>
    <table id="tbl"><thead><tr>
      <th style="width:34px"></th><th data-sort="name">名称</th>
      <th data-sort="size" style="width:110px">大小</th>
      <th style="width:90px">分类</th><th style="width:70px"></th>
    </tr></thead><tbody id="tbody"></tbody></table>
  </section>
  <section id="view-review" class="hidden">
    <div class="row" style="margin-bottom:10px">
      <span class="stat">截图：</span>
      <input id="shotsN" type="number" value="100" min="1"
             style="width:90px;background:#0f1115;color:#e6e8eb;border:1px solid #3a3f47;border-radius:6px;padding:5px 8px">
      <button onclick="runShots('more', parseInt(document.getElementById('shotsN').value)||100)">继续截图 N 个</button>
      <button onclick="runShots('all')">全部截图</button>
      <span class="stat" style="margin-left:10px">并发</span>
      <input id="shotsW" type="number" value="3" min="1" max="8"
             style="width:56px;background:#0f1115;color:#e6e8eb;border:1px solid #3a3f47;border-radius:6px;padding:5px 8px">
      <span class="stat" style="margin-left:12px">排序：</span>
      <select id="review-sort" onchange="renderReview()"
              style="background:#0f1115;color:#e6e8eb;border:1px solid #3a3f47;border-radius:6px;padding:5px 8px">
        <option value="size-desc">大小：从大到小（默认）</option>
        <option value="size-asc">大小：从小到大</option>
        <option value="name">名称</option>
        <option value="category">分类</option>
        <option value="duration-desc">时长</option>
      </select>
      <span id="shots-msg" class="stat"></span>
    </div>
    <div class="cards" id="cards"></div>
  </section>
  <section id="view-selected" class="hidden">
    <p class="stat" id="selinfo"></p>
    <div id="sellist"></div>
  </section>
  <section id="view-rules" class="hidden">
    <details open><summary>分类树（点每行「关键词」展开编辑；匹配时子分类优先，逐级向上）</summary>
      <div id="cat-editor"></div>
      <div style="margin-top:8px"><button onclick="addCat(null)">＋ 新增顶级分类</button></div>
    </details>
    <details><summary>最高优先级覆盖（每行 <code>关键字=分类</code>，分类名/ID 均可）</summary>
      <textarea id="rule-overrides" spellcheck="false" style="width:100%;height:120px;margin-top:8px"></textarea>
      <div style="margin-top:8px"><button class="primary" onclick="saveOverrides()">保存覆盖</button></div>
    </details>
    <div style="margin-top:12px">
      <button class="primary" onclick="reclassify()">按当前规则重新分类</button>
      <button onclick="resetClassify()">重置全部分类（含手动）</button>
      <span id="rule-msg" class="stat" style="margin-left:8px"></span>
    </div>
  </section>
</main>
<div id="tip"></div>
<div id="toast"></div>
<div id="review-nav" class="hidden">
  <button onclick="reviewGoto(reviewTopIndex()-1)">◀</button>
  <span id="review-pos" class="stat">0 / 0</span>
  <button onclick="reviewGoto(reviewTopIndex()+1)">▶</button>
  <input id="review-jump" type="number" min="1" placeholder="序号">
  <button onclick="reviewJump()">跳转</button>
  <span id="review-mark" class="stat">标记 —</span>
  <button onclick="reviewGotoMark()">↩ 跳标记</button>
</div>
<div id="lightbox" onclick="this.style.display='none'"><img id="lightbox-img"></div>
<div id="modal" onclick="if(event.target===this)closePlayer()">
  <span class="close" onclick="closePlayer()">✕</span>
  <video id="player-video" controls playsinline></video>
</div>
<div id="confirm" onclick="if(event.target===this)closeConfirm()">
  <div class="confirm-box">
    <h3 id="confirm-title"></h3>
    <div class="confirm-body" id="confirm-body"></div>
    <div class="confirm-actions">
      <button onclick="closeConfirm()">取消</button>
      <button class="danger" id="confirm-ok">确认</button>
    </div>
  </div>
</div>
<script>
const VIDEOS = __VIDEOS_JSON__;
let LABELS = __LABELS_JSON__;
let COLORS = __COLORS_JSON__;
let CATEGORIES = __CATEGORIES_JSON__;
let CAT_DEFAULTS = __CAT_DEFAULTS_JSON__;
const RESUME_INDEX = __RESUME_INDEX__;
const RESUME_ID = __RESUME_ID__;
const RULES = __RULES_JSON__;
const INITIAL_SELECTED = __SELECTED_JSON__;

let CATS = CATEGORIES.map(c => c.id);
let filterCat = null;                // null = all
const selected = new Set();          // file ids
const selectedFolders = new Set();   // folder paths ("/a/b")
const byId = Object.fromEntries(VIDEOS.map(v => [v.id, v]));
(INITIAL_SELECTED || []).forEach(id => { if (byId[id]) selected.add(id); });
let tab = 'files';                    // folder segments
let curPath = [];                     // folder segments
let sortKey = 'size', sortAsc = false;

/* ---------------- helpers ---------------- */
function fmtSize(n) {
  const u = ['B','KB','MB','GB','TB']; let i = 0; n = n || 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
}
function fmtDur(s) {
  if (!s) return '-';
  s = Math.floor(s); const h = Math.floor(s/3600), m = Math.floor(s%3600/60), ss = s%60;
  return h ? h+':'+String(m).padStart(2,'0')+':'+String(ss).padStart(2,'0')
           : m+':'+String(ss).padStart(2,'0');
}
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function catOf(v){ return v.category || 'unknown'; }
function catParent(id) { const n = CATEGORIES.find(c => c.id === id); return n ? n.parent : null; }
function isDescOrSelf(cat, target) { let x = cat; while (x) { if (x === target) return true; x = catParent(x); } return false; }
function visible(v){ return !filterCat || isDescOrSelf(catOf(v), filterCat); }
function catSelect(v){
  const auto = v.auto_category || catOf(v);
  const cur = v.manual ? catOf(v) : '';
  const autoNode = CATEGORIES.find(c => c.id === auto);
  const autoPath = autoNode && autoNode.path ? autoNode.path.join(' / ') : (LABELS[auto] || auto);
  let h = '<select class="catsel" onclick="event.stopPropagation()" onchange="setManual(\'' + v.id + '\', this.value)">';
  h += '<option value=""' + (cur===''?' selected':'') + '>自动分类-' + esc(autoPath) + '</option>';
  for (const c of CATEGORIES) {
    const path = (c.path || [c.name]).join(' / ');
    h += '<option value="' + c.id + '"' + (cur===c.id?' selected':'') + '>' + esc(path) + '</option>';
  }
  h += '</select>';
  if (v.manual) h += ' <span class="badge">手动</span>';
  return h;
}
async function setManual(id, value) {
  try {
    const r = await fetch('/manual', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ id, category: value || null })});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '失败');
    const v = byId[id];
    if (v) { v.category = j.category; v.manual = j.manual; if (j.auto_category) v.auto_category = j.auto_category; }
    renderChips(); renderStat();
    if (tab === 'files') renderFiles();
    if (tab === 'review') renderReview();
  } catch (e) { alert('设置分类失败：' + e); }
}

/* ---------------- tree ---------------- */
const root = { name:'全部', segs:[], children:{}, files:[], size:0, parent:null };
const nodesByPath = { '': root };
function buildTree() {
  for (const v of VIDEOS) {
    const segs = (v.path || '/').split('/').filter(Boolean);
    let node = root;
    for (let i = 0; i < segs.length; i++) {
      const key = segs.slice(0, i+1).join('/');
      let child = node.children[segs[i]];
      if (!child) {
        child = { name: segs[i], segs: segs.slice(0, i+1), children:{}, files:[], size:0, parent: node };
        node.children[segs[i]] = child;
        nodesByPath[key] = child;
      }
      node = child;
    }
    node.files.push(v);
    v._size = v.size || 0;
    let n = node;
    while (n) { n.size += v._size; n = n.parent; }
  }
}
function getNode(segs) { let n = root; for (const s of segs) { n = n.children[s]; if (!n) return null; } return n; }
function rebuild() {
  root.children = {}; root.files = []; root.size = 0;
  for (const k in nodesByPath) delete nodesByPath[k];
  nodesByPath[''] = root;
  buildTree();
}

/* ---------------- treemap layout ---------------- */
function worst(row, side) {
  const s = row.reduce((a,r)=>a+r.area,0);
  const mx = Math.max(...row.map(r=>r.area)), mn = Math.min(...row.map(r=>r.area));
  return Math.max((side*side*mx)/(s*s), (s*s)/(side*side*mn));
}
function layoutRow(row, rect, out) {
  const wide = rect.w >= rect.h;
  const side = wide ? rect.h : rect.w;
  const area = row.reduce((a,r)=>a+r.area,0);
  const thick = area / side;
  let off = 0;
  for (const node of row) {
    const len = node.area / thick;
    if (wide) out.push({ it: node.it, x:rect.x, y:rect.y+off, w:thick, h:len });
    else out.push({ it: node.it, x:rect.x+off, y:rect.y, w:len, h:thick });
    off += len;
  }
  if (wide) { rect.x += thick; rect.w -= thick; }
  else { rect.y += thick; rect.h -= thick; }
  return rect;
}
function squarify(items, rect) {
  const out = [];
  const total = items.reduce((a,r)=>a+r.value,0) || 1;
  const scale = (rect.w*rect.h)/total;
  const nodes = items.map(it => ({ it, area: Math.max(it.value*scale, 0.001) }));
  nodes.sort((a,b)=>b.area-a.area);
  let row = [], r = {...rect}, i = 0;
  while (i < nodes.length) {
    const it = nodes[i];
    const side = Math.min(r.w, r.h);
    if (row.length === 0 || worst(row.concat([it]), side) <= worst(row, side)) { row.push(it); i++; }
    else { layoutRow(row, r, out); row = []; }
  }
  if (row.length) layoutRow(row, r, out);
  return out;
}

function computeFiltered() {
  (function dfs(n) {
    let s = 0;
    for (const f of n.files) if (visible(f)) s += (f.size || 0);
    for (const k in n.children) { dfs(n.children[k]); s += n.children[k].f; }
    n.f = s;
  })(root);
}

/* ---------------- current entries ---------------- */
function currentEntries() {
  computeFiltered();
  const node = getNode(curPath) || root;
  const entries = [];
  for (const k in node.children) {
    const c = node.children[k];
    if (c.f > 0) entries.push({ kind:'folder', name:c.name, size:c.f, node:c });
  }
  for (const f of node.files) {
    if (!visible(f)) continue;
    entries.push({ kind:'file', name:f.name, size:f.size||0, item:f });
  }
  entries.sort((a,b)=> sortAsc ? a.size-b.size : b.size-a.size);
  return entries;
}

/* ---------------- render ---------------- */
function render() {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  document.getElementById('view-dedupe').classList.toggle('hidden', tab !== 'dedupe');
  document.getElementById('view-organize').classList.toggle('hidden', tab !== 'organize');
  document.getElementById('view-files').classList.toggle('hidden', tab !== 'files');
  document.getElementById('view-review').classList.toggle('hidden', tab !== 'review');
  document.getElementById('view-selected').classList.toggle('hidden', tab !== 'selected');
  document.getElementById('view-rules').classList.toggle('hidden', tab !== 'rules');
  renderChips();
  renderStat();
  if (tab === 'dedupe') { DEDUPE ? renderDedupe() : loadDedupe(); }
  if (tab === 'organize') { ORGANIZE ? renderOrganize() : loadOrganize(); }
  if (tab === 'files') renderFiles();
  if (tab === 'review') renderReview();
  if (tab === 'selected') renderSelected();
  if (tab === 'rules') renderRules();
  updateReviewNav();
}

/* ---------------- duplicate finder ---------------- */
let DEDUPE = null;
const dedupeExpanded = new Set();
async function loadDedupe(force) {
  const box = document.getElementById('dedupe-list');
  box.innerHTML = '<p class="stat">扫描中…（按文件大小 + 名称相似度/番号聚类，数据量大时稍等）</p>';
  try {
    const r = await fetch('/dedupe' + (force ? '?force=1' : ''));
    const j = await r.json();
    DEDUPE = j.groups || [];
    dedupeExpanded.clear();
    renderDedupe();
    const msg = document.getElementById('dedupe-msg');
    if (msg) msg.textContent = '共 ' + DEDUPE.length + ' 组重复';
  } catch (e) {
    box.innerHTML = '<p class="stat">扫描失败：' + e + '</p>';
  }
}
function _dedupeRow(v) {
  const on = selected.has(v.id);
  return '<div class="sel-row">' +
    '<input type="checkbox" ' + (on?'checked':'') + ' onclick="dedupeToggle(\'' + v.id + '\')">' +
    '<div class="grow">' + esc(v.name) +
      '<div class="sub">' + esc(v.path) + ' · ' + fmtSize(v.size) +
      ' · <span class="cat" style="background:' + (COLORS[v.category]||'#888') + '">' +
      esc(LABELS[v.category]||v.category||'') + '</span></div></div>' +
    '<a class="play" href="#" data-id="' + v.id + '" onclick="return playHere(event,this)">播放</a>' +
    '<a class="play" target="_blank" rel="noopener" href="/download/' + encodeURIComponent(v.id) + '">下载</a>' +
  '</div>';
}
function renderDedupe() {
  const box = document.getElementById('dedupe-list');
  if (!DEDUPE) return;
  const groups = DEDUPE.map(g => ({ ...g, items: (g.items || []).filter(v => byId[v.id]) }))
                       .filter(g => g.items.length > 1);
  if (!groups.length) { box.innerHTML = '<p class="stat">没有发现重复文件（或已被删除）。</p>'; return; }
  box.innerHTML = groups.map(g => {
    const open = dedupeExpanded.has(g.index);
    const shown = open ? g.items : g.items.slice(0, 1);
    const rows = shown.map(_dedupeRow).join('');
    const more = g.items.length - 1;
    const toggle = more > 0
      ? '<button onclick="toggleExpand(' + g.index + ')">' + (open ? ('折叠其余 ' + more + ' 个') : ('展开其余 ' + more + ' 个')) + '</button>'
      : '';
    return '<div class="card">' +
      '<div class="head" style="justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">' +
        '<div class="name">' + (g.code ? ('番号 ' + esc(g.code) + ' · ') : '') +
          g.items.length + ' 个重复 · 单个 ' + fmtSize(g.size) + ' · 可省 ' + fmtSize(g.wasted) + '</div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap">' + toggle +
          '<button onclick="markGroupAll(' + g.index + ')">全选该组删除</button>' +
          '<button onclick="markRedundant(' + g.index + ')">仅保留最大</button>' +
        '</div>' +
      '</div>' +
      '<div style="padding:0 12px 12px">' + rows + '</div>' +
    '</div>';
  }).join('');
}
function toggleExpand(idx) {
  dedupeExpanded.has(idx) ? dedupeExpanded.delete(idx) : dedupeExpanded.add(idx);
  renderDedupe();
}
function dedupeToggle(id) {
  selected.has(id) ? selected.delete(id) : selected.add(id);
  renderStat(); renderDedupe();
  if (tab === 'selected') renderSelected();
}
function markRedundant(idx) {
  const g = (DEDUPE || []).find(x => x.index === idx);
  if (!g) return;
  g.items.forEach((v, i) => { if (i > 0 && byId[v.id]) selected.add(v.id); });
  renderStat(); renderDedupe();
  if (tab === 'selected') renderSelected();
}
function markGroupAll(idx) {
  const g = (DEDUPE || []).find(x => x.index === idx);
  if (!g) return;
  g.items.forEach(v => { if (byId[v.id]) selected.add(v.id); });
  renderStat(); renderDedupe();
  if (tab === 'selected') renderSelected();
}
function markAllRedundant() {
  (DEDUPE || []).forEach(g => g.items.forEach((v, i) => { if (i > 0 && byId[v.id]) selected.add(v.id); }));
  renderStat(); renderDedupe();
  toast('已在每组标记除“最大一个”外的其余文件，可到“待删除”核对后执行删除');
}

/* ---------------- organize (preview only) ---------------- */
let ORGANIZE = null;
async function loadOrganize(force) {
  const box = document.getElementById('organize-body');
  box.innerHTML = '<p class="stat">生成方案中…</p>';
  try {
    const r = await fetch('/organize' + (force ? '?force=1' : ''));
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '失败');
    ORGANIZE = j.plan;
    renderOrganize();
  } catch (e) { box.innerHTML = '<p class="stat">生成失败：' + e + '</p>'; }
}
function renderOrganize() {
  const box = document.getElementById('organize-body');
  if (!ORGANIZE) return;
  const p = ORGANIZE, s = p.summary;
  let h = '';
  h += '<p class="stat">目标根 <b>' + esc(p.base) + '</b>　数据来源 ' + esc(p.files_source || '') +
       '　<b>（预览，不会改动云盘）</b></p>';
  h += '<p class="stat">待移动 <b>' + s.move_count + '</b> 个 / ' + fmtSize(s.move_size) +
       '　将删除文件夹 <b>' + s.delete_folder_count + '</b>（顺带清理小文件 ' +
       s.delete_extra_count + ' 个 / ' + fmtSize(s.delete_extra_size) + '）　保留文件夹 <b>' +
       s.keep_folder_count + '</b></p>';
  let tg = '';
  for (const k in s.targets) {
    tg += '<div class="sel-row"><div class="grow">' + esc(p.base) + '/' + esc(k) +
          '</div><div>' + s.targets[k] + ' 个</div></div>';
  }
  h += '<details open><summary>移动到（按分类）</summary>' + tg + '</details>';
  let mv = '';
  for (const k in (p.by_target || {})) {
    const arr = p.by_target[k];
    mv += '<details><summary>' + esc(k) + ' · ' + arr.length + ' 个</summary><div>';
    arr.slice(0, 200).forEach(m => {
      mv += '<div class="sel-row"><div class="grow">' + esc(m.name) +
            '<div class="sub">' + esc(m.from) + ' → ' + esc(m.to) +
            (m.target_name !== m.name ? ('（改名 ' + esc(m.target_name) + '）') : '') +
            '</div></div><div>' + fmtSize(m.size) + '</div></div>';
    });
    if (arr.length > 200) mv += '<p class="stat">…仅显示前 200 条</p>';
    mv += '</div></details>';
  }
  h += '<details><summary>移动明细</summary>' + (mv || '<p class="stat">无</p>') + '</details>';
  let dl = '';
  (p.delete_folders || []).slice(0, 300).forEach(d => {
    dl += '<div class="sel-row"><div class="grow">' + esc(d.path) +
          '<div class="sub">移动 ' + d.moved + ' 个；清理 ' + d.extra_removed +
          ' 个小文件 / ' + fmtSize(d.extra_size) + '</div></div></div>';
  });
  if ((p.delete_folders || []).length > 300) dl += '<p class="stat">…仅显示前 300 个</p>';
  h += '<details><summary>将删除的文件夹 ' + (p.delete_folders || []).length + '</summary>' +
       (dl || '<p class="stat">无</p>') + '</details>';
  let kp = '';
  (p.keep_folders || []).slice(0, 300).forEach(d => {
    kp += '<div class="sel-row"><div class="grow">' + esc(d.path) +
          '<div class="sub">移动 ' + d.moved + ' 个；保留 ' + d.kept_count + ' 个较大文件，如：' +
          esc((d.kept_files && d.kept_files[0] && d.kept_files[0].name) || '') + '</div></div></div>';
  });
  if ((p.keep_folders || []).length > 300) kp += '<p class="stat">…仅显示前 300 个</p>';
  h += '<details><summary>保留的文件夹 ' + (p.keep_folders || []).length + '</summary>' +
       (kp || '<p class="stat">无</p>') + '</details>';
  box.innerHTML = h;
}

/* ---------------- classify rules ---------------- */
function renderRules() {
  const ov = (RULES.overrides || []).map(o => o.keyword + '=' + o.category).join('\n');
  const oe = document.getElementById('rule-overrides');
  if (oe) oe.value = ov;
  renderCatsEditor();
}
/* ---------------- category tree editor ---------------- */
function applyCats(data) {
  CATEGORIES = data.categories || CATEGORIES;
  LABELS = data.labels || LABELS;
  COLORS = data.colors || COLORS;
  CAT_DEFAULTS = data.defaults || CAT_DEFAULTS;
  CATS = CATEGORIES.map(c => c.id);
  if (filterCat && !CATS.includes(filterCat)) filterCat = null;
  renderChips();
  renderCatsEditor();
  renderStat();
  if (tab === 'files') renderFiles();
  if (tab === 'review') renderReview();
  if (tab === 'dedupe') renderDedupe();
}
const kwOpen = new Set();
function renderCatsEditor() {
  const box = document.getElementById('cat-editor');
  if (!box) return;
  box.innerHTML = CATEGORIES.map(c => {
    const custom = (RULES.extra && RULES.extra[c.id]) || null;
    const builtin = CAT_DEFAULTS[c.id] || [];
    const kws = (custom && custom.length) ? custom : builtin;
    const open = kwOpen.has(c.id);
    const badge = kws.length ? (' <span class="badge">' + kws.length + ' 关键词' + ((!custom || !custom.length) && builtin.length ? '·内置' : '') + '</span>') : '';
    let h = '<div class="sel-row" style="padding-left:' + (c.depth * 18) + 'px">' +
      '<div class="grow">' + esc(c.name) + badge + '</div>' +
      '<button onclick="toggleKw(\'' + c.id + '\')">' + (open ? '收起' : '关键词') + '</button>' +
      '<button onclick="addCat(\'' + c.id + '\')">＋子类</button>' +
      '<button onclick="renameCat(\'' + c.id + '\')">改名</button>' +
      '<button onclick="deleteCat(\'' + c.id + '\')">删除</button>' +
    '</div>';
    if (open) {
      h += '<div style="padding:4px 0 10px ' + (c.depth * 18 + 8) + 'px">' +
        (builtin.length && (!custom || !custom.length)
          ? '<div class="stat" style="margin-bottom:4px">当前为内置关键词，可增删后保存（保存后以你的列表为准）</div>' : '') +
        '<textarea id="kw-' + c.id + '" spellcheck="false" style="width:100%;height:160px;background:#0f1115;color:#e6e8eb;border:1px solid #2a2e35;border-radius:8px;padding:8px;font:12px/1.5 ui-monospace,Menlo,monospace">' +
          esc(kws.join('\n')) + '</textarea>' +
        '<div style="margin-top:6px">' +
          '<button class="primary" onclick="saveKeywords(\'' + c.id + '\')">保存关键词并重新分类</button> ' +
          '<button onclick="toggleKw(\'' + c.id + '\')">取消</button>' +
        '</div></div>';
    }
    return h;
  }).join('');
}
function toggleKw(id) { kwOpen.has(id) ? kwOpen.delete(id) : kwOpen.add(id); renderCatsEditor(); }
async function saveKeywords(id) {
  const extra = {};
  for (const k in (RULES.extra || {})) extra[k] = RULES.extra[k];
  extra[id] = _lines('kw-' + id);
  kwOpen.delete(id);
  await _saveRules(extra, RULES.overrides || [], '已保存「' + (LABELS[id] || id) + '」的关键词并重新分类');
}
async function saveOverrides() {
  const overrides = [];
  let bad = 0;
  for (const line of _lines('rule-overrides')) {
    const idx = line.lastIndexOf('=');
    if (idx <= 0) { bad++; continue; }
    const keyword = line.slice(0, idx).trim();
    let cid = line.slice(idx + 1).trim();
    if (!CATS.includes(cid)) {
      const hit = CATEGORIES.find(c => c.name === cid || (c.path || []).join('/') === cid);
      if (hit) cid = hit.id;
    }
    if (!keyword || !CATS.includes(cid)) { bad++; continue; }
    overrides.push({ keyword, category: cid });
  }
  const extra = {};
  for (const k in (RULES.extra || {})) extra[k] = RULES.extra[k];
  const msg = document.getElementById('rule-msg');
  if (bad && msg) msg.textContent = '有 ' + bad + ' 行无效，已忽略；保存中…';
  await _saveRules(extra, overrides, '已保存覆盖规则');
}
async function _saveRules(extra, overrides, okMsg) {
  const msg = document.getElementById('rule-msg');
  try {
    const r = await fetch('/rules', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ rules: { extra, overrides } })});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '失败');
    applyItems(j.items);
    RULES.extra = extra; RULES.overrides = overrides;
    renderChips(); renderStat(); renderCatsEditor();
    if (tab === 'files') renderFiles();
    if (tab === 'review') renderReview();
    if (msg) msg.textContent = okMsg + ' ✓';
  } catch (e) { if (msg) msg.textContent = '保存失败：' + e; }
}
async function catsApi(path, body) {
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body || {})});
  const j = await r.json();
  if (!j.ok) throw new Error(j.error || '失败');
  if (j.categories) applyCats(j);
  if (j.items) applyItems(j.items);
  return j;
}
async function addCat(parentId) {
  const name = prompt(parentId ? '子分类名称：' : '顶级分类名称：');
  if (!name) return;
  try { await catsApi('/categories/add', {parent_id: parentId || null, name}); toast('已新增分类'); }
  catch (e) { alert('新增失败：' + e); }
}
async function renameCat(id) {
  const name = prompt('新名称：', (LABELS[id] || ''));
  if (!name) return;
  try { await catsApi('/categories/rename', {id, name}); toast('已改名'); }
  catch (e) { alert('改名失败：' + e); }
}
async function deleteCat(id) {
  if (!confirm('删除分类「' + (LABELS[id] || id) + '」（含其子分类）？\n属于它的文件会回到自动分类。')) return;
  try { await catsApi('/categories/delete', {id}); toast('已删除分类'); }
  catch (e) { alert('删除失败：' + e); }
}
function _lines(id) {
  const el = document.getElementById(id);
  return (el ? el.value : '').split('\n').map(s => s.trim()).filter(Boolean);
}
function applyItems(items) {
  if (!items) return;
  for (const id in items) {
    const v = byId[id];
    if (v) { v.category = items[id].category; v.auto_category = items[id].auto_category; v.manual = items[id].manual; }
  }
}
function selectAllVisible() {
  let n = 0;
  for (const v of VIDEOS) if (visible(v)) { selected.add(v.id); n++; }
  renderStat(); renderChips();
  if (tab === 'files') renderFiles();
  if (tab === 'review') renderReview();
  if (tab === 'dedupe') renderDedupe();
  toast('已选中 ' + n + ' 个（当前筛选可见的文件）');
}
async function applyBatchCategory() {
  const idset = new Set(selected);
  for (const p of selectedFolders) for (const id of folderVideoIds(p)) idset.add(id);
  const ids = [...idset];
  if (!ids.length) { alert('先勾选文件/文件夹，或点“选中所有可见”'); return; }
  const cat = document.getElementById('batch-cat').value;
  const label = cat ? (LABELS[cat] || cat) : '自动分类';
  const extra = selectedFolders.size ? ('（含 ' + selectedFolders.size + ' 个文件夹内的视频）') : '';
  if (!confirm('把选中的 ' + ids.length + ' 个视频' + extra + ' 设为「' + label + '」？\n（会从“待删除”选择中移除）')) return;
  try {
    const r = await fetch('/manual_batch', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ ids, category: cat || null })});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '失败');
    applyItems(j.items);
    selected.clear();
    selectedFolders.clear();
    renderStat(); renderChips();
    if (tab === 'files') renderFiles();
    if (tab === 'review') renderReview();
    if (tab === 'dedupe') renderDedupe();
    toast('已把 ' + ids.length + ' 个文件分类为「' + label + '」');
  } catch (e) { alert('批量分类失败：' + e); }
}
async function reclassify() {
  const msg = document.getElementById('rule-msg');
  if (msg) msg.textContent = '重新分类中…';
  try {
    const r = await fetch('/reclassify', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '失败');
    applyItems(j.items);
    renderChips(); renderStat();
    if (tab === 'files') renderFiles();
    if (tab === 'review') renderReview();
    if (tab === 'dedupe') renderDedupe();
    if (msg) msg.textContent = '已按当前规则重新分类 ✓';
  } catch (e) { if (msg) msg.textContent = '失败：' + e; }
}
async function resetClassify() {
  if (!confirm('重置全部自动+手动分类结果？\n（会清空自定义规则和所有手动分类）')) return;
  const msg = document.getElementById('rule-msg');
  msg.textContent = '重置中…';
  try {
    const r = await fetch('/reset_classify', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '失败');
    applyItems(j.items);
    RULES.extra = {}; RULES.overrides = [];
    renderRules(); renderChips(); renderStat();
    if (tab === 'files') renderFiles();
    if (tab === 'review') renderReview();
    msg.textContent = '已重置全部分类 ✓';
  } catch (e) { msg.textContent = '失败：' + e; }
}

function catTree() {
  const root = [], stack = [];
  for (const c of CATEGORIES) {
    const node = { id: c.id, name: c.name, depth: c.depth, children: [] };
    while (stack.length && stack[stack.length - 1].depth >= c.depth) stack.pop();
    if (stack.length) stack[stack.length - 1].children.push(node);
    else root.push(node);
    stack.push(node);
  }
  return root;
}
function _countSubtree(node, cnt) {
  let n = cnt[node.id] || 0;
  for (const c of node.children) n += _countSubtree(c, cnt);
  node.count = n;
  return n;
}
function _fnode(n) {
  const has = n.children && n.children.length;
  const arrow = has ? '<span class="arrow">›</span>' : '';
  const sub = has ? '<div class="submenu">' + n.children.map(c => _fnode(c)).join('') + '</div>' : '';
  const dot = '<span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:' + (COLORS[n.id]||'#888') + ';margin-right:6px"></span>';
  const sel = (n.id === filterCat);
  const check = sel ? '<span class="check">✓</span>' : '';
  return '<div class="fitem' + (sel ? ' sel' : '') + '" onclick="setFilter(\'' + n.id + '\'); event.stopPropagation();">' +
    '<span>' + dot + esc(n.name) + ' <span class="stat" style="font-size:11px">' + (n.count||0) + '</span></span>' +
    '<span>' + check + arrow + '</span>' + sub + '</div>';
}
function renderChips() {
  const box = document.getElementById('chips');
  if (!box) return;
  const cnt = {};
  for (const v of VIDEOS) cnt[catOf(v)] = (cnt[catOf(v)]||0) + 1;
  const tree = catTree();
  for (const n of tree) _countSubtree(n, cnt);
  box.innerHTML =
    '<div class="fitem' + (!filterCat ? ' sel' : '') + '" onclick="setFilter(null); event.stopPropagation();">' +
      '<span>全部分类</span>' + (!filterCat ? '<span class="check">✓</span>' : '') + '</div>' +
    tree.map(n => _fnode(n)).join('');
  const btn = document.getElementById('filter-btn');
  if (btn) {
    const node = filterCat && CATEGORIES.find(c => c.id === filterCat);
    btn.textContent = (node ? (node.path || [node.name]).join(' / ') : '全部分类') + ' ▾';
  }
}
function toggleFilterMenu(ev) {
  if (ev) ev.stopPropagation();
  const m = document.getElementById('chips');
  if (m) m.style.display = (m.style.display === 'block') ? 'none' : 'block';
}
function closeFilterMenu() { const m = document.getElementById('chips'); if (m) m.style.display = 'none'; }
function setFilter(id) { filterCat = id; render(); closeFilterMenu(); }
document.addEventListener('click', closeFilterMenu);
function populateBatchSelect() {
  const sel = document.getElementById('batch-cat');
  if (!sel) return;
  sel.innerHTML = '<option value="">恢复自动</option>' +
    CATEGORIES.map(c => '<option value="' + c.id + '">' +
      esc((c.path || [c.name]).join(' / ')) + '</option>').join('');
}
function renderStat() {
  let n = 0, total = 0;
  for (const v of VIDEOS) if (visible(v)) { n++; total += v.size||0; }
  let sn = 0, st = 0;
  for (const id of selected) { const v = byId[id]; if (v) { sn++; st += v.size||0; } }
  document.getElementById('stat').innerHTML =
    '显示 <b>' + n + '</b> 项 / ' + fmtSize(total) +
    '　已选 <b>' + sn + '</b> / ' + fmtSize(st) +
    (selectedFolders.size ? ('　文件夹 <b>' + selectedFolders.size + '</b>') : '');
  document.getElementById('selcount').textContent = sn;
  syncSelection();
}
let _syncTimer = null;
function syncSelection() {
  clearTimeout(_syncTimer);
  _syncTimer = setTimeout(() => {
    fetch('/submit', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ delete: [...selected], replace: true })}).catch(() => {});
  }, 500);
}

function renderFiles() {
  const node = getNode(curPath) || root;
  // breadcrumb
  let crumb = '<a onclick="goto([])">全部</a>';
  let acc = [];
  for (const s of curPath) { acc.push(s); const a = acc.slice(); crumb += ' / <a onclick=\'goto(' + JSON.stringify(a) + ')\'>' + esc(s) + '</a>'; }
  document.getElementById('crumb').innerHTML = crumb;

  const entries = currentEntries();
  const tm = document.getElementById('tm');
  const rect = { x:0, y:0, w: tm.clientWidth, h: tm.clientHeight };
  const items = entries.map(e => ({ value: Math.max(e.size, 1), e }));
  const placed = squarify(items, rect);
  let html = '';
  for (const p of placed) {
    const e = p.it.e;
    const w = Math.max(0, p.w), h = Math.max(0, p.h);
    let cls = 'tm-node', color = '';
    if (e.kind === 'folder') cls += ' folder';
    else { color = COLORS[catOf(e.item)] || '#888'; if (selected.has(e.item.id)) cls += ' selected'; }
    const label = (w > 46 && h > 16) ? '<div class="tm-label">' + esc(e.name) + '</div>' : '';
    const style = 'left:'+p.x+'px;top:'+p.y+'px;width:'+w+'px;height:'+h+'px;' +
                  (color ? ('background:'+color+';') : '');
    const handler = e.kind === 'folder'
      ? 'data-segs=\'' + JSON.stringify(e.node.segs) + '\' onclick="gotoEl(this)"'
      : 'onclick="toggleFile(\'' + e.item.id + '\')"';
    const data = e.kind === 'folder'
      ? 'data-tip="folder|' + esc(e.name) + '|' + e.size + '|' + esc(e.node.segs.join('/')) + '"'
      : 'data-tip="file|' + esc(e.name) + '|' + e.size + '|' + esc(e.item.path) + '|' + catOf(e.item) + '"';
    html += '<div class="' + cls + '" style="' + style + '" ' + handler + ' ' + data + '>' + label + '</div>';
  }
  tm.innerHTML = html;

  // table
  const tb = document.getElementById('tbody');
  tb.innerHTML = entries.map(e => {
    if (e.kind === 'folder') {
      const fpath = '/' + e.node.segs.join('/');
      const fsel = selectedFolders.has(fpath);
      return '<tr class="folder' + (fsel ? ' picked' : '') + '" data-segs=\'' + JSON.stringify(e.node.segs) + '\' onclick="gotoEl(this)">' +
        '<td><input type="checkbox" ' + (fsel ? 'checked' : '') + ' data-path="' + esc(fpath) +
          '" onclick="event.stopPropagation(); toggleFolder(this)"></td>' +
        '<td>📁 ' + esc(e.name) + '</td><td>' + fmtSize(e.size) +
        '</td><td>-</td><td></td></tr>';
    }
    const v = e.item, on = selected.has(v.id);
    return '<tr class="file ' + (on?'picked':'') + '" data-id="' + v.id + '">' +
      '<td><input type="checkbox" ' + (on?'checked':'') + ' onclick="toggleFile(\'' + v.id + '\')"></td>' +
      '<td>' + esc(v.name) + (v.thumbs && v.thumbs.length ? '' : '') + '</td>' +
      '<td>' + fmtSize(v.size) + '</td>' +
      '<td>' + catSelect(v) + '</td>' +
      '<td><a class="play" href="#" data-id="' + v.id + '" onclick="return playHere(event,this)">播放</a>' +
      ' <a class="play" target="_blank" rel="noopener" href="/download/' + encodeURIComponent(v.id) + '">下载</a></td></tr>';
  }).join('');

  // re-bind tooltips
  bindTips();
}
function goto(segs) { curPath = segs.slice(); render(); }
function gotoEl(el) { try { goto(JSON.parse(el.dataset.segs || '[]')); } catch(e) { goto([]); } }
function toggleFile(id) { selected.has(id) ? selected.delete(id) : selected.add(id); render(); }
function toggleFolder(cb) {
  const p = cb.dataset.path;
  cb.checked ? selectedFolders.add(p) : selectedFolders.delete(p);
  cb.closest('tr').classList.toggle('picked', cb.checked);
  renderStat();
}
function folderVideoIds(path) {
  const p = path.replace(/\/+$/, '');
  const out = [];
  for (const v of VIDEOS) {
    const vp = (v.path || '/').replace(/\/+$/, '');
    if (vp === p || vp.startsWith(p + '/')) out.push(v.id);
  }
  return out;
}
function selectAllCurrent() { for (const e of currentEntries()) if (e.kind==='file') selected.add(e.item.id); render(); }

/* drag (marquee) multi-select in the file table */
let _drag = { active: false, moved: false, base: null, startEl: null };
function _fileRows() { return [...document.querySelectorAll('#tbody tr.file')]; }
function _nearestFileRow(y) {
  let best = null, bd = 1e9;
  for (const el of _fileRows()) {
    const r = el.getBoundingClientRect();
    const d = Math.abs((r.top + r.height / 2) - y);
    if (d < bd) { bd = d; best = el; }
  }
  return best;
}
function _applyDragRange(y) {
  const endEl = _nearestFileRow(y);
  const rows = _fileRows();
  if (!endEl) return;
  const si = rows.indexOf(_drag.startEl), ei = rows.indexOf(endEl);
  if (si < 0 || ei < 0) return;
  const lo = Math.min(si, ei), hi = Math.max(si, ei);
  selected.clear();
  (_drag.base || []).forEach(id => selected.add(id));
  for (let i = lo; i <= hi; i++) { const id = rows[i].dataset.id; if (id) selected.add(id); }
  for (const el of rows) {
    const on = selected.has(el.dataset.id);
    el.classList.toggle('picked', on);
    const cb = el.querySelector('input'); if (cb) cb.checked = on;
  }
  renderStat();
}
function _bindTableDrag() {
  const tbody = document.getElementById('tbody');
  if (!tbody) return;
  tbody.addEventListener('mousedown', ev => {
    const tr = ev.target.closest('tr.file');
    if (!tr || ev.button !== 0) return;
    if (ev.target.closest('a, input, button, select, option')) return;
    // only start a marquee drag from the left part (checkbox/name columns)
    const td = ev.target.closest('td');
    const tds = [...tr.children];
    if (!td || tds.indexOf(td) > 1) return;
    _drag.active = true; _drag.moved = false; _drag.startEl = tr;
    _drag.base = (ev.ctrlKey || ev.metaKey) ? new Set(selected) : new Set();
    ev.preventDefault();
    document.body.classList.add('selecting');
  });
  document.addEventListener('mousemove', ev => {
    if (!_drag.active) return;
    _drag.moved = true;
    _applyDragRange(ev.clientY);
  });
  document.addEventListener('mouseup', ev => {
    if (!_drag.active) return;
    const tr = _drag.startEl;
    _drag.active = false;
    document.body.classList.remove('selecting');
    if (_drag.moved) { renderFiles(); }
    else if (tr && tr.dataset.id) {  // simple click on the row toggles it
      toggleFile(tr.dataset.id);
    }
  });
}
function clearSel() { selected.clear(); selectedFolders.clear(); render(); }

/* ---------------- tooltip ---------------- */
function bindTips() {
  document.querySelectorAll('.tm-node').forEach(el => {
    el.addEventListener('mousemove', ev => {
      const parts = (el.dataset.tip||'').split('|');
      const tip = document.getElementById('tip');
      let text = '';
      if (parts[0]==='folder') text = '📁 ' + parts[1] + '<br>' + fmtSize(+parts[2]) + '　' + esc(parts[3]);
      else text = esc(parts[1]) + '<br>' + fmtSize(+parts[2]) + '　' + esc(parts[3]) +
                  '<br>分类：' + esc(LABELS[parts[4]]||parts[4]);
      tip.innerHTML = text;
      tip.style.display = 'block';
      tip.style.left = Math.min(ev.clientX + 14, window.innerWidth - 480) + 'px';
      tip.style.top = (ev.clientY + 16) + 'px';
    });
    el.addEventListener('mouseleave', () => { document.getElementById('tip').style.display = 'none'; });
  });
}

/* ---------------- review cards ---------------- */
function reviewItems() {
  const items = VIDEOS.filter(v => visible(v) && v.thumbs && v.thumbs.length);
  const sel = document.getElementById('review-sort');
  const mode = sel ? sel.value : 'size-desc';
  const cmp = {
    'size-desc': (a,b) => (b.size||0) - (a.size||0),
    'size-asc':  (a,b) => (a.size||0) - (b.size||0),
    'name':      (a,b) => String(a.name||'').localeCompare(String(b.name||'')),
    'category':  (a,b) => catOf(a).localeCompare(catOf(b)) || (b.size||0) - (a.size||0),
    'duration-desc': (a,b) => (b.duration||0) - (a.duration||0),
  }[mode] || ((a,b) => (b.size||0) - (a.size||0));
  return items.sort(cmp);
}
function renderReview() {
  const list = document.getElementById('cards');
  const items = reviewItems();
  list.innerHTML = '';
  reviewCards = [];
  if (!items.length) { list.innerHTML = '<p class="stat">当前筛选下没有已生成截图的视频。</p>'; updateReviewNav(); return; }
  items.forEach((v, i) => {
    const card = document.createElement('div');
    card.className = 'card'; card.dataset.id = v.id; card.dataset.idx = i;
    const res = (v.width && v.height) ? v.width + '×' + v.height : '分辨率未知';
    const thumbs = v.thumbs.map((t, i2) =>
      '<img loading="lazy" src="/thumb/' + encodeURIComponent(v.id) + '/' + (i2+1) +
      '" onclick="zoom(this.src); event.stopPropagation();">').join('');
    card.innerHTML =
      '<div class="card-cat">' + catSelect(v) + '</div>' +
      '<div class="head"><input type="checkbox" onchange="toggle(this)">' +
        '<div class="meta"><div class="name">' + esc(v.name) +
          '<a class="play" href="#" data-id="' + v.id + '" onclick="return playHere(event,this)">▶ 播放</a>' +
          '<a class="play" target="_blank" rel="noopener" href="/download/' + encodeURIComponent(v.id) + '">⬇ 下载</a></div>' +
        '<div class="sub">' + esc(v.path) + '</div>' +
        '<div class="sub"><span class="badge">' + fmtSize(v.size) + '</span>' +
        '<span class="badge">' + fmtDur(v.duration) + '</span>' +
        '<span class="badge">' + res + '</span>' +
        '<button class="mark" data-idx="' + i + '" onclick="markHere(this)">📍 标记进度</button></div></div></div>' +
      '<div class="thumbs">' + thumbs + '</div>';
    card.addEventListener('click', ev => {
      if (ev.target.closest('img, a, button, input, select, option')) return;
      const cb = card.querySelector('input'); cb.checked = !cb.checked;
      card.classList.toggle('marked', cb.checked);
      selected.has(v.id) ? selected.delete(v.id) : selected.add(v.id);
      renderStat(); renderChips();
    });
    list.appendChild(card);
    reviewCards.push(card);
  });
  updateReviewNav();
  applyResume(items);
}
/* floating index navigator for the review tab */
let reviewCards = [];
let MARKED_ID = RESUME_ID || '';
function reviewTopIndex() {
  if (!reviewCards.length) return 0;
  for (let i = 0; i < reviewCards.length; i++) {
    if (reviewCards[i].getBoundingClientRect().bottom > 110) return i;
  }
  return reviewCards.length - 1;
}
function reviewMarkPos() {
  if (!MARKED_ID) return -1;
  for (let i = 0; i < reviewCards.length; i++) {
    if (reviewCards[i].dataset.id === MARKED_ID) return i;
  }
  return -1;
}
function updateReviewNav() {
  const nav = document.getElementById('review-nav');
  if (!nav) return;
  if (tab !== 'review' || !reviewCards.length) { nav.classList.add('hidden'); return; }
  nav.classList.remove('hidden');
  document.getElementById('review-pos').textContent = (reviewTopIndex() + 1) + ' / ' + reviewCards.length;
  const mp = reviewMarkPos();
  const mEl = document.getElementById('review-mark');
  if (mEl) mEl.textContent = mp >= 0 ? ('标记 ' + (mp + 1) + ' / ' + reviewCards.length) : '标记 —';
}
function reviewGoto(i) {
  if (!reviewCards.length) return;
  i = Math.max(0, Math.min(i, reviewCards.length - 1));
  reviewCards[i].scrollIntoView({ block: 'start' });
  updateReviewNav();
}
function reviewJump() {
  const v = parseInt(document.getElementById('review-jump').value, 10);
  if (v > 0) reviewGoto(v - 1);
}
function reviewGotoMark() {
  const i = reviewMarkPos();
  if (i < 0) { toast('当前筛选下没有标记的视频'); return; }
  reviewGoto(i);
}
window.addEventListener('scroll', () => { if (tab === 'review') updateReviewNav(); });
function toggle(cb) {
  const card = cb.closest('.card'); card.classList.toggle('marked', cb.checked);
  const id = card.dataset.id;
  cb.checked ? selected.add(id) : selected.delete(id);
  renderStat();
}
function zoom(src) { document.getElementById('lightbox-img').src = src; document.getElementById('lightbox').style.display='flex'; }
function applyResume(items) {
  const i = reviewMarkPos();
  if (i < 0) return;
  const card = reviewCards[i];
  if (card) { card.classList.add('resume'); setTimeout(() => card.scrollIntoView({ block: 'start' }), 150); }
  updateReviewNav();
}
function markHere(btn) {
  const idx = parseInt(btn.dataset.idx, 10);
  const card = btn.closest('.card'); const item = byId[card.dataset.id];
  MARKED_ID = item.id;
  updateReviewNav();
  fetch('/mark', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({index: idx, id: item.id, name: item.name})}).then(() => {
      const bar = document.getElementById('resume-bar');
      bar.style.display = 'block';
      bar.textContent = '进度标记：' + item.name + '（浮窗里点“↩ 跳标记”可回到这里）';
    });
}

/* ---------------- organize apply (from the page) ---------------- */
async function runOrganize() {
  const opts = {
    apply: document.getElementById('org-apply').checked,
    delete_folders: document.getElementById('org-del').checked,
    clean_junk: document.getElementById('org-junk').checked,
    include_other: document.getElementById('org-other').checked,
    limit: parseInt(document.getElementById('org-limit').value, 10) || 0,
  };
  if (!opts.apply && !opts.clean_junk) { alert('请至少勾选“执行移动”或“清理垃圾文件夹”'); return; }
  const what = [];
  if (opts.apply) what.push('执行移动' + (opts.limit ? ('（前 ' + opts.limit + ' 个）') : '') + (opts.delete_folders ? ' + 删除空的源文件夹' : ''));
  if (opts.clean_junk) what.push('清理垃圾文件夹');
  if (opts.include_other) what.push('含成人-其他');
  if (!confirm('确认：' + what.join('；') + '？\n（删除均为移入回收站，可恢复）')) return;
  const msg = document.getElementById('org-msg');
  try {
    const r = await fetch('/organize/apply', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(opts)});
    const j = await r.json();
    if (!j.ok) { msg.textContent = j.error || '无法开始'; return; }
    msg.textContent = '任务已开始…';
    pollOrganize();
  } catch (e) { msg.textContent = '失败：' + e; }
}
function pollOrganize() {
  fetch('/organize/status').then(r => r.json()).then(s => {
    const msg = document.getElementById('org-msg');
    if (s.running) {
      msg.textContent = (s.msg || '处理中…') + (s.total ? (' ' + s.current + '/' + s.total) : '');
      setTimeout(pollOrganize, 1000);
    } else if (s.error) {
      msg.textContent = '出错：' + s.error;
    } else if (s.finished) {
      msg.textContent = '完成';
      toast('整理任务完成，正在刷新方案…');
      loadOrganize(true);
    }
  }).catch(() => setTimeout(pollOrganize, 1500));
}

/* ---------------- run shots from the page ---------------- */
async function runShots(mode, count) {
  const msg = document.getElementById('shots-msg');
  const wEl = document.getElementById('shotsW');
  const workers = wEl ? (parseInt(wEl.value) || 0) : 0;
  try {
    const r = await fetch('/shots', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ mode, count, workers })});
    const j = await r.json();
    if (!j.ok) { msg.textContent = j.error || '无法开始'; return; }
    msg.textContent = '截图任务已开始…';
    pollShots();
  } catch (e) { msg.textContent = '失败：' + e; }
}
function pollShots() {
  fetch('/shots/status').then(r => r.json()).then(s => {
    const msg = document.getElementById('shots-msg');
    if (s.running) {
      msg.textContent = '截图中 ' + s.current + '/' + (s.total || '?') + ' — ' + (s.name || '');
      setTimeout(pollShots, 1000);
    } else if (s.error) {
      msg.textContent = '截图出错：' + s.error;
    } else if (s.finished) {
      msg.textContent = '截图完成';
      for (const u of (s.updated || [])) {
        const v = byId[u.id];
        if (v) { v.thumbs = u.thumbs; v.done = u.done; v.duration = u.duration || v.duration;
                 v.width = u.width || v.width; v.height = u.height || v.height; }
      }
      if (tab === 'review') renderReview();
    }
  }).catch(() => setTimeout(pollShots, 1500));
}

/* ---------------- selected tab ---------------- */
function renderSelected() {
  const box = document.getElementById('sellist');
  const tools = ['<button onclick="clearSel()" style="margin-bottom:10px">清空选择</button>',
                 '<button onclick="submitSel()" style="margin-left:8px;margin-bottom:10px">仅导出清单</button>',
                 '<button class="danger" onclick="executeDelete()" style="margin-left:8px;margin-bottom:10px">执行删除（移入回收站）</button>',
                 '<span id="apply-msg" class="stat" style="margin-left:8px"></span>'];
  let total = 0;
  const rows = [...selected].map(id => byId[id]).filter(Boolean).map(v => {
    total += v.size||0;
    return '<div class="sel-row"><div class="grow">' +
      '<span class="cat" style="background:' + (COLORS[catOf(v)]||'#888') + '">' + esc(LABELS[catOf(v)]||catOf(v)) + '</span> ' +
      esc(v.name) + '<div class="sub">' + esc(v.path) + '</div></div>' +
      '<div>' + fmtSize(v.size) + '</div>' +
      '<button onclick="toggleFile(\'' + v.id + '\')">移除</button></div>';
  }).join('');
  document.getElementById('selinfo').innerHTML = '已选 <b>' + selected.size + '</b> 项，共 <b>' + fmtSize(total) + '</b>';
  box.innerHTML = tools.join('') + (rows || '<p class="stat">还没有选择任何文件。</p>');
}

/* ---------------- confirm + apply ---------------- */
function confirmDialog(title, bodyHtml, okText, onOk) {
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-body').innerHTML = bodyHtml;
  const ok = document.getElementById('confirm-ok');
  ok.textContent = okText;
  ok.onclick = () => { closeConfirm(); onOk(); };
  document.getElementById('confirm').style.display = 'flex';
}
function closeConfirm() { document.getElementById('confirm').style.display = 'none'; }

function executeDelete() {
  const ids = [...selected];
  if (!ids.length) { alert('还没有选择任何文件'); return; }
  let rows = '', total = 0;
  for (const id of ids) {
    const v = byId[id]; if (!v) continue;
    total += v.size || 0;
    rows += '<div class="sel-row"><div class="grow">' +
      '<span class="cat" style="background:' + (COLORS[catOf(v)]||'#888') + '">' + esc(LABELS[catOf(v)]||catOf(v)) + '</span> ' +
      esc(v.name) + '<div class="sub">' + esc(v.path) + '</div></div><div>' + fmtSize(v.size) + '</div></div>';
  }
  const body = '<p class="stat">共 <b>' + ids.length + '</b> 个文件，<b>' + fmtSize(total) +
    '</b>。将<b>移入迅雷回收站</b>（可在回收站恢复，不是永久删除）。</p>' + rows;
  confirmDialog('确认删除以下 ' + ids.length + ' 个文件？', body, '确认删除', () => runApply(ids));
}
async function runApply(ids) {
  const msg = document.getElementById('apply-msg');
  if (msg) msg.textContent = '删除中…';
  try {
    const r = await fetch('/apply', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ ids })});
    const j = await r.json();
    if (!j.ok) { if (msg) msg.textContent = j.error || '无法开始'; return; }
    pollApply();
  } catch (e) { if (msg) msg.textContent = '失败：' + e; }
}
function pollApply() {
  fetch('/apply/status').then(r => r.json()).then(s => {
    const msg = document.getElementById('apply-msg');
    if (s.running) {
      if (msg) msg.textContent = '删除中 ' + s.current + '/' + (s.total || '?');
      setTimeout(pollApply, 800);
    } else if (s.error) {
      if (msg) msg.textContent = '删除出错：' + s.error;
    } else if (s.finished) {
      const results = s.results || [];
      const okIds = results.filter(r => r.ok).map(r => r.id);
      const fails = results.filter(r => !r.ok);
      for (const id of okIds) {
        selected.delete(id);
        const v = byId[id];
        if (v) { const i = VIDEOS.indexOf(v); if (i >= 0) VIDEOS.splice(i, 1); delete byId[id]; }
      }
      rebuild();
      renderChips(); renderStat();
      if (tab === 'selected') renderSelected();
      if (tab === 'files') renderFiles();
      if (tab === 'review') renderReview();
      const m2 = document.getElementById('apply-msg');
      if (m2) m2.textContent = '删除完成：成功 ' + okIds.length + '，失败 ' + fails.length;
    }
  }).catch(() => setTimeout(pollApply, 1200));
}

/* ---------------- play (in-page) + download ---------------- */
function playHere(ev, el) {
  ev.preventDefault(); ev.stopPropagation();
  const v = document.getElementById('player-video');
  v.src = '/play/' + encodeURIComponent(el.dataset.id);
  document.getElementById('modal').style.display = 'flex';
  v.play().catch(() => {});
  return false;
}
function closePlayer() {
  const v = document.getElementById('player-video');
  try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) {}
  document.getElementById('modal').style.display = 'none';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePlayer(); });

/* ---------------- submit ---------------- */
async function submitSel() {
  const ids = [...selected];
  if (!ids.length) { alert('还没有选择任何文件'); return; }
  let total = 0; ids.forEach(id => total += (byId[id]?.size||0));
  if (!confirm('导出 ' + ids.length + ' 个文件（' + fmtSize(total) + '）到删除清单？\n（不会删除，仅累加写入 selections.json，页面可继续操作）')) return;
  try {
    const r = await fetch('/submit', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ delete: ids })});
    const j = await r.json();
    if (!j.ok) throw new Error(j.error || '失败');
    toast('已导出清单，累计 ' + j.total + ' 个（未删除）。可继续挑选，最后用“执行删除”或终端 ./sweeper apply');
  } catch (e) { alert('导出失败：' + e); }
}
function toast(message, ms) {
  const t = document.getElementById('toast');
  t.textContent = message;
  t.style.display = 'block';
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.style.display = 'none'; }, ms || 4000);
}

/* ---------------- sort ---------------- */
document.querySelectorAll('th[data-sort]').forEach(th => th.addEventListener('click', () => {
  const k = th.dataset.sort;
  if (sortKey === k) sortAsc = !sortAsc; else { sortKey = k; sortAsc = false; }
  render();
}));

/* ---------------- tabs & boot ---------------- */
const tabScroll = {};
function setTab(t) {
  if (t === tab) return;
  tabScroll[tab] = window.scrollY || 0;
  tab = t;
  render();
  requestAnimationFrame(() => window.scrollTo(0, tabScroll[t] || 0));
}
window.addEventListener('resize', () => { if (tab === 'files') renderFiles(); });

rebuild();
render();
_bindTableDrag();
populateBatchSelect();
</script>
</body>
</html>
"""

BASE_COLORS = {
    "jp": "#e06c75",
    "west": "#61afef",
    "cn": "#e5c07b",
    "adult_other": "#c678dd",
    "non_adult": "#56b6c2",
    "unknown": "#8b93a1",
}


def cats_payload() -> dict:
    flat = categories.flat()
    colors = {}
    for x in flat:
        cid = x.get("id")
        if cid in BASE_COLORS:
            colors[cid] = BASE_COLORS[cid]
        else:
            h = int(hashlib.md5(str(cid).encode()).hexdigest(), 16) % 360
            r, g, b = colorsys.hsv_to_rgb(h / 360.0, 0.55, 0.85)
            colors[cid] = "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
    defaults = {cid: classify.default_keywords(cid) for cid in ("jp", "cn", "west", "non_adult")}
    return {"categories": flat, "labels": {x["id"]: x["name"] for x in flat},
            "colors": colors, "defaults": defaults}


def build_html(videos: list[dict], resume_index: int = -1,
               selected_ids: list | None = None, resume_id: str = "") -> str:
    slim = [{
        "id": v["id"],
        "name": v.get("name"),
        "path": v.get("path"),
        "size": v.get("size"),
        "duration": v.get("duration"),
        "width": v.get("width"),
        "height": v.get("height"),
        "category": v.get("category", "unknown"),
        "auto_category": v.get("auto_category", v.get("category", "unknown")),
        "manual": bool(v.get("manual")),
        "thumbs": v.get("thumbs") or [],
    } for v in videos]
    html = PAGE.replace("__VIDEOS_JSON__", json.dumps(slim, ensure_ascii=False))
    payload = cats_payload()
    html = html.replace("__LABELS_JSON__", json.dumps(payload["labels"], ensure_ascii=False))
    html = html.replace("__COLORS_JSON__", json.dumps(payload["colors"], ensure_ascii=False))
    html = html.replace("__CATEGORIES_JSON__", json.dumps(payload["categories"], ensure_ascii=False))
    html = html.replace("__CAT_DEFAULTS_JSON__", json.dumps(payload["defaults"], ensure_ascii=False))
    html = html.replace("__RULES_JSON__", json.dumps(classify.load_user_rules(), ensure_ascii=False))
    html = html.replace("__SELECTED_JSON__", json.dumps(selected_ids or [], ensure_ascii=False))
    html = html.replace("__RESUME_ID__", json.dumps(resume_id or ""))
    return html.replace("__RESUME_INDEX__", str(resume_index))


def _resume_index(videos: list[dict]) -> int:
    progress = util.read_json(util.REVIEW_PROGRESS_FILE, {}) or {}
    if not progress:
        return -1
    pid = progress.get("id")
    if pid:
        for i, v in enumerate(videos):
            if v["id"] == pid:
                return i
    try:
        idx = int(progress.get("index", -1))
    except (TypeError, ValueError):
        return -1
    return idx if 0 <= idx < len(videos) else -1


def serve(videos: list[dict], port: int = 8765, open_browser: bool = True,
          play_url_fn=None, shots_fn=None, apply_fn=None, download_url_fn=None,
          organize_fn=None) -> dict:
    dataset = videos
    by_id = {v["id"]: v for v in videos}
    resume_index = _resume_index(videos)
    progress = util.read_json(util.REVIEW_PROGRESS_FILE, {}) or {}
    resume_id = progress.get("id") or ""
    saved = util.read_json(util.SELECTIONS_FILE, {}) or {}
    initial_selected = [i for i in (saved.get("delete") or []) if i in by_id]
    html = build_html(videos, resume_index, initial_selected, resume_id)
    submitted: dict = {"done": False, "selections": None}
    httpd_holder: dict = {}
    shots_state = {"running": False, "finished": False, "current": 0, "total": 0,
                   "name": "", "error": None, "updated": []}
    shots_lock = threading.Lock()
    apply_state = {"running": False, "finished": False, "current": 0, "total": 0,
                   "error": None, "results": []}
    apply_lock = threading.Lock()
    organize_state = {"running": False, "finished": False, "current": 0, "total": 0,
                      "msg": "", "error": None, "result": None}
    organize_lock = threading.Lock()
    dedupe_cache: dict = {"groups": None}

    def _organize_worker(opts: dict) -> None:
        try:
            def on_progress(i, total, msg="", *a):
                with organize_lock:
                    organize_state.update(current=i or 0, total=total or 0, msg=str(msg))

            result = organize_fn(opts, on_progress)
            with organize_lock:
                organize_state["result"] = result
        except Exception as exc:
            util.log(f"整理执行失败: {exc}", "ERROR")
            with organize_lock:
                organize_state["error"] = str(exc)
        finally:
            with organize_lock:
                organize_state["running"] = False
                organize_state["finished"] = True

    def _apply_worker(ids: list) -> None:
        try:
            def on_progress(i, total, fid):
                with apply_lock:
                    apply_state.update(current=i, total=total)

            results = apply_fn(ids, on_progress)
            with apply_lock:
                apply_state["results"] = results
            ok_ids = {r["id"] for r in results if r["ok"]}
            if ok_ids:
                existing = util.read_json(util.SELECTIONS_FILE, {}) or {}
                remaining = [i for i in (existing.get("delete") or []) if i not in ok_ids]
                existing["delete"] = remaining
                existing["items"] = [it for it in (existing.get("items") or [])
                                     if it.get("id") not in ok_ids]
                util.atomic_write_json(util.SELECTIONS_FILE, existing)

                # drop the deleted files from local listings so a restart won't show them
                for path in (util.VIDEOS_FILE, util.FILES_FILE, util.CLASSIFIED_FILE):
                    items = util.read_json(path)
                    if isinstance(items, list):
                        util.atomic_write_json(
                            path, [x for x in items if x.get("id") not in ok_ids])
                for v in list(dataset):
                    if v.get("id") in ok_ids:
                        dataset.remove(v)
                        by_id.pop(v.get("id"), None)
                util.log(f"已从本地清单移除 {len(ok_ids)} 个已删除文件")
        except Exception as exc:
            util.log(f"执行删除失败: {exc}", "ERROR")
            with apply_lock:
                apply_state["error"] = str(exc)
        finally:
            with apply_lock:
                apply_state["running"] = False
                apply_state["finished"] = True

    def _shots_worker(mode: str, count: int, workers: int) -> None:
        try:
            def on_progress(i, total, video):
                with shots_lock:
                    shots_state.update(current=i, total=total, name=video.get("name"))

            selected = shots_fn(mode, count, workers, on_progress)
            with shots_lock:
                shots_state["updated"] = [{
                    "id": v["id"], "thumbs": v.get("thumbs") or [], "done": v.get("done"),
                    "duration": v.get("duration"), "width": v.get("width"),
                    "height": v.get("height"), "error": v.get("error"),
                } for v in selected]
        except Exception as exc:
            util.log(f"截图任务失败: {exc}", "ERROR")
            with shots_lock:
                shots_state["error"] = str(exc)
        finally:
            with shots_lock:
                shots_state["running"] = False
                shots_state["finished"] = True

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body: bytes, content_type="text/html; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                self._send(200, html.encode("utf-8"))
                return
            if parsed.path.startswith("/thumb/"):
                self._serve_thumb(parsed.path[len("/thumb/"):])
                return
            if parsed.path.startswith("/play/"):
                self._serve_play(parsed.path[len("/play/"):])
                return
            if parsed.path.startswith("/download/"):
                self._serve_download(parsed.path[len("/download/"):])
                return
            if parsed.path == "/shots/status":
                with shots_lock:
                    payload = {"ok": True, **shots_state}
                self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json")
                return
            if parsed.path == "/apply/status":
                with apply_lock:
                    payload = {"ok": True, **apply_state}
                self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json")
                return
            if parsed.path == "/dedupe":
                if dedupe_cache["groups"] is None or parsed.query == "force=1":
                    groups = dedupe.group_payload(dedupe.find_duplicates(dataset))
                    dedupe_cache["groups"] = groups
                    util.log(f"重复文件扫描完成：{len(groups)} 组")
                self._send(200, json.dumps({"ok": True, "groups": dedupe_cache["groups"]},
                                           ensure_ascii=False).encode("utf-8"), "application/json")
                return
            if parsed.path == "/organize":
                try:
                    plan = organize.load_and_build(util.load_config())
                    payload = {"ok": True, "plan": plan}
                except Exception as exc:
                    util.log(f"生成整理方案失败: {exc}", "ERROR")
                    payload = {"ok": False, "error": str(exc)}
                self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json")
                return
            if parsed.path == "/categories":
                self._send(200, json.dumps({"ok": True, **cats_payload()}, ensure_ascii=False)
                           .encode("utf-8"), "application/json")
                return
            if parsed.path == "/organize/status":
                with organize_lock:
                    payload = {"ok": True, **organize_state}
                self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                           "application/json")
                return
            self._send(404, b"not found", "text/plain")

        def _serve_play(self, vid: str):
            if play_url_fn is None:
                self._send(501, b"play not enabled", "text/plain")
                return
            if vid not in by_id:
                self._send(404, b"no video", "text/plain")
                return
            try:
                url = play_url_fn(vid)
            except Exception as exc:
                self._send(502, f"获取播放直链失败: {exc}".encode("utf-8"), "text/plain; charset=utf-8")
                return
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()

        def _serve_download(self, vid: str):
            """Stream the file to the browser with Content-Disposition: attachment."""
            picker = download_url_fn or play_url_fn
            if picker is None:
                self._send(501, b"download not enabled", "text/plain")
                return
            video = by_id.get(vid)
            if not video:
                self._send(404, b"no video", "text/plain")
                return
            try:
                url = picker(vid)
            except Exception as exc:
                self._send(502, f"获取下载直链失败: {exc}".encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            import requests as _requests

            filename = video.get("name") or (vid + ".mp4")
            try:
                upstream = _requests.get(url, stream=True, timeout=30)
            except Exception as exc:
                self._send(502, f"连接资源失败: {exc}".encode("utf-8"),
                           "text/plain; charset=utf-8")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + quote(filename))
            length = upstream.headers.get("Content-Length")
            if length:
                self.send_header("Content-Length", length)
            self.end_headers()
            try:
                for chunk in upstream.iter_content(chunk_size=1 << 20):
                    if chunk:
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                upstream.close()

        def _serve_thumb(self, rest: str):
            parts = rest.split("/")
            if len(parts) != 2:
                self._send(404, b"bad thumb", "text/plain")
                return
            vid, idx = parts
            video = by_id.get(vid)
            if not video:
                self._send(404, b"no video", "text/plain")
                return
            try:
                idx = int(idx) - 1
                rel = (video.get("thumbs") or [])[idx]
            except (ValueError, IndexError):
                self._send(404, b"no thumb", "text/plain")
                return
            path = util.DATA_DIR / rel
            if not path.exists():
                self._send(404, b"missing", "text/plain")
                return
            self._send(200, path.read_bytes(), "image/jpeg")

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                data = {}

            if path == "/apply":
                if apply_fn is None:
                    self._send(501, json.dumps(
                        {"ok": False, "error": "删除功能不可用（可能未登录）"},
                        ensure_ascii=False).encode("utf-8"), "application/json")
                    return
                ids = [i for i in (data.get("ids") or []) if i in by_id]
                if not ids:
                    self._send(400, json.dumps({"ok": False, "error": "没有可删除的文件"},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                    return
                with apply_lock:
                    if apply_state["running"]:
                        self._send(409, json.dumps(
                            {"ok": False, "error": "已有删除任务在运行"},
                            ensure_ascii=False).encode("utf-8"), "application/json")
                        return
                    apply_state.update(running=True, finished=False, current=0,
                                       total=len(ids), error=None, results=[])
                util.log(f"开始执行删除：{len(ids)} 个文件")
                threading.Thread(target=_apply_worker, args=(ids,), daemon=True).start()
                self._send(200, b'{"ok":true}', "application/json")
                return

            if path == "/organize/apply":
                if organize_fn is None:
                    self._send(501, json.dumps({"ok": False, "error": "整理功能不可用（可能未登录）"},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                    return
                opts = {
                    "apply": bool(data.get("apply")),
                    "delete_folders": bool(data.get("delete_folders")),
                    "clean_junk": bool(data.get("clean_junk")),
                    "include_other": bool(data.get("include_other")),
                    "limit": int(data.get("limit") or 0) or None,
                }
                if not (opts["apply"] or opts["clean_junk"]):
                    self._send(400, json.dumps({"ok": False, "error": "请至少勾选“执行移动”或“清理垃圾文件夹”"},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                    return
                with organize_lock:
                    if organize_state["running"]:
                        self._send(409, json.dumps({"ok": False, "error": "已有整理任务在运行"},
                                                   ensure_ascii=False).encode("utf-8"), "application/json")
                        return
                    organize_state.update(running=True, finished=False, current=0, total=0,
                                          msg="", error=None, result=None)
                util.log(f"开始整理任务：{opts}")
                threading.Thread(target=_organize_worker, args=(opts,), daemon=True).start()
                self._send(200, b'{"ok":true}', "application/json")
                return

            if path == "/shots":
                if shots_fn is None:
                    self._send(501, json.dumps(
                        {"ok": False, "error": "截图功能不可用（可能未登录）"},
                        ensure_ascii=False).encode("utf-8"), "application/json")
                    return
                with shots_lock:
                    if shots_state["running"]:
                        self._send(409, json.dumps(
                            {"ok": False, "error": "已有截图任务在运行"},
                            ensure_ascii=False).encode("utf-8"), "application/json")
                        return
                    shots_state.update(running=True, finished=False, current=0, total=0,
                                       name="", error=None, updated=[])
                mode = data.get("mode") or "more"
                try:
                    count = int(data.get("count") or 100)
                except (TypeError, ValueError):
                    count = 100
                try:
                    workers = int(data.get("workers") or 0)
                except (TypeError, ValueError):
                    workers = 0
                util.log(f"开始截图任务：mode={mode} count={count} workers={workers or '默认'}")
                threading.Thread(target=_shots_worker, args=(mode, count, workers),
                                 daemon=True).start()
                self._send(200, b'{"ok":true}', "application/json")
                return

            if path == "/mark":
                if data.get("clear"):
                    util.atomic_write_json(util.REVIEW_PROGRESS_FILE, {})
                    util.log("已清除进度标记")
                else:
                    index = data.get("index", -1)
                    util.atomic_write_json(util.REVIEW_PROGRESS_FILE, {
                        "index": index, "id": data.get("id"),
                        "name": data.get("name"), "updated_at": int(time.time()),
                    })
                    util.log(f"已标记进度：第 {int(index) + 1} 个 — {data.get('name')}")
                self._send(200, b'{"ok":true}', "application/json")
                return

            if path == "/manual":
                fid = data.get("id")
                cat = data.get("category")
                item = by_id.get(fid)
                if item is None:
                    self._send(404, b'{"ok":false,"error":"unknown id"}', "application/json")
                    return
                manual = classify.load_manual()
                if cat in categories.ids():
                    manual[fid] = cat
                else:
                    manual.pop(fid, None)
                classify.save_manual(manual)
                if fid in manual:
                    item["category"] = manual[fid]
                    item["manual"] = True
                else:
                    auto, _ = classify.classify(item.get("name") or "", item.get("path") or "")
                    item["category"] = auto
                    item["auto_category"] = auto
                    item["manual"] = False
                util.log(f"手动分类: {item.get('name')} -> {categories.labels().get(item['category'], item['category'])}")
                self._send(200, json.dumps({"ok": True, "id": fid, "category": item["category"],
                                            "auto_category": item.get("auto_category", item["category"]),
                                            "manual": item["manual"]}, ensure_ascii=False).encode("utf-8"),
                           "application/json")
                return

            if path == "/manual_batch":
                ids = [i for i in (data.get("ids") or []) if i in by_id]
                cat = data.get("category")
                if not ids:
                    self._send(400, json.dumps({"ok": False, "error": "没有选中文件"},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                    return
                manual = classify.load_manual()
                for fid in ids:
                    if cat in categories.ids():
                        manual[fid] = cat
                    else:
                        manual.pop(fid, None)
                classify.save_manual(manual)
                classified = classify.categorize_files(dataset, None, manual)
                util.atomic_write_json(util.CLASSIFIED_FILE, classified)
                items = {c["id"]: {"category": c["category"],
                                   "auto_category": c.get("auto_category", c["category"]),
                                   "manual": bool(c.get("manual"))} for c in classified}
                for v in dataset:
                    rec = items.get(v["id"])
                    if rec:
                        v.update(category=rec["category"], auto_category=rec["auto_category"],
                                 manual=rec["manual"])
                label = categories.labels().get(cat, "自动分类") if cat in categories.ids() else "自动分类"
                util.log(f"批量手动分类：{len(ids)} 个 → {label}")
                self._send(200, json.dumps({"ok": True, "items": items},
                                           ensure_ascii=False).encode("utf-8"), "application/json")
                return

            if path == "/categories/add":
                try:
                    categories.add_node(data.get("parent_id") or None, data.get("name"))
                    self._send(200, json.dumps({"ok": True, **cats_payload()},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                except Exception as exc:
                    self._send(400, json.dumps({"ok": False, "error": str(exc)},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                return

            if path == "/categories/rename":
                try:
                    categories.rename_node(data.get("id"), data.get("name"))
                    self._send(200, json.dumps({"ok": True, **cats_payload()},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                except Exception as exc:
                    self._send(400, json.dumps({"ok": False, "error": str(exc)},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                return

            if path == "/categories/delete":
                try:
                    removed = categories.delete_node(data.get("id"))
                except Exception as exc:
                    self._send(400, json.dumps({"ok": False, "error": str(exc)},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                    return
                manual = classify.load_manual()
                for rid in removed:
                    manual.pop(rid, None)
                classify.save_manual(manual)
                classified = classify.categorize_files(dataset, None, manual)
                util.atomic_write_json(util.CLASSIFIED_FILE, classified)
                items = {c["id"]: {"category": c["category"],
                                   "auto_category": c.get("auto_category", c["category"]),
                                   "manual": bool(c.get("manual"))} for c in classified}
                for v in dataset:
                    rec = items.get(v["id"])
                    if rec:
                        v.update(category=rec["category"], auto_category=rec["auto_category"],
                                 manual=rec["manual"])
                util.log(f"已删除分类 {data.get('id')}（含子类 {len(removed)} 个）")
                self._send(200, json.dumps({"ok": True, "items": items, **cats_payload()},
                                           ensure_ascii=False).encode("utf-8"), "application/json")
                return

            if path == "/reset_classify":
                classify.save_manual({})
                classify.save_user_rules({"extra": {}, "overrides": []})
                classified = classify.categorize_files(dataset, {}, {})
                util.atomic_write_json(util.CLASSIFIED_FILE, classified)
                items = {c["id"]: {"category": c["category"],
                                   "auto_category": c.get("auto_category", c["category"]),
                                   "manual": bool(c.get("manual"))} for c in classified}
                for v in dataset:
                    rec = items.get(v["id"])
                    if rec:
                        v.update(category=rec["category"], auto_category=rec["auto_category"],
                                 manual=rec["manual"])
                util.log(f"已重置全部分类（含手动），重新分类 {len(items)} 项")
                self._send(200, json.dumps({"ok": True, "items": items},
                                           ensure_ascii=False).encode("utf-8"), "application/json")
                return

            if path == "/reclassify":
                try:
                    classified = classify.categorize_files(dataset)
                    util.atomic_write_json(util.CLASSIFIED_FILE, classified)
                    items = {c["id"]: {"category": c["category"],
                                       "auto_category": c.get("auto_category", c["category"]),
                                       "manual": bool(c.get("manual"))} for c in classified}
                    for v in dataset:
                        rec = items.get(v["id"])
                        if rec:
                            v.update(category=rec["category"], auto_category=rec["auto_category"],
                                     manual=rec["manual"])
                    util.log(f"已按当前规则重新分类 {len(items)} 项")
                    self._send(200, json.dumps({"ok": True, "items": items},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                except Exception as exc:
                    util.log(f"重新分类失败: {exc}", "ERROR")
                    self._send(500, json.dumps({"ok": False, "error": str(exc)},
                                               ensure_ascii=False).encode("utf-8"), "application/json")
                return

            if path == "/rules":
                rules = data.get("rules") or {}
                try:
                    classify.save_user_rules(rules)
                    rules = classify.load_user_rules()
                    classified = classify.categorize_files(dataset, rules)
                    util.atomic_write_json(util.CLASSIFIED_FILE, classified)
                    items = {c["id"]: {"category": c["category"],
                                       "auto_category": c.get("auto_category", c["category"]),
                                       "manual": bool(c.get("manual"))} for c in classified}
                    for v in dataset:
                        rec = items.get(v["id"])
                        if rec:
                            v.update(category=rec["category"], auto_category=rec["auto_category"],
                                     manual=rec["manual"])
                    util.log(f"分类规则已更新，重新分类 {len(items)} 项")
                    self._send(200, json.dumps({"ok": True, "items": items},
                                               ensure_ascii=False).encode("utf-8"),
                               "application/json")
                except Exception as exc:
                    util.log(f"重新分类失败: {exc}", "ERROR")
                    self._send(500, json.dumps({"ok": False, "error": str(exc)},
                                               ensure_ascii=False).encode("utf-8"),
                               "application/json")
                return

            if path != "/submit":
                self._send(404, b"not found", "text/plain")
                return

            new_ids = [i for i in (data.get("delete") or []) if i in by_id]
            existing = util.read_json(util.SELECTIONS_FILE, {}) or {}
            if data.get("replace"):
                cur = list(dict.fromkeys(new_ids))
            else:
                cur = list(existing.get("delete") or [])
                for i in new_ids:
                    if i not in cur:
                        cur.append(i)
            selections = {
                "submitted_at": int(time.time()),
                "delete": cur,
                "items": [
                    {"id": i, "name": by_id[i].get("name"), "path": by_id[i].get("path"),
                     "size": by_id[i].get("size")}
                    for i in cur if i in by_id
                ],
            }
            util.atomic_write_json(util.SELECTIONS_FILE, selections)
            util.log(f"已更新删除清单：本次 {len(new_ids)}，合计 {len(cur)}（服务继续运行）")
            self._send(200, json.dumps({"ok": True, "total": len(cur)},
                                       ensure_ascii=False).encode("utf-8"), "application/json")
            return

    httpd = None
    last_error = None
    for candidate in range(port, port + 20):
        try:
            httpd = _FastHTTPServer(("127.0.0.1", candidate), Handler)
            if candidate != port:
                util.log(f"端口 {port} 被占用，改用 {candidate}", "WARN")
            port = candidate
            break
        except OSError as exc:
            last_error = exc
            if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
    if httpd is None:
        raise SystemExit(
            f"端口 {port}~{port + 19} 都被占用，无法启动服务（{last_error}）。\n"
            f"可先结束占用进程：lsof -ti :{port} | xargs kill"
        )

    httpd_holder["httpd"] = httpd
    url = f"http://127.0.0.1:{port}/"
    util.log(f"管家页面: {url}")
    util.log("文件管理=WizTree 框图；视频审核=缩略图勾选；待删除=网页“执行删除”或终端 apply")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        util.log("已中断", "WARN")
    finally:
        httpd.server_close()

    util.log("服务已停止", "WARN")
    return util.read_json(util.SELECTIONS_FILE, {}) or {}

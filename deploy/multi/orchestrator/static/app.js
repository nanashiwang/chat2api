// chat2api Orchestrator 前端
// 原生 fetch + 简单 DOM 操作，无框架

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function getCookie(name) {
    return document.cookie.split('; ')
        .find(r => r.startsWith(name + '='))?.split('=')[1] || '';
}

function csrf() {
    return getCookie('orch_csrf');
}

async function api(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    // 所有请求都带 CSRF 头：少数 GET（如 /api/secrets/{slug} reveal）也走 CSRF 校验
    const c = csrf();
    if (c) opts.headers['X-CSRF-Token'] = c;
    const r = await fetch('.' + path, opts);
    if (r.status === 401) {
        location.href = './login';
        return;
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(formatApiError(data.detail) || `HTTP ${r.status}`);
    return data;
}

function formatApiError(detail) {
    if (!detail) return '';
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) {
        return detail.map((item) => {
            if (typeof item === 'string') return item;
            const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
            return [loc, item.msg].filter(Boolean).join(': ');
        }).join('\n');
    }
    if (typeof detail === 'object') {
        return detail.message || detail.msg || JSON.stringify(detail);
    }
    return String(detail);
}

function toast(msg, isErr = false) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.remove('hidden', 'bg-gray-900', 'bg-red-600');
    el.classList.add(isErr ? 'bg-red-600' : 'bg-gray-900');
    setTimeout(() => el.classList.add('hidden'), 3000);
}

function fmtUptime(sec) {
    if (sec == null) return '-';
    if (sec < 60) return sec + 's';
    if (sec < 3600) return Math.floor(sec / 60) + 'm';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h';
    return Math.floor(sec / 86400) + 'd';
}

function fmtCookieAge(ts) {
    if (!ts) return '<span class="text-gray-400">未刷新</span>';
    const age = Math.floor(Date.now() / 1000 - ts);
    let txt, color;
    if (age < 600) { txt = age + 's 前'; color = 'text-green-600'; }
    else if (age < 3600) { txt = Math.floor(age / 60) + 'm 前'; color = 'text-green-600'; }
    else if (age < 86400) { txt = Math.floor(age / 3600) + 'h 前'; color = 'text-yellow-600'; }
    else { txt = Math.floor(age / 86400) + 'd 前'; color = 'text-red-600'; }
    return `<span class="${color}">${txt}</span>`;
}

function healthBadge(state, health) {
    if (state !== 'running') {
        return `<span><span class="dot dot-na"></span>${state}</span>`;
    }
    if (health === 'healthy') return `<span><span class="dot dot-healthy"></span>healthy</span>`;
    if (health === 'unhealthy') return `<span><span class="dot dot-unhealthy"></span>unhealthy</span>`;
    if (health === 'starting') return `<span><span class="dot dot-starting"></span>starting</span>`;
    return `<span><span class="dot dot-na"></span>${health}</span>`;
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
    }[c]));
}

/**
 * 紧凑型行内模型 chip 渲染：最多展示 maxVisible 个，超出显示 "+N"。
 * 入参：models 可以是 ["gpt-5", ...] 或 [{id, source}, ...]
 */
function renderInlineModels(models, maxVisible = 4) {
    if (!models || !models.length) {
        return '<div class="mt-1 text-xs text-gray-400">无可用模型</div>';
    }
    const ids = models.map(m => (typeof m === 'string') ? m : (m && m.id));
    const visible = ids.slice(0, maxVisible);
    const more = ids.length - visible.length;
    const chips = visible.map(id => `<span class="model-chip">${escapeHtml(id)}</span>`).join('');
    const moreBadge = more > 0
        ? `<span class="model-chip" style="background:#f3f4f6;color:#6b7280;border-color:#e5e7eb">+${more}</span>`
        : '';
    return `<div class="flex flex-wrap gap-1 mt-1 max-w-md">${chips}${moreBadge}</div>`;
}

function renderRows(instances) {
    if (!instances.length) {
        $('#tbody').innerHTML = '<tr><td colspan="9" class="px-4 py-8 text-center text-gray-400">暂无账号，点击右上角「新增账号」开始</td></tr>';
        return;
    }
    $('#tbody').innerHTML = instances.map(it => `
        <tr class="border-t border-gray-100 hover:bg-gray-50">
            <td class="px-4 py-2 font-medium kbd-row">${escapeHtml(it.slug)}</td>
            <td class="px-4 py-2 kbd-row text-xs text-gray-600">${escapeHtml(it.proxy_masked || '-')}</td>
            <td class="px-4 py-2">${healthBadge(it.state, it.health)}</td>
            <td class="px-4 py-2 kbd-row text-xs text-gray-600">${escapeHtml(it.exit_ip || '?')}</td>
            <td class="px-4 py-2 text-xs text-gray-500">${fmtUptime(it.uptime_seconds)}</td>
            <td class="px-4 py-2 text-xs">${fmtCookieAge(it.cookie_last_success_at)}</td>
            <td class="px-4 py-2 text-xs text-gray-600">${escapeHtml(it.note || '-')}</td>
            <td class="px-4 py-2">
                <button class="row-action-btn text-indigo-600 font-medium" data-action="invoke" data-slug="${escapeHtml(it.slug)}">📡 调用</button>
                ${renderInlineModels(it.models, 4)}
            </td>
            <td class="px-4 py-2 text-right whitespace-nowrap">
                <button class="row-action-btn text-blue-600" data-action="secret" data-slug="${escapeHtml(it.slug)}">凭证</button>
                <button class="row-action-btn text-gray-700" data-action="edit" data-slug="${escapeHtml(it.slug)}" data-proxy="${escapeHtml(it.proxy_masked || '')}" data-note="${escapeHtml(it.note || '')}">编辑</button>
                <button class="row-action-btn text-orange-600" data-action="restart" data-slug="${escapeHtml(it.slug)}">重启</button>
                ${it.state === 'running'
                    ? `<button class="row-action-btn text-yellow-600" data-action="stop" data-slug="${escapeHtml(it.slug)}">停止</button>`
                    : `<button class="row-action-btn text-green-600" data-action="start" data-slug="${escapeHtml(it.slug)}">启动</button>`}
                <button class="row-action-btn text-red-600" data-action="delete" data-slug="${escapeHtml(it.slug)}">删除</button>
            </td>
        </tr>
    `).join('');

    $$('#tbody button').forEach(btn => {
        btn.addEventListener('click', () => onRowAction(btn.dataset));
    });
}

async function loadStatus() {
    try {
        const data = await api('GET', '/api/status');
        renderRows(data.instances);
        $('#server-status').textContent = `共 ${data.instances.length} 个实例 · 服务器时间 ${new Date(data.server_time*1000).toLocaleTimeString()}`;
    } catch (e) {
        toast('加载状态失败：' + e.message, true);
    }
}

// ---------- 模态 ----------

let modalMode = 'add';   // 'add' | 'edit'
let modalSlug = '';

function openModal(mode, prefill = {}) {
    modalMode = mode;
    modalSlug = prefill.slug || '';
    $('#modal-title').textContent = mode === 'add' ? '新增账号' : `编辑 ${prefill.slug}`;
    $('#f-slug').value = prefill.slug || '';
    $('#f-slug').disabled = mode === 'edit';
    $('#f-proxy').value = mode === 'edit' ? '' : (prefill.proxy_url || '');
    $('#f-proxy').placeholder = mode === 'edit'
        ? `当前：${prefill.proxy || '(无)'}; 留空则不变`
        : 'socks5://user:pass@host:port';
    $('#f-note').value = prefill.note || '';
    $('#modal-error').classList.add('hidden');
    $('#modal').classList.remove('hidden');
    $('#modal').classList.add('flex');
}

function closeModal() {
    $('#modal').classList.add('hidden');
    $('#modal').classList.remove('flex');
}

$('#btn-add').addEventListener('click', () => openModal('add'));
$('#btn-cancel').addEventListener('click', closeModal);
$('#btn-refresh').addEventListener('click', () => loadStatus());

$('#modal-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const slug = $('#f-slug').value.trim();
    const proxy_url = $('#f-proxy').value.trim();
    const note = $('#f-note').value.trim();
    $('#btn-submit').disabled = true;
    $('#btn-submit').textContent = '处理中...';
    try {
        if (modalMode === 'add') {
            await api('POST', '/api/accounts', { slug, proxy_url, note });
            toast('新增成功，等待容器启动...');
        } else {
            const body = { note };
            if (proxy_url) body.proxy_url = proxy_url;
            await api('PATCH', '/api/accounts/' + encodeURIComponent(modalSlug), body);
            toast('编辑成功，正在重建...');
        }
        closeModal();
        await loadStatus();
    } catch (e) {
        $('#modal-error').textContent = e.message;
        $('#modal-error').classList.remove('hidden');
    } finally {
        $('#btn-submit').disabled = false;
        $('#btn-submit').textContent = '保存';
    }
});

// ---------- 行操作 ----------

async function onRowAction({ action, slug, proxy, note }) {
    if (action === 'edit') {
        openModal('edit', { slug, proxy, note });
        return;
    }
    if (action === 'secret') {
        await showSecret(slug);
        return;
    }
    if (action === 'invoke') {
        await openInvokeModal(slug);
        return;
    }
    if (action === 'delete') {
        if (!confirm(`确认删除 ${slug}？\n容器将被销毁，data/${slug}/ 会保留。`)) return;
        try {
            await api('DELETE', '/api/accounts/' + encodeURIComponent(slug));
            toast(`已删除 ${slug}`);
            await loadStatus();
        } catch (e) {
            toast('删除失败：' + e.message, true);
        }
        return;
    }
    if (['start', 'stop', 'restart'].includes(action)) {
        try {
            await api('POST', `/api/instances/${encodeURIComponent(slug)}/${action}`);
            toast(`${action} ${slug} 已发出`);
            setTimeout(loadStatus, 1500);
        } catch (e) {
            toast(`${action} 失败：` + e.message, true);
        }
    }
}

// ---------- 凭证查看 ----------

async function showSecret(slug) {
    if (!confirm(`查看 ${slug} 的明文凭证？\n该操作会写入审计日志。`)) return;
    try {
        const d = await api('GET', '/api/secrets/' + encodeURIComponent(slug));
        const origin = location.origin;   // e.g. http://107.172.96.31:60403
        const adminUrl = `${origin}/${d.slug}/admin/login`;
        const apiUrl   = `${origin}/${d.slug}/v1/chat/completions`;
        $('#secret-body').innerHTML = `
            <div class="text-xs text-gray-500 mb-2">仅展示用户侧需要的凭证；后端 API_PREFIX 由 nginx 自动改写，不应直接访问。</div>
            <div><span class="font-semibold">slug:</span> <code class="bg-gray-100 px-1">${escapeHtml(d.slug)}</code></div>
            <div><span class="font-semibold">AUTHORIZATION:</span> <code class="bg-gray-100 px-1 break-all">${escapeHtml(d.AUTHORIZATION)}</code></div>
            <div><span class="font-semibold">ADMIN_PASSWORD:</span> <code class="bg-gray-100 px-1 break-all">${escapeHtml(d.ADMIN_PASSWORD)}</code></div>
            <div><span class="font-semibold">PROXY_URL:</span> <code class="bg-gray-100 px-1 break-all">${escapeHtml(d.PROXY_URL || '(无)')}</code></div>
            <div class="pt-3 mt-3 border-t space-y-2">
                <div>
                    <div class="text-xs text-gray-500 mb-1">① Admin 后台（粘 cookie 用）</div>
                    <a class="text-blue-600 underline break-all" href="${escapeHtml(adminUrl)}" target="_blank" rel="noopener">${escapeHtml(adminUrl)}</a>
                    <div class="text-xs text-gray-400">用上面 ADMIN_PASSWORD 登录</div>
                </div>
                <div>
                    <div class="text-xs text-gray-500 mb-1">② API 调用示例</div>
                    <code class="bg-gray-100 px-1 block break-all text-xs">curl ${escapeHtml(apiUrl)} -H "Authorization: Bearer ${escapeHtml(d.AUTHORIZATION)}" -H "Content-Type: application/json" -d '{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}'</code>
                </div>
            </div>
        `;
        $('#modal-secret').classList.remove('hidden');
        $('#modal-secret').classList.add('flex');
    } catch (e) {
        toast('获取凭证失败：' + e.message, true);
    }
}

$('#btn-close-secret').addEventListener('click', () => {
    $('#modal-secret').classList.add('hidden');
    $('#modal-secret').classList.remove('flex');
    $('#secret-body').innerHTML = '';   // 立即清屏
});

// ---------- 审计 ----------

$('#btn-audit').addEventListener('click', async () => {
    try {
        const d = await api('GET', '/api/audit?limit=200');
        $('#audit-body').innerHTML = d.records.map(r => `
            <tr class="border-t border-gray-100">
                <td class="px-2 py-1 kbd-row text-gray-600">${escapeHtml(r.ts || '')}</td>
                <td class="px-2 py-1 kbd-row">${escapeHtml(r.ip || '')}</td>
                <td class="px-2 py-1 font-medium">${escapeHtml(r.action || '')}</td>
                <td class="px-2 py-1 kbd-row">${escapeHtml(r.slug || '-')}</td>
                <td class="px-2 py-1">${r.ok ? '<span class="text-green-600">✓</span>' : '<span class="text-red-600">✗</span>'}</td>
                <td class="px-2 py-1 text-gray-500">${escapeHtml(JSON.stringify({...r, ts:undefined, ip:undefined, action:undefined, slug:undefined, ok:undefined, actor:undefined}).replace(/^\{\}$/, ''))}</td>
            </tr>
        `).join('');
        $('#modal-audit').classList.remove('hidden');
        $('#modal-audit').classList.add('flex');
    } catch (e) {
        toast('加载审计失败：' + e.message, true);
    }
});

$('#btn-close-audit').addEventListener('click', () => {
    $('#modal-audit').classList.add('hidden');
    $('#modal-audit').classList.remove('flex');
});

// ---------- 诊断日志 ----------

let logTargetsLoaded = false;

async function loadLogTargets() {
    const d = await api('GET', '/api/log-targets');
    const targets = d.targets || [];
    $('#log-target').innerHTML = targets.map(t =>
        `<option value="${escapeHtml(t.id)}">${escapeHtml(t.label)} · ${escapeHtml(t.container)}</option>`
    ).join('');
    logTargetsLoaded = true;
}

async function refreshLogs() {
    const target = $('#log-target').value || 'orchestrator';
    const tail = $('#log-tail').value || '200';
    $('#logs-body').textContent = '加载中...';
    $('#logs-meta').textContent = '';
    try {
        const d = await api('GET', `/api/logs?target=${encodeURIComponent(target)}&tail=${encodeURIComponent(tail)}`);
        $('#logs-body').textContent = d.logs || '(无日志输出)';
        $('#logs-meta').textContent = `${d.ok ? 'OK' : '异常'} · ${d.container || target} · 最近 ${d.tail || tail} 行`;
        if (!d.ok) toast('日志目标返回异常，内容已显示', true);
    } catch (e) {
        $('#logs-body').textContent = '加载日志失败：' + e.message;
        $('#logs-meta').textContent = '加载失败';
        toast('加载日志失败：' + e.message, true);
    }
}

async function openLogsModal() {
    $('#modal-logs').classList.remove('hidden');
    $('#modal-logs').classList.add('flex');
    try {
        if (!logTargetsLoaded) await loadLogTargets();
        await refreshLogs();
    } catch (e) {
        $('#logs-body').textContent = '加载日志失败：' + e.message;
        toast('加载日志失败：' + e.message, true);
    }
}

$('#btn-diag-logs').addEventListener('click', openLogsModal);
$('#btn-close-logs').addEventListener('click', () => {
    $('#modal-logs').classList.add('hidden');
    $('#modal-logs').classList.remove('flex');
});
$('#btn-refresh-logs').addEventListener('click', refreshLogs);
$('#log-target').addEventListener('change', refreshLogs);
$('#log-tail').addEventListener('change', refreshLogs);

// ---------- 调用信息 (单实例) ----------

let invokeCurrent = null;   // 当前 modal 展示的 info dict
let invokeSnippetTab = 'curl';

const PLAN_COLOR_CLASS = {
    free: 'bg-gray-200 text-gray-700',
    plus: 'bg-blue-100 text-blue-700',
    team: 'bg-emerald-100 text-emerald-700',
    pro: 'bg-amber-100 text-amber-700',
    enterprise: 'bg-violet-100 text-violet-700',
    unknown: 'bg-rose-100 text-rose-700',
};

function absoluteBaseUrl(rawBaseUrl) {
    if (!rawBaseUrl) return '';
    if (/^https?:\/\//.test(rawBaseUrl)) return rawBaseUrl;
    // 相对路径 → 拼当前 origin
    return location.origin + (rawBaseUrl.startsWith('/') ? rawBaseUrl : '/' + rawBaseUrl);
}

function genSnippets(baseUrl, apiKey, model) {
    const url = (baseUrl || '').replace(/\/$/, '');
    const k = apiKey || 'YOUR_API_KEY';
    const m = model || 'gpt-4o-mini';
    return {
        curl: `curl ${url}/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "Authorization: Bearer ${k}" \\
  -d '{
    "model": "${m}",
    "messages": [{"role":"user","content":"Hello"}]
  }'`,
        python: `from openai import OpenAI

client = OpenAI(
    base_url="${url}",
    api_key="${k}",
)
resp = client.chat.completions.create(
    model="${m}",
    messages=[{"role": "user", "content": "Hello"}],
)
print(resp.choices[0].message.content)`,
        node: `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "${url}",
  apiKey: "${k}",
});

const resp = await client.chat.completions.create({
  model: "${m}",
  messages: [{ role: "user", content: "Hello" }],
});
console.log(resp.choices[0].message.content);`,
    };
}

async function copyToClipboard(text, btn) {
    try {
        await navigator.clipboard.writeText(text);
        const orig = btn.textContent;
        btn.textContent = '✓ 已复制';
        setTimeout(() => { btn.textContent = orig; }, 1500);
    } catch (e) {
        toast('复制失败：' + e.message, true);
    }
}

function renderModelChips(models, sourceHint) {
    const wrap = $('#invoke-models-chips');
    if (!models || !models.length) {
        wrap.innerHTML = '<span class="text-xs text-gray-400">无可用模型</span>';
    } else {
        wrap.innerHTML = models.map(m => {
            const modelId = (typeof m === 'string') ? m : (m && m.id);
            const source = (typeof m === 'string') ? '' : (m && m.source);
            const sourceTag = source === 'probe'
                ? '<span class="ml-1 text-[10px] text-emerald-600">实测</span>'
                : (source === 'alias'
                    ? '<span class="ml-1 text-[10px] text-amber-600">别名</span>'
                    : '');
            return `<span class="model-chip">${escapeHtml(modelId)}${sourceTag}</span>`;
        }).join('');
    }
    $('#invoke-models-source-hint').textContent = sourceHint || '';
}

function renderInvokeSnippet() {
    if (!invokeCurrent) return;
    const baseUrl = absoluteBaseUrl(invokeCurrent.base_url);
    const firstModel = (invokeCurrent.models && invokeCurrent.models[0] && invokeCurrent.models[0].id) || 'gpt-4o-mini';
    // 注意：因为后端没下发原文 auth，前端代码示例里只能填 masked key。提示用户从「凭证」按钮取原文。
    const apiKey = invokeCurrent.auth_masked || 'YOUR_API_KEY';
    const snippets = genSnippets(baseUrl, apiKey, firstModel);
    $('#invoke-snippet').textContent = snippets[invokeSnippetTab] || snippets.curl;
}

async function openInvokeModal(slug) {
    $('#invoke-slug-label').textContent = slug;
    $('#invoke-base-url').textContent = '加载中...';
    $('#invoke-auth-key').textContent = '加载中...';
    $('#invoke-plan').textContent = '...';
    $('#invoke-plan-source').textContent = '';
    $('#invoke-cached-state').textContent = '';
    $('#invoke-models-chips').innerHTML = '';
    $('#invoke-error').classList.add('hidden');
    $('#invoke-error').textContent = '';
    $('#invoke-snippet').textContent = '';
    $('#modal-invoke').classList.remove('hidden');
    $('#modal-invoke').classList.add('flex');

    try {
        const info = await api('GET', '/api/instances/' + encodeURIComponent(slug) + '/info');
        invokeCurrent = info;
        $('#invoke-base-url').textContent = absoluteBaseUrl(info.base_url) || '(未配置)';
        $('#invoke-auth-key').textContent = info.auth_masked || '(空)';
        const planEl = $('#invoke-plan');
        planEl.textContent = info.plan_label || info.plan_type || 'unknown';
        planEl.className = 'inline-block px-2 py-1 rounded text-xs ' + (PLAN_COLOR_CLASS[info.plan_type] || PLAN_COLOR_CLASS.unknown);
        $('#invoke-plan-source').textContent = info.plan_source === 'jwt' ? '(从 JWT 解析)' : '(无 token, 默认 unknown)';
        $('#invoke-cached-state').textContent = info.cached ? '✓ 使用 5min 缓存' : '✓ 新生成';
        renderModelChips(info.models, '(套餐默认表)');
        $('#invoke-error').classList.add('hidden');
        $('#invoke-error').textContent = '';
        invokeSnippetTab = 'curl';
        $$('.snippet-tab').forEach(b => {
            const active = b.dataset.snippet === 'curl';
            b.classList.toggle('border-blue-600', active);
            b.classList.toggle('text-blue-600', active);
            b.classList.toggle('border-transparent', !active);
            b.classList.toggle('text-gray-500', !active);
        });
        renderInvokeSnippet();
    } catch (e) {
        $('#invoke-base-url').textContent = '加载失败';
        toast('加载调用信息失败：' + e.message, true);
    }
}

function closeInvokeModal() {
    $('#modal-invoke').classList.add('hidden');
    $('#modal-invoke').classList.remove('flex');
    invokeCurrent = null;
}

$('#btn-close-invoke').addEventListener('click', closeInvokeModal);

// 实时探测按钮
$('#btn-probe-models').addEventListener('click', async () => {
    if (!invokeCurrent) return;
    const slug = invokeCurrent.slug;
    const btn = $('#btn-probe-models');
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = '探测中...';
    try {
        const d = await api('POST', '/api/probe-models/' + encodeURIComponent(slug), {});
        const models = d.model_entries || (d.models || []).map(id => ({ id, source: 'probe' }));
        if (invokeCurrent) {
            invokeCurrent.models = models;
            const hasAlias = models.some(m => m.source === 'alias');
            renderModelChips(
                models,
                (hasAlias ? '(实测 + 深度研究别名 @ ' : '(实测 @ ') + new Date(d.probed_at * 1000).toLocaleTimeString() + ')'
            );
            renderInvokeSnippet();   // 用新的第一个 model 刷新代码示例
        }
        $('#invoke-error').classList.add('hidden');
        $('#invoke-error').textContent = '';
        toast('探测成功：' + models.length + ' 个模型');
        // 30s 内置灰
        btn.textContent = '⏳ 30s 冷却中';
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = originalText;
        }, 30000);
    } catch (e) {
        $('#invoke-error').textContent = e.message;
        $('#invoke-error').classList.remove('hidden');
        toast('探测失败：' + e.message, true);
        btn.disabled = false;
        btn.textContent = originalText;
    }
});

// snippet tab 切换
$$('.snippet-tab').forEach(btn => {
    btn.addEventListener('click', () => {
        invokeSnippetTab = btn.dataset.snippet;
        $$('.snippet-tab').forEach(b => {
            const active = b === btn;
            b.classList.toggle('border-blue-600', active);
            b.classList.toggle('text-blue-600', active);
            b.classList.toggle('border-transparent', !active);
            b.classList.toggle('text-gray-500', !active);
        });
        renderInvokeSnippet();
    });
});

// 通用 copy 按钮事件委托
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const targetId = btn.dataset.copyTarget;
    if (!targetId) return;
    const el = document.getElementById(targetId);
    if (!el) return;
    const text = el.tagName === 'PRE' ? el.textContent : el.textContent.trim();
    copyToClipboard(text, btn);
});

// ---------- 调用汇总（跨实例）+ 导出 ----------

async function openSummaryModal() {
    $('#summary-body').innerHTML = '<tr><td colspan="6" class="px-3 py-6 text-center text-gray-400">加载中...</td></tr>';
    $('#summary-empty').classList.add('hidden');
    $('#modal-summary').classList.remove('hidden');
    $('#modal-summary').classList.add('flex');
    try {
        const d = await api('GET', '/api/instances/aggregate');
        const rows = d.instances || [];
        if (!rows.length) {
            $('#summary-body').innerHTML = '';
            $('#summary-empty').classList.remove('hidden');
            return;
        }
        $('#summary-body').innerHTML = rows.map(r => {
            const endpoint = absoluteBaseUrl(r.base_url) || '(未配置)';
            const planClass = PLAN_COLOR_CLASS[r.plan_type] || PLAN_COLOR_CLASS.unknown;
            const health = r.container_state === 'running'
                ? (r.container_health === 'healthy'
                    ? '<span class="text-green-600">● healthy</span>'
                    : '<span class="text-yellow-600">● ' + escapeHtml(r.container_health || '?') + '</span>')
                : '<span class="text-gray-400">○ ' + escapeHtml(r.container_state || 'absent') + '</span>';
            const modelIds = (r.models || []).map(m => (typeof m === 'string') ? m : (m && m.id)).filter(Boolean);
            const modelsHtml = modelIds.length
                ? `<div class="flex flex-wrap gap-1">${modelIds.map(id => `<span class="model-chip">${escapeHtml(id)}</span>`).join('')}</div>`
                : '<span class="text-xs text-gray-400">无</span>';
            return `
                <tr class="border-t border-gray-100 hover:bg-gray-50 align-top">
                    <td class="px-3 py-2 font-medium kbd-row">${escapeHtml(r.slug)}</td>
                    <td class="px-3 py-2"><span class="inline-block px-2 py-0.5 rounded text-xs ${planClass}">${escapeHtml(r.plan_label || r.plan_type)}</span></td>
                    <td class="px-3 py-2 text-xs text-gray-600 kbd-row break-all">${escapeHtml(endpoint)}</td>
                    <td class="px-3 py-2 text-xs text-gray-600 kbd-row">${escapeHtml(r.auth_masked || '-')}</td>
                    <td class="px-3 py-2 text-xs">${modelsHtml}</td>
                    <td class="px-3 py-2 text-xs">${health}</td>
                </tr>
            `;
        }).join('');
    } catch (e) {
        $('#summary-body').innerHTML = `<tr><td colspan="6" class="px-3 py-6 text-center text-red-500">加载失败：${escapeHtml(e.message)}</td></tr>`;
    }
}

function exportConfig(fmt) {
    // 触发文件下载：浏览器会保留 session cookie，FastAPI Response 带 Content-Disposition
    window.location.href = './api/export/' + encodeURIComponent(fmt);
    toast('开始下载 ' + fmt + ' 配置');
}

$('#btn-summary').addEventListener('click', openSummaryModal);
$('#btn-close-summary').addEventListener('click', () => {
    $('#modal-summary').classList.add('hidden');
    $('#modal-summary').classList.remove('flex');
});

document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-export');
    if (!btn) return;
    const fmt = btn.dataset.fmt;
    if (fmt) exportConfig(fmt);
});

// ---------- Playground 试调用 ----------

let pgInstances = [];   // 缓存 options 返回值

async function openPlaygroundModal() {
    $('#modal-playground').classList.remove('hidden');
    $('#modal-playground').classList.add('flex');
    $('#pg-result').classList.add('hidden');
    $('#pg-result-error').classList.add('hidden');
    try {
        const d = await api('GET', '/api/playground/options');
        pgInstances = d.instances || [];
        const slugSel = $('#pg-slug');
        if (!pgInstances.length) {
            slugSel.innerHTML = '<option value="">(无实例)</option>';
            $('#pg-model').innerHTML = '';
            return;
        }
        slugSel.innerHTML = pgInstances.map(it =>
            `<option value="${escapeHtml(it.slug)}">${escapeHtml(it.slug)} · ${escapeHtml(it.plan_label || it.plan_type)}</option>`
        ).join('');
        renderPgModels();
    } catch (e) {
        toast('加载实例列表失败：' + e.message, true);
    }
}

function renderPgModels() {
    const slug = $('#pg-slug').value;
    const inst = pgInstances.find(x => x.slug === slug);
    const models = inst ? (inst.models || []) : [];
    $('#pg-model').innerHTML = models.length
        ? models.map(m => `<option value="${escapeHtml(m)}">${escapeHtml(m)}</option>`).join('')
        : '<option value="">(无模型)</option>';
}

async function runPlayground() {
    const slug = $('#pg-slug').value;
    const model = $('#pg-model').value;
    const system = $('#pg-system').value;
    const user = $('#pg-user').value.trim();
    const temperature = parseFloat($('#pg-temp').value);
    const max_tokens = parseInt($('#pg-max-tokens').value, 10);

    if (!slug || !model) { toast('请先选择实例与模型', true); return; }
    if (!user) { toast('user prompt 不能为空', true); return; }

    const btn = $('#btn-pg-run');
    const origText = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="pg-loading-spinner"></span>运行中...';
    $('#pg-result').classList.remove('hidden');
    $('#pg-result-status').textContent = '...';
    $('#pg-result-latency').textContent = '...';
    $('#pg-result-usage').textContent = '...';
    $('#pg-result-content').textContent = '';
    $('#pg-result-error').classList.add('hidden');

    try {
        const d = await api('POST', '/api/playground/invoke', {
            slug, model, system, user, temperature, max_tokens
        });
        $('#pg-result-latency').textContent = (d.latency_ms || 0) + 'ms';
        if (d.ok) {
            $('#pg-result-status').innerHTML = '<span class="text-green-600">✓ 200</span>';
            const u = d.usage || {};
            $('#pg-result-usage').textContent = `${u.prompt_tokens || '-'} + ${u.completion_tokens || '-'} = ${u.total_tokens || '-'}`;
            $('#pg-result-content').textContent = d.content || '(空响应)';
        } else {
            $('#pg-result-status').innerHTML = `<span class="text-red-600">✗ ${escapeHtml(String(d.status || '?'))}</span>`;
            $('#pg-result-usage').textContent = '-';
            $('#pg-result-content').textContent = '';
            $('#pg-result-error').textContent = typeof d.error === 'string' ? d.error : JSON.stringify(d.error);
            $('#pg-result-error').classList.remove('hidden');
        }
    } catch (e) {
        $('#pg-result-status').innerHTML = '<span class="text-red-600">✗ 异常</span>';
        $('#pg-result-error').textContent = e.message;
        $('#pg-result-error').classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = origText;
    }
}

$('#btn-playground').addEventListener('click', openPlaygroundModal);
$('#btn-close-playground').addEventListener('click', () => {
    $('#modal-playground').classList.add('hidden');
    $('#modal-playground').classList.remove('flex');
});
$('#pg-slug').addEventListener('change', renderPgModels);
$('#pg-temp').addEventListener('input', () => {
    $('#pg-temp-label').textContent = $('#pg-temp').value;
});
$('#btn-pg-run').addEventListener('click', runPlayground);

// ---------- 启动 ----------

loadStatus();
setInterval(loadStatus, 5000);

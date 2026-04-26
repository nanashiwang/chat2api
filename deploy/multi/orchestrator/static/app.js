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
    if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
    return data;
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

function renderRows(instances) {
    if (!instances.length) {
        $('#tbody').innerHTML = '<tr><td colspan="8" class="px-4 py-8 text-center text-gray-400">暂无账号，点击右上角「新增账号」开始</td></tr>';
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
        $('#secret-body').innerHTML = `
            <div><span class="font-semibold">slug:</span> <code class="bg-gray-100 px-1">${escapeHtml(d.slug)}</code></div>
            <div><span class="font-semibold">AUTHORIZATION:</span> <code class="bg-gray-100 px-1 break-all">${escapeHtml(d.AUTHORIZATION)}</code></div>
            <div><span class="font-semibold">ADMIN_PASSWORD:</span> <code class="bg-gray-100 px-1 break-all">${escapeHtml(d.ADMIN_PASSWORD)}</code></div>
            <div><span class="font-semibold">API_PREFIX:</span> <code class="bg-gray-100 px-1">${escapeHtml(d.API_PREFIX)}</code></div>
            <div><span class="font-semibold">PROXY_URL:</span> <code class="bg-gray-100 px-1 break-all">${escapeHtml(d.PROXY_URL || '(无)')}</code></div>
            <div class="pt-3 mt-3 border-t text-xs text-gray-500">
                调用示例：<br>
                <code class="bg-gray-100 px-1 mt-1 inline-block break-all">curl http://&lt;vps&gt;:60403/${escapeHtml(d.slug)}/v1/chat/completions -H "Authorization: Bearer ${escapeHtml(d.AUTHORIZATION)}"</code><br>
                Admin 后台：<a class="text-blue-600 underline" href="../${escapeHtml(d.slug)}/admin/login" target="_blank">/${escapeHtml(d.slug)}/admin/login</a>
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

// ---------- 启动 ----------

loadStatus();
setInterval(loadStatus, 5000);

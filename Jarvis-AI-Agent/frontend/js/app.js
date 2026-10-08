/**
 * Jarvis AI Agent - Frontend Application
 * WebSocket chat, sidebar panels, confirmations and reminder notifications.
 *
 * Security rule for this file: any text that came from the server, the LLM
 * or the user goes through escapeHtml() before it is put into innerHTML.
 */

// ── Configuration ──
const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`;
const API_BASE = '/api';
const RESPONSE_TIMEOUT_MS = 90_000;

// ── State ──
let ws = null;
let isConnected = false;
let reconnectDelay = 1000;
let pendingConfirmation = null;   // { confirm_id, content }
let currentProvider = null;       // null = server default
let currency = '₹';
let typingTimer = null;

// ── Helpers ──
function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/** Escape first, then apply a tiny safe subset of markdown. */
function formatMessage(text) {
    return escapeHtml(text)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/`([^`]+?)`/g, '<code>$1</code>')
        .replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>')
        .replace(/\n/g, '<br>');
}

function oneOf(value, allowed, fallback) {
    return allowed.includes(value) ? value : fallback;
}

function money(amount) {
    return `${currency}${Number(amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

async function api(path, options = {}) {
    const res = await fetch(`${API_BASE}${path}`, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch (_) { /* no body */ }
        throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
}

// ── WebSocket ──
function connectWebSocket() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        isConnected = true;
        reconnectDelay = 1000;
        updateConnectionStatus(true);
    };

    ws.onmessage = (event) => {
        let data;
        try { data = JSON.parse(event.data); } catch (_) { return; }
        handleMessage(data);
    };

    ws.onclose = () => {
        isConnected = false;
        hideTypingIndicator();
        updateConnectionStatus(false);
        setTimeout(connectWebSocket, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    };

    ws.onerror = () => { /* onclose handles reconnecting */ };
}

// ── Message Handling ──
function handleMessage(data) {
    switch (data.type) {
        case 'welcome':
            hideTypingIndicator();
            addMessage('assistant', data.content);
            if (data.providers) populateProviders(data.providers, data.default_provider);
            break;
        case 'chat':
            hideTypingIndicator();
            addMessage('assistant', data.content);
            break;
        case 'action':
            hideTypingIndicator();
            addMessage('assistant', data.content);
            refreshSidebar();
            break;
        case 'confirm':
            hideTypingIndicator();
            showConfirmation(data);
            break;
        case 'reminder':
            addMessage('assistant', data.content, 'reminder');
            showDesktopNotification(data.content);
            loadReminders();
            break;
        case 'error':
        case 'status':
            hideTypingIndicator();
            addMessage('assistant', `⚠️ ${data.content}`, 'error');
            break;
        case 'pong':
            break;
        default:
            hideTypingIndicator();
            if (data.content) addMessage('assistant', data.content);
    }
}

// ── Send Message ──
function sendMessage() {
    const input = document.getElementById('messageInput');
    const text = input.value.trim();
    if (!text) return;
    if (!isConnected) {
        addMessage('assistant', '⚠️ Not connected to Jarvis yet — please wait a moment.', 'error');
        return;
    }

    askNotificationPermission();
    addMessage('user', text);
    showTypingIndicator();

    ws.send(JSON.stringify({ type: 'chat', content: text, provider: currentProvider }));

    input.value = '';
    input.focus();
}

// ── UI Functions ──
function addMessage(role, content, variant = '') {
    const container = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = `message ${role}${variant ? ' ' + variant : ''}`;
    div.innerHTML = `
        <div class="message-bubble">
            <div class="message-content">${formatMessage(content)}</div>
            <div class="message-time">${escapeHtml(new Date().toLocaleTimeString())}</div>
        </div>
    `;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function showTypingIndicator() {
    const el = document.getElementById('typingIndicator');
    if (el) el.classList.add('visible');
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => {
        hideTypingIndicator();
        addMessage('assistant', '⚠️ No response from the server. Is the AI provider reachable?', 'error');
    }, RESPONSE_TIMEOUT_MS);
}

function hideTypingIndicator() {
    clearTimeout(typingTimer);
    const el = document.getElementById('typingIndicator');
    if (el) el.classList.remove('visible');
}

function updateConnectionStatus(connected) {
    const dot = document.getElementById('connectionDot');
    const text = document.getElementById('connectionText');
    if (dot) dot.className = `status-dot ${connected ? 'connected' : 'disconnected'}`;
    if (text) text.textContent = connected ? 'Online' : 'Reconnecting...';
}

// ── Notifications ──
function askNotificationPermission() {
    if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission().catch(() => {});
    }
}

function showDesktopNotification(text) {
    if ('Notification' in window && Notification.permission === 'granted') {
        try { new Notification('Jarvis', { body: text }); } catch (_) { /* some browsers block it */ }
    }
}

// ── Confirmation Modal ──
function showConfirmation(data) {
    pendingConfirmation = { confirm_id: data.confirm_id };
    const modal = document.getElementById('confirmModal');
    const msg = document.getElementById('confirmMessage');
    if (msg) msg.innerHTML = formatMessage(data.content);
    if (modal) modal.classList.add('visible');
}

function answerConfirmation(approved) {
    if (pendingConfirmation && ws && isConnected) {
        if (approved) showTypingIndicator();
        ws.send(JSON.stringify({
            type: 'confirm',
            confirm_id: pendingConfirmation.confirm_id,
            approved,
        }));
    }
    closeModal();
}

function confirmAction() { answerConfirmation(true); }
function cancelAction() { answerConfirmation(false); }

function closeModal() {
    const modal = document.getElementById('confirmModal');
    if (modal) modal.classList.remove('visible');
    pendingConfirmation = null;
}

// ── Sidebar Data Loading ──
function renderList(containerId, items, emptyText, renderItem) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = items.length ? '' : `<p class="empty">${escapeHtml(emptyText)}</p>`;
    items.forEach(item => {
        const div = document.createElement('div');
        div.className = 'sidebar-item';
        renderItem(div, item);
        container.appendChild(div);
    });
}

async function loadTasks() {
    try {
        const tasks = await api('/tasks');
        renderList('tasksList', tasks, 'No tasks yet', (div, task) => {
            const priority = oneOf(task.priority, ['low', 'medium', 'high'], 'medium');
            const id = Number(task.id);
            if (task.completed) div.classList.add('completed');
            div.innerHTML = `
                <div class="item-header">
                    <input type="checkbox" ${task.completed ? 'checked' : ''}
                           onchange="toggleTask(${id}, this.checked)" aria-label="Mark done">
                    <span class="item-title">${escapeHtml(task.title)}</span>
                    <span class="priority priority-${priority}">${priority}</span>
                </div>
                ${task.description ? `<p class="item-desc">${escapeHtml(task.description)}</p>` : ''}
                ${task.due_date ? `<p class="item-desc">Due ${escapeHtml(new Date(task.due_date).toLocaleString())}</p>` : ''}
                <button class="btn-delete" onclick="deleteTask(${id})" title="Delete task">✕</button>
            `;
        });
    } catch (e) {
        console.error('Failed to load tasks:', e);
    }
}

async function loadReminders() {
    try {
        const reminders = await api('/reminders');
        renderList('remindersList', reminders, 'No reminders', (div, rem) => {
            if (rem.triggered) div.classList.add('completed');
            div.innerHTML = `
                <div class="item-header">
                    <span class="item-title">🔔 ${escapeHtml(rem.message)}</span>
                </div>
                <p class="item-desc">${escapeHtml(new Date(rem.remind_at).toLocaleString())}</p>
            `;
        });
    } catch (e) {
        console.error('Failed to load reminders:', e);
    }
}

async function loadNotes() {
    try {
        const notes = await api('/notes');
        renderList('notesList', notes, 'No notes yet', (div, note) => {
            const content = note.content || '';
            const tags = (note.tags || '').split(',').map(t => t.trim()).filter(Boolean);
            div.innerHTML = `
                <div class="item-header">
                    <span class="item-title">📝 ${escapeHtml(note.title)}</span>
                </div>
                <p class="item-desc">${escapeHtml(content.substring(0, 100))}${content.length > 100 ? '...' : ''}</p>
                ${tags.length ? `<div class="tags">${tags.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
                <button class="btn-delete" onclick="deleteNote(${Number(note.id)})" title="Delete note">✕</button>
            `;
        });
    } catch (e) {
        console.error('Failed to load notes:', e);
    }
}

async function loadExpenses() {
    try {
        const data = await api('/expenses');
        const expenses = data.expenses || [];
        renderList('expensesList', expenses, 'No expenses yet', (div, exp) => {
            div.innerHTML = `
                <div class="item-header">
                    <span class="item-title">💰 ${escapeHtml(money(exp.amount))}</span>
                    <span class="tag">${escapeHtml(exp.category)}</span>
                </div>
                ${exp.description ? `<p class="item-desc">${escapeHtml(exp.description)}</p>` : ''}
                <p class="item-desc small">${escapeHtml(exp.date)}</p>
                <button class="btn-delete" onclick="deleteExpense(${Number(exp.id)})" title="Delete expense">✕</button>
            `;
        });
        const summaryEl = document.getElementById('expensesSummary');
        if (summaryEl) summaryEl.textContent = `Total: ${money(data.total)} (${expenses.length} items)`;
    } catch (e) {
        console.error('Failed to load expenses:', e);
    }
}

async function loadHabits() {
    try {
        const data = await api('/habits');
        renderList('habitsList', data.habits || [], 'No habits yet', (div, habit) => {
            const id = Number(habit.id);
            div.innerHTML = `
                <div class="item-header">
                    <span class="item-title">${habit.done_today ? '✅' : '⬜'} ${escapeHtml(habit.name)}</span>
                    <span class="priority priority-high">🔥 ${Number(habit.current_streak)}d</span>
                </div>
                <p class="item-desc">${Number(habit.total_completions)} total | ${escapeHtml(habit.frequency)}</p>
                ${habit.done_today ? '' : `<button class="btn-log" onclick="logHabit(${id})" title="Log today">✓ Log today</button>`}
            `;
        });
    } catch (e) {
        console.error('Failed to load habits:', e);
    }
}

async function loadSystemStatus() {
    try {
        const data = await api('/system/status');
        if (!data.success) return;
        document.getElementById('sysCpu').textContent = `CPU: ${data.cpu_percent}%`;
        document.getElementById('sysRam').textContent = `RAM: ${data.memory.percent}%`;
        document.getElementById('sysDisk').textContent = `DISK: ${data.disk.percent}%`;
    } catch (e) {
        console.error('Failed to load system status:', e);
    }
}

function refreshSidebar() {
    loadTasks();
    loadReminders();
    loadNotes();
    loadExpenses();
    loadHabits();
    loadSystemStatus();
}

// ── Sidebar Actions ──
async function runAndRefresh(fn, reload) {
    try {
        await fn();
    } catch (e) {
        addMessage('assistant', `⚠️ ${e.message}`, 'error');
    }
    reload();
}

function toggleTask(id, checked) {
    const path = checked ? `/tasks/${id}/complete` : `/tasks/${id}/reopen`;
    runAndRefresh(() => api(path, { method: 'PATCH' }), loadTasks);
}

function deleteTask(id) {
    if (!confirm('Delete this task?')) return;
    runAndRefresh(() => api(`/tasks/${id}`, { method: 'DELETE' }), loadTasks);
}

function deleteNote(id) {
    if (!confirm('Delete this note?')) return;
    runAndRefresh(() => api(`/notes/${id}`, { method: 'DELETE' }), loadNotes);
}

function deleteExpense(id) {
    if (!confirm('Delete this expense?')) return;
    runAndRefresh(() => api(`/expenses/${id}`, { method: 'DELETE' }), loadExpenses);
}

function logHabit(id) {
    runAndRefresh(() => api(`/habits/${id}/log`, { method: 'POST' }), loadHabits);
}

// ── Quick Actions ──
function quickAction(action) {
    const prompts = {
        'new-task': 'Create a new task: ',
        'set-reminder': 'Remind me to ',
        'new-note': 'Create a note: ',
        'web-search': 'Search the web for: ',
        'weather': 'What is the weather in ',
        'add-expense': `Add expense: ${currency}`,
        'pomodoro': 'Start a pomodoro for: ',
        'log-habit': 'Log habit: ',
        'daily-summary': 'Give me my daily productivity summary',
    };
    const input = document.getElementById('messageInput');
    if (!input) return;
    input.value = prompts[action] || '';
    input.focus();
    if (action === 'daily-summary') sendMessage();
}

// ── Tab Switching ──
function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    const targetBtn = document.querySelector(`[data-tab="${CSS.escape(tabName)}"]`);
    if (targetBtn) targetBtn.classList.add('active');
    const targetPanel = document.getElementById(`${tabName}Panel`);
    if (targetPanel) targetPanel.classList.add('active');
}

// ── Providers ──
function populateProviders(providers, defaultProvider) {
    const select = document.getElementById('providerSelect');
    if (!select) return;
    if (!providers.length) {
        select.innerHTML = '<option value="">No AI provider configured</option>';
        currentProvider = null;
        return;
    }
    if (!currentProvider || !providers.includes(currentProvider)) {
        currentProvider = defaultProvider || providers[0];
    }
    select.innerHTML = providers
        .map(p => `<option value="${escapeHtml(p)}" ${p === currentProvider ? 'selected' : ''}>${escapeHtml(p)}</option>`)
        .join('');
}

async function switchProvider(provider) {
    try {
        await api('/providers/switch', { method: 'POST', body: JSON.stringify({ provider }) });
        currentProvider = provider;
        addMessage('assistant', `Switched to **${provider}**.`);
    } catch (e) {
        addMessage('assistant', `⚠️ ${e.message}`, 'error');
    }
}

// ── Initialize ──
document.addEventListener('DOMContentLoaded', async () => {
    try {
        const health = await api('/health');
        currency = health.currency_symbol || currency;
        populateProviders(health.available_providers || [], health.default_provider);
    } catch (_) { /* server not up yet; the WebSocket welcome fills this in */ }

    connectWebSocket();
    refreshSidebar();
    setInterval(loadSystemStatus, 15000);

    const input = document.getElementById('messageInput');
    if (input) {
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
    }

    const providerSelect = document.getElementById('providerSelect');
    if (providerSelect) {
        providerSelect.addEventListener('change', (e) => switchProvider(e.target.value));
    }

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && pendingConfirmation) cancelAction();
    });
});

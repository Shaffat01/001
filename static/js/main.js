// ============ GLOBAL STATE ============
let currentSort = 'since_last_change';
let currentOrder = 'asc';
let selectedNodeId = null;
let contextNodeId = null;
let nodesData = [];
let pollIntervals = [];
let currentFilter = 'all';
let searchQuery = '';
let previousStatuses = {};
let soundEnabled = localStorage.getItem('soundEnabled') !== 'false';
let audioContext = null;
let browserNotificationsEnabled = localStorage.getItem('browserNotifications') !== 'false';
let notificationPermission = 'default';

// SSE state
let eventSource = null;
let sseActive = false;
let sseReconnectDelay = 1000;
let sseReconnectTimer = null;

// ============ THEME ============
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const newTheme = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    const icon = document.querySelector('.theme-toggle i');
    if (icon) icon.className = newTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';
}

const savedTheme = localStorage.getItem('theme') || 'light';
document.documentElement.setAttribute('data-theme', savedTheme);

// ============ LOAD NODES ============
function loadNodes() {
    fetch(`/api/nodes?sort=${currentSort}&order=${currentOrder}`)
        .then(r => r.json())
        .then(data => {
            nodesData = data.nodes;
            renderNodes(nodesData);
            updateLastUpdate();
            checkAndUpdateTabTitle();
        })
        .catch(err => {
            console.error('Error loading nodes:', err);
            updateStatusText('Error loading nodes');
        });
}

function updateLastUpdate() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString('en-GB', {
        timeZone: 'Asia/Dhaka', hour12: false,
        hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
    const el = document.getElementById('lastUpdate');
    if (el) el.textContent = timeStr;
}

// ============ FILTERING ============
function setFilter(filter) {
    currentFilter = filter;
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
    });
    renderNodes(nodesData);
}

function filterNodes() {
    searchQuery = document.getElementById('searchInput').value.toLowerCase();
    document.getElementById('searchClear').style.display = searchQuery ? 'block' : 'none';
    renderNodes(nodesData);
}

function clearSearch() {
    document.getElementById('searchInput').value = '';
    searchQuery = '';
    document.getElementById('searchClear').style.display = 'none';
    renderNodes(nodesData);
}

function applyFiltersAndSearch(nodes) {
    let filtered = nodes;
    if (currentFilter !== 'all') {
        const statusMap = { 'up': 'Node Up', 'down': 'Node Down', 'warning': 'Warning', 'unknown': 'Unknown' };
        filtered = filtered.filter(n => n.status === statusMap[currentFilter]);
    }
    if (searchQuery) {
        filtered = filtered.filter(n =>
            n.node_name.toLowerCase().includes(searchQuery) ||
            n.ip_address.toLowerCase().includes(searchQuery)
        );
    }
    return filtered;
}

// ============ RENDER ============
function renderNodes(nodes) {
    const tbody = document.getElementById('nodesBody');
    const filtered = applyFiltersAndSearch(nodes);

    const countEl = document.getElementById('resultCount');
    if (countEl) countEl.textContent = `Showing ${filtered.length} of ${nodes.length} nodes`;

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" class="loading-cell">
            <div class="loading-spinner">
                <i class="fas fa-search" style="color: var(--text-muted);"></i>
                <p>No nodes match your criteria</p>
            </div></td></tr>`;
        return;
    }

    let html = '';
    filtered.forEach(node => {
        const isSelected = selectedNodeId === node.id;
        const isDown = node.status === 'Node Down';
        const isWarning = node.status === 'Warning';
        const isUnknown = node.status === 'Unknown';

        let dotClass = 'dot-up', badgeClass = 'status-up', rowClass = '';
        if (isDown) { dotClass = 'dot-down'; badgeClass = 'status-down'; rowClass = 'row-down'; }
        else if (isWarning) { dotClass = 'dot-warning'; badgeClass = 'status-warning'; rowClass = 'row-warning'; }
        else if (isUnknown) { dotClass = 'dot-unknown'; badgeClass = 'status-unknown'; }

        // Notification detection only when SSE NOT active (SSE handles it)
        if (!sseActive) {
            const previousStatus = previousStatuses[node.id];
            if (previousStatus && previousStatus !== node.status) {
                rowClass += ' new-status';
                if (node.status === 'Node Down') {
                    playNodeDownSound();
                    showNotification(`🔴 <strong>${escapeHtml(node.node_name)}</strong> is DOWN<br><small>${escapeHtml(node.ip_address)}</small>`, 'error');
                    sendDesktopNotification('🔴 Node DOWN', `${node.node_name} (${node.ip_address}) is DOWN`, '', 'down-' + node.id);
                    showAlertBar(`${escapeHtml(node.node_name)} (${escapeHtml(node.ip_address)}) is DOWN!`, 'down');
                } else if (node.status === 'Node Up' && (previousStatus === 'Node Down' || previousStatus === 'Warning')) {
                    playNodeUpSound();
                    showNotification(`🟢 <strong>${escapeHtml(node.node_name)}</strong> is UP<br><small>${escapeHtml(node.ip_address)}</small>`, 'success');
                    sendDesktopNotification('🟢 Node UP', `${node.node_name} (${node.ip_address}) is UP`, '', 'up-' + node.id);
                    showAlertBar(`${escapeHtml(node.node_name)} (${escapeHtml(node.ip_address)}) is back UP!`, 'up');
                } else if (node.status === 'Warning') {
                    playWarningSound();
                    showNotification(`🟡 <strong>${escapeHtml(node.node_name)}</strong> - Warning`, 'warning');
                }
            }
        }
        previousStatuses[node.id] = node.status;

        if (isSelected) rowClass += ' selected';

        let rtClass = 'metric-good';
        if (node.response_time > 100) rtClass = 'metric-warning';
        if (node.response_time > 500 || !node.response_time) rtClass = 'metric-bad';

        let plClass = 'metric-good';
        if (node.packet_loss > 0) plClass = 'metric-warning';
        if (node.packet_loss >= 50) plClass = 'metric-bad';

        html += `
            <tr class="${rowClass}" data-id="${node.id}"
                onclick="selectNode(${node.id}, event)"
                ondblclick="editNode(${node.id})"
                oncontextmenu="showContextMenu(event, ${node.id})">
                <td class="col-status"><span class="status-dot ${dotClass}"></span></td>
                <td><div class="node-name-cell"><span title="${escapeHtml(node.node_name)}">${escapeHtml(node.node_name)}</span></div></td>
                <td><span class="ip-address">${escapeHtml(node.ip_address)}</span></td>
                <td><span class="metric-value ${rtClass}">${node.response_time_display}</span></td>
                <td><span class="metric-value ${plClass}">${node.packet_loss_display}</span></td>
                <td><span class="status-badge ${badgeClass}">${node.status}</span></td>
                <td><span class="time-ago"><i class="far fa-clock"></i> ${node.since_last_change}</span></td>
                <td class="col-actions">
                    <div class="row-actions">
                        <button class="row-action-btn ping" onclick="pingNode(${node.id}); event.stopPropagation();" title="Ping"><i class="fas fa-satellite-dish"></i></button>
                        <button class="row-action-btn" onclick="editNode(${node.id}); event.stopPropagation();" title="Edit"><i class="fas fa-edit"></i></button>
                        <button class="row-action-btn delete" onclick="deleteNode(${node.id}, '${escapeHtml(node.node_name).replace(/'/g, "\\'")}'); event.stopPropagation();" title="Delete"><i class="fas fa-trash"></i></button>
                    </div>
                </td>
            </tr>`;
    });
    tbody.innerHTML = html;
}

// ============ SORTING ============
function initSorting() {
    document.querySelectorAll('th.sortable').forEach(th => {
        th.addEventListener('click', function() {
            const sortField = this.dataset.sort;
            if (currentSort === sortField) {
                currentOrder = currentOrder === 'asc' ? 'desc' : 'asc';
            } else {
                currentSort = sortField;
                currentOrder = 'asc';
            }
            document.querySelectorAll('th.sortable').forEach(h => {
                h.classList.remove('active-sort');
                const arrow = h.querySelector('.sort-arrow');
                if (arrow) arrow.textContent = '';
            });
            this.classList.add('active-sort');
            const arrow = this.querySelector('.sort-arrow');
            if (arrow) arrow.textContent = currentOrder === 'asc' ? '▲' : '▼';
            loadNodes();
        });
    });
}

// ============ NODE ACTIONS ============
function selectNode(nodeId, event) {
    if (event) event.stopPropagation();
    selectedNodeId = nodeId;
    document.querySelectorAll('#nodesBody tr').forEach(tr => tr.classList.remove('selected'));
    const row = document.querySelector(`tr[data-id="${nodeId}"]`);
    if (row) row.classList.add('selected');
    hideContextMenu();
}

function editNode(nodeId) {
    window.location.href = `/node/${nodeId}`;
}

function pingNode(nodeId) {
    updateStatusText('Pinging node...');
    fetch(`/api/nodes/${nodeId}/ping`, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            updateStatusText(`Ping complete: ${data.ip_address} - ${data.status}`);
            loadNodes();
        })
        .catch(() => updateStatusText('Ping failed'));
}

function deleteNode(nodeId, nodeName) {
    if (!confirm(`Are you sure you want to delete "${nodeName}"?`)) return;
    fetch('/api/nodes/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            updateStatusText(`Node "${nodeName}" deleted`);
            loadNodes();
            updateStats();
        }
    });
}

// ============ CONTEXT MENU ============
function showContextMenu(event, nodeId) {
    event.preventDefault();
    event.stopPropagation();
    contextNodeId = nodeId;
    selectNode(nodeId, event);

    const menu = document.getElementById('contextMenu');
    menu.style.display = 'block';
    menu.style.left = event.clientX + 'px';
    menu.style.top = event.clientY + 'px';

    const menuRect = menu.getBoundingClientRect();
    if (menuRect.right > window.innerWidth) menu.style.left = (event.clientX - menuRect.width) + 'px';
    if (menuRect.bottom > window.innerHeight) menu.style.top = (event.clientY - menuRect.height) + 'px';
}

function hideContextMenu() {
    const menu = document.getElementById('contextMenu');
    if (menu) menu.style.display = 'none';
}

function editFromContext() { hideContextMenu(); if (contextNodeId) editNode(contextNodeId); }
function pingFromContext() { hideContextMenu(); if (contextNodeId) pingNode(contextNodeId); }
function deleteFromContext() {
    hideContextMenu();
    if (contextNodeId) {
        const node = nodesData.find(n => n.id === contextNodeId);
        deleteNode(contextNodeId, node ? node.node_name : 'Unknown');
    }
}
document.addEventListener('click', hideContextMenu);

// ============ TOOLBAR ============
function refreshAll() {
    const btn = document.getElementById('refreshBtn');
    const icon = btn.querySelector('i');
    icon.classList.add('spinning');
    updateStatusText('Refreshing all nodes...');
    fetch('/api/refresh', { method: 'POST' })
        .then(() => setTimeout(() => {
            loadNodes();
            updateStats();
            icon.classList.remove('spinning');
            updateStatusText('Refresh complete');
        }, 2000));
}

function pingSelected() {
    if (selectedNodeId) pingNode(selectedNodeId);
    else updateStatusText('No node selected');
}

// ============ STATS ============
function updateStats() {
    fetch('/api/stats')
        .then(r => r.json())
        .then(data => {
            animateNumber('statTotal', data.total);
            animateNumber('statUp', data.up);
            animateNumber('statDown', data.down);
            animateNumber('statWarning', data.warning || 0);
            animateNumber('statUnknown', data.unknown);
        });
}

function animateNumber(elementId, target) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const current = parseInt(el.textContent) || 0;
    if (current === target) return;
    const duration = 500, steps = 20;
    const stepValue = (target - current) / steps;
    let step = 0;
    const interval = setInterval(() => {
        step++;
        el.textContent = Math.round(current + stepValue * step);
        if (step >= steps) {
            el.textContent = target;
            clearInterval(interval);
        }
    }, duration / steps);
}

// ============ SSE ============
function startSSE() {
    if (eventSource) return;
    console.log('[SSE] Connecting...');
    eventSource = new EventSource('/api/stream');

    eventSource.onopen = function() {
        console.log('[SSE] Connected');
        sseActive = true;
        sseReconnectDelay = 1000;
        updateStatusText('Live stream connected');
    };

    eventSource.onmessage = function(e) {
        try {
            handleSSEEvent(JSON.parse(e.data));
        } catch (err) {
            console.error('[SSE] Parse error:', err);
        }
    };

    eventSource.onerror = function() {
        console.warn('[SSE] Connection lost');
        sseActive = false;
        if (eventSource) { eventSource.close(); eventSource = null; }
        clearTimeout(sseReconnectTimer);
        sseReconnectTimer = setTimeout(startSSE, sseReconnectDelay);
        sseReconnectDelay = Math.min(sseReconnectDelay * 2, 30000);
    };
}

function handleSSEEvent(msg) {
    if (msg.type === 'connected') return;

    if (msg.type === 'stats') {
        animateNumber('statTotal', msg.data.total);
        animateNumber('statUp', msg.data.up);
        animateNumber('statDown', msg.data.down);
        animateNumber('statWarning', msg.data.warning || 0);
        animateNumber('statUnknown', msg.data.unknown);
        checkAndUpdateTabTitle();
        return;
    }

    if (msg.type === 'node_status') {
        const n = msg.data;
        // Update local data
        const idx = nodesData.findIndex(x => x.id === n.id);
        if (idx >= 0) nodesData[idx].status = n.new_status;

        // Notifications
        if (n.new_status === 'Node Down' && n.old_status !== 'Node Down') {
            playNodeDownSound();
            showNotification(`🔴 <strong>${escapeHtml(n.node_name)}</strong> is DOWN<br><small>${escapeHtml(n.ip_address)}</small>`, 'error');
            sendDesktopNotification('🔴 Node DOWN', `${n.node_name} (${n.ip_address}) is DOWN`, '', 'down-' + n.id);
            showAlertBar(`${escapeHtml(n.node_name)} (${escapeHtml(n.ip_address)}) is DOWN!`, 'down');
        } else if (n.new_status === 'Node Up' && (n.old_status === 'Node Down' || n.old_status === 'Warning')) {
            playNodeUpSound();
            showNotification(`🟢 <strong>${escapeHtml(n.node_name)}</strong> is UP<br><small>${escapeHtml(n.ip_address)}</small>`, 'success');
            sendDesktopNotification('🟢 Node UP', `${n.node_name} (${n.ip_address}) is UP`, '', 'up-' + n.id);
            showAlertBar(`${escapeHtml(n.node_name)} (${escapeHtml(n.ip_address)}) is back UP!`, 'up');
        } else if (n.new_status === 'Warning') {
            playWarningSound();
            showNotification(`🟡 <strong>${escapeHtml(n.node_name)}</strong> - Warning`, 'warning');
        }

        // Re-render (cheap for 290 rows)
        renderNodes(nodesData);
        updateLastUpdate();
        checkAndUpdateTabTitle();
        return;
    }

    if (msg.type === 'nodes_changed') {
        msg.data.forEach(n => {
            const idx = nodesData.findIndex(x => x.id === n.id);
            if (idx >= 0) nodesData[idx] = n;
            else nodesData.push(n);
        });
        renderNodes(nodesData);
        checkAndUpdateTabTitle();
    }
}

// ============ POLLING FALLBACK ============
function startPolling() {
    stopPolling();
    // Backup polling only when SSE disconnected
    pollIntervals.push(setInterval(() => {
        if (!sseActive) { loadNodes(); updateStats(); }
    }, 30000));
}

function stopPolling() {
    pollIntervals.forEach(id => clearInterval(id));
    pollIntervals = [];
}

document.addEventListener('visibilitychange', function() {
    if (document.hidden) stopPolling();
    else {
        loadNodes(); updateStats(); startPolling();
    }
});

// ============ UTILITIES ============
function updateStatusText(text) {
    const el = document.getElementById('statusText');
    if (el) el.textContent = text;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

document.addEventListener('keydown', function(e) {
    if (e.key === 'F5') { e.preventDefault(); refreshAll(); }
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault(); document.getElementById('searchInput').focus();
    }
    if (e.key === 'Escape') { clearSearch(); document.getElementById('searchInput').blur(); }
});

// ============ INIT ============
document.addEventListener('DOMContentLoaded', function() {
    initSorting();
    loadNodes();
    updateStats();
    startSSE();        // primary
    startPolling();    // fallback (30s only when SSE down)

    document.addEventListener('click', function initAudioOnce() {
        initAudio();
        requestNotificationPermission();
        document.removeEventListener('click', initAudioOnce);
    }, { once: true });

    const savedTheme = localStorage.getItem('theme') || 'light';
    const icon = document.querySelector('.theme-toggle i');
    if (icon) icon.className = savedTheme === 'dark' ? 'fas fa-sun' : 'fas fa-moon';

    const soundBtn = document.getElementById('soundToggle');
    if (soundBtn) {
        const soundIcon = soundBtn.querySelector('i');
        if (soundEnabled) { soundIcon.className = 'fas fa-volume-up'; soundBtn.classList.remove('muted'); }
        else { soundIcon.className = 'fas fa-volume-mute'; soundBtn.classList.add('muted'); }
    }

    const notifBtn = document.getElementById('notifToggle');
    if (notifBtn) {
        const notifIcon = notifBtn.querySelector('i');
        if (browserNotificationsEnabled) { notifIcon.className = 'fas fa-bell'; notifBtn.classList.remove('muted'); }
        else { notifIcon.className = 'fas fa-bell-slash'; notifBtn.classList.add('muted'); }
    }

    setTimeout(() => {
        document.querySelectorAll('.flash-msg').forEach(msg => {
            msg.style.transition = 'opacity 0.5s, transform 0.5s';
            msg.style.opacity = '0';
            msg.style.transform = 'translateX(100%)';
            setTimeout(() => msg.remove(), 500);
        });
    }, 5000);
});

// ============ AUDIO ============
function initAudio() {
    if (!audioContext) {
        try { audioContext = new (window.AudioContext || window.webkitAudioContext)(); }
        catch (e) { console.error('Web Audio API not supported'); }
    }
}

function playTone(frequency, duration, type = 'sine', volume = 0.3) {
    if (!soundEnabled) return;
    if (!audioContext) initAudio();
    if (!audioContext) return;
    try {
        if (audioContext.state === 'suspended') audioContext.resume();
        const osc = audioContext.createOscillator();
        const gain = audioContext.createGain();
        osc.connect(gain);
        gain.connect(audioContext.destination);
        osc.type = type;
        osc.frequency.value = frequency;
        gain.gain.setValueAtTime(0, audioContext.currentTime);
        gain.gain.linearRampToValueAtTime(volume, audioContext.currentTime + 0.01);
        gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + duration);
        osc.start(audioContext.currentTime);
        osc.stop(audioContext.currentTime + duration);
    } catch (e) { console.error('Error playing tone:', e); }
}

function playSequence(notes) {
    if (!soundEnabled) return;
    notes.forEach((note, index) => {
        setTimeout(() => {
            playTone(note.freq, note.duration, note.type || 'sine', note.volume || 0.3);
        }, note.delay || index * 150);
    });
}

function playNodeDownSound() {
    playSequence([
        { freq: 880, duration: 0.15, delay: 0, volume: 0.4 },
        { freq: 660, duration: 0.15, delay: 150, volume: 0.4 },
        { freq: 440, duration: 0.3, delay: 300, volume: 0.5 }
    ]);
}

function playNodeUpSound() {
    playSequence([
        { freq: 523.25, duration: 0.1, delay: 0, volume: 0.3 },
        { freq: 659.25, duration: 0.1, delay: 100, volume: 0.3 },
        { freq: 783.99, duration: 0.2, delay: 200, volume: 0.3 }
    ]);
}

function playWarningSound() {
    playSequence([
        { freq: 600, duration: 0.1, delay: 0, volume: 0.3 },
        { freq: 600, duration: 0.1, delay: 200, volume: 0.3 }
    ]);
}

// ============ DESKTOP NOTIFICATIONS ============
function requestNotificationPermission() {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') notificationPermission = 'granted';
    else if (Notification.permission !== 'denied') {
        Notification.requestPermission().then(p => notificationPermission = p);
    }
}

function sendDesktopNotification(title, body, icon, tag) {
    if (browserNotificationsEnabled && ('Notification' in window) && Notification.permission === 'granted') {
        try {
            const notif = new Notification(title, {
                body, icon: icon || '',
                tag: tag || 'netmonitor-' + Date.now(),
                requireInteraction: false, silent: false
            });
            setTimeout(() => notif.close(), 8000);
            notif.onclick = () => { window.focus(); notif.close(); };
        } catch (e) { console.error('[NOTIFICATION] Error:', e); }
    }
}

// ============ TAB TITLE ============
let originalTitle = 'NetMonitor Pro';
let titleBlinkInterval = null;

function updateTabTitle(downCount) {
    if (titleBlinkInterval) { clearInterval(titleBlinkInterval); titleBlinkInterval = null; }
    if (downCount > 0) {
        let blink = true;
        titleBlinkInterval = setInterval(() => {
            document.title = blink
                ? `🔴 ${downCount} NODE DOWN! - NetMonitor Pro`
                : `⚠️ ALERT: ${downCount} nodes down - NetMonitor Pro`;
            blink = !blink;
        }, 1500);
    } else {
        document.title = '✅ All Online - NetMonitor Pro';
        setTimeout(() => document.title = originalTitle, 3000);
    }
}

// ============ ALERT BAR ============
function showAlertBar(message, type) {
    let existing = document.getElementById('urgentAlertBar');
    if (existing) existing.remove();

    const bar = document.createElement('div');
    bar.id = 'urgentAlertBar';
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:99999;padding:12px 20px;text-align:center;font-size:14px;font-weight:600;font-family:Inter,sans-serif;animation:slideDown 0.3s ease;cursor:pointer;';

    if (type === 'down') {
        bar.style.background = 'linear-gradient(90deg, #dc2626, #ef4444)';
        bar.style.color = 'white';
        bar.innerHTML = '🔴 ' + message + ' &nbsp; <span style="text-decoration:underline;">Click to view</span>';
    } else if (type === 'up') {
        bar.style.background = 'linear-gradient(90deg, #059669, #10b981)';
        bar.style.color = 'white';
        bar.innerHTML = '🟢 ' + message;
    } else {
        bar.style.background = 'linear-gradient(90deg, #d97706, #f59e0b)';
        bar.style.color = 'white';
        bar.innerHTML = '🟡 ' + message;
    }

    bar.onclick = () => { window.focus(); if (type === 'down') setFilter('down'); bar.remove(); };
    document.body.appendChild(bar);
    if (type !== 'down') setTimeout(() => bar.parentNode && bar.remove(), 10000);
}

function checkAndUpdateTabTitle() {
    if (!nodesData || nodesData.length === 0) return;
    let downCount = 0;
    nodesData.forEach(n => { if (n.status === 'Node Down') downCount++; });
    updateTabTitle(downCount);
}

// ============ TOGGLES ============
function toggleBrowserNotifications() {
    browserNotificationsEnabled = !browserNotificationsEnabled;
    localStorage.setItem('browserNotifications', browserNotificationsEnabled);
    const btn = document.getElementById('notifToggle');
    const icon = btn.querySelector('i');
    if (browserNotificationsEnabled) {
        requestNotificationPermission();
        icon.className = 'fas fa-bell';
        btn.classList.remove('muted');
        showNotification('🔔 Desktop notifications enabled', 'success');
        setTimeout(() => sendDesktopNotification('✅ NetMonitor Pro', 'Desktop notifications enabled!', '', 'test'), 1000);
    } else {
        icon.className = 'fas fa-bell-slash';
        btn.classList.add('muted');
        showNotification('🔕 Desktop notifications disabled', 'info');
    }
}

function toggleSound() {
    soundEnabled = !soundEnabled;
    localStorage.setItem('soundEnabled', soundEnabled);
    const btn = document.getElementById('soundToggle');
    const icon = btn.querySelector('i');
    if (soundEnabled) {
        icon.className = 'fas fa-volume-up';
        btn.classList.remove('muted');
        playNodeUpSound();
        showNotification('🔊 Sound enabled', 'success');
    } else {
        icon.className = 'fas fa-volume-mute';
        btn.classList.add('muted');
        showNotification('🔇 Sound muted', 'info');
    }
}

function showNotification(message, type = 'info') {
    const container = document.getElementById('notificationContainer') || createNotificationContainer();
    const notif = document.createElement('div');
    notif.className = `app-notification notif-${type}`;
    notif.innerHTML = `
        <div class="notif-icon"><i class="fas fa-${type === 'success' ? 'check-circle' : type === 'error' ? 'times-circle' : type === 'warning' ? 'exclamation-triangle' : 'info-circle'}"></i></div>
        <div class="notif-content">${message}</div>
        <button class="notif-close" onclick="this.parentElement.remove()"><i class="fas fa-times"></i></button>
    `;
    container.appendChild(notif);
    setTimeout(() => {
        notif.style.animation = 'slideOutRight 0.3s ease forwards';
        setTimeout(() => notif.remove(), 300);
    }, 5000);
}

function createNotificationContainer() {
    const c = document.createElement('div');
    c.id = 'notificationContainer';
    c.className = 'notification-container';
    document.body.appendChild(c);
    return c;
}
// ===== WebSocket 连接 =====
const socket = io();

// ===== DOM 元素 =====
const statusEl = document.getElementById('status');
const statusTextEl = document.getElementById('status-text');
const totalDetectionsEl = document.getElementById('total-detections');
const totalFacesEl = document.getElementById('total-faces');
const devicesEl = document.getElementById('devices');
const lastTimeEl = document.getElementById('last-time');
const historyListEl = document.getElementById('history-list');
const liveListEl = document.getElementById('live-list');
const recordCountEl = document.getElementById('record-count');
const facesGridEl = document.getElementById('faces-grid');
const faceCountEl = document.getElementById('face-count');
const chatMessages = document.getElementById('chat-messages');
const chatInput = document.getElementById('chat-input');
const chatSendBtn = document.getElementById('chat-send-btn');

// ===== WebSocket 事件 =====
socket.on('connect', () => {
    statusEl.classList.add('connected');
    statusEl.classList.remove('disconnected');
    statusTextEl.textContent = '已连接';
    loadStats();
    loadHistory();
    loadFaceImages();
});

socket.on('disconnect', () => {
    statusEl.classList.remove('connected');
    statusEl.classList.add('disconnected');
    statusTextEl.textContent = '连接断开';
});

// ===== 设备在线检测 =====
let deviceLastSeen = 0;

socket.on('new_detection', (data) => {
    deviceLastSeen = Date.now();
    document.getElementById('device-status').textContent = '设备: 🟢 在线';
    document.getElementById('device-status').style.color = '#22c55e';
    addLiveDetection(data);
    addHistoryItem(data, true);
    loadStats();
});

socket.on('new_face_image', (data) => {
    addFaceImage(data, true);
});

setInterval(() => {
    const el = document.getElementById('device-status');
    if (Date.now() - deviceLastSeen > 10000) {
        el.textContent = '设备: 🔴 离线';
        el.style.color = '#ef4444';
    }
}, 5000);

// ===== 统计 =====
async function loadStats() {
    try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        totalDetectionsEl.textContent = data.total_detections;
        totalFacesEl.textContent = data.total_faces;
        devicesEl.textContent = data.devices;
        lastTimeEl.textContent = data.last_detection;
    } catch (e) {
        console.error('加载统计失败:', e);
    }
}

// ===== 历史记录 =====
async function loadHistory() {
    try {
        const res = await fetch('/api/detections?limit=50');
        const data = await res.json();
        historyListEl.innerHTML = '';
        if (data.length === 0) {
            historyListEl.innerHTML = '<div class="empty-state"><div class="icon">📭</div><div>暂无数据</div></div>';
        } else {
            data.forEach(item => addHistoryItem(item, false));
        }
        recordCountEl.textContent = `${data.length} 条记录`;
    } catch (e) {
        console.error('加载历史失败:', e);
    }
}

function addHistoryItem(data, isNew) {
    const div = document.createElement('div');
    div.className = 'detection-item' + (isNew ? ' new-detection' : '');

    let facesHtml = '';
    if (data.faces && data.faces.length > 0) {
        facesHtml = '<div class="face-box">';
        data.faces.forEach((face, i) => {
            const score = (face.score * 100).toFixed(1);
            facesHtml += `<div class="face-tag">👤 ${i+1}: <span class="score">${score}%</span></div>`;
        });
        facesHtml += '</div>';
    }

    div.innerHTML = `
        <div class="detection-header">
            <span class="detection-time">${data.datetime || '未知时间'}</span>
            <span class="face-count">${data.face_count} 张人脸</span>
        </div>
        <div class="device-info">设备: ${data.device || 'ESP32'} | 帧: ${data.frame || '-'}</div>
        ${facesHtml}
    `;

    if (isNew) {
        historyListEl.insertBefore(div, historyListEl.firstChild);
        while (historyListEl.children.length > 100) {
            historyListEl.removeChild(historyListEl.lastChild);
        }
    } else {
        historyListEl.appendChild(div);
    }
}

// ===== 实时检测 =====
function addLiveDetection(data) {
    const div = document.createElement('div');
    div.className = 'detection-item new-detection';
    const now = data.time || new Date().toLocaleTimeString();

    let facesHtml = '';
    if (data.faces && data.faces.length > 0) {
        facesHtml = '<div class="face-box">';
        data.faces.forEach((face, i) => {
            const score = (face.score * 100).toFixed(1);
            const box = face.box;
            facesHtml += `<div class="face-tag">👤 ${i+1}: <span class="score">${score}%</span><br>📍 (${box[0]},${box[1]})-(${box[2]},${box[3]})</div>`;
        });
        facesHtml += '</div>';
    }

    div.innerHTML = `
        <div class="detection-header">
            <span class="detection-time">${now}</span>
            <span class="face-count">${data.face_count} 张人脸</span>
        </div>
        ${facesHtml}
    `;

    liveListEl.insertBefore(div, liveListEl.firstChild);
    while (liveListEl.children.length > 20) {
        liveListEl.removeChild(liveListEl.lastChild);
    }
}

// ===== 人脸图片 =====
async function loadFaceImages() {
    try {
        const res = await fetch('/api/face_images?limit=20');
        const data = await res.json();
        facesGridEl.innerHTML = '';
        if (data.length === 0) {
            facesGridEl.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">😶</div><div>暂无人脸图片</div></div>';
        } else {
            data.forEach(item => addFaceImage(item, false));
        }
        faceCountEl.textContent = `${data.length} 张`;
    } catch (e) {
        console.error('加载人脸图片失败:', e);
    }
}

function addFaceImage(data, isNew) {
    const div = document.createElement('div');
    div.className = 'face-card' + (isNew ? ' new-detection' : '');
    const score = (data.score * 100).toFixed(1);
    div.innerHTML = `
        <img src="${data.img_url}" alt="人脸" loading="lazy">
        <div class="info">
            <span class="score">${score}%</span><br>
            <span>${data.datetime || ''}</span>
        </div>
    `;
    if (isNew) {
        facesGridEl.insertBefore(div, facesGridEl.firstChild);
        while (facesGridEl.children.length > 20) {
            facesGridEl.removeChild(facesGridEl.lastChild);
        }
    } else {
        facesGridEl.appendChild(div);
    }
}

// ===== 语音聊天 =====
function addChatMsg(role, text) {
    const div = document.createElement('div');
    div.className = 'chat-msg ' + role;
    const now = new Date().toLocaleTimeString();
    div.innerHTML = `<div class="bubble">${text}</div><div class="time">${now}</div>`;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function sendChat() {
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = '';
    addChatMsg('user', text);
    chatSendBtn.disabled = true;
    try {
        const res = await fetch('/api/voice_chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        const data = await res.json();
        addChatMsg('ai', data.reply || '(无回复)');
        if (data.actions) {
            data.actions.forEach(a => {
                if (a.action === 'fan') updateToggle('btn-fan', a.state === 'on');
                if (a.action === 'led') updateToggle('btn-led', a.state === 'on');
                if (a.action === 'buzzer') updateToggle('btn-buzzer', a.state === 'on');
                if (a.action === 'face_detect') updateToggle('btn-face', a.state === 'on');
            });
        }
    } catch (e) {
        addChatMsg('ai', '请求失败: ' + e.message);
    }
    chatSendBtn.disabled = false;
}

// ===== 设备控制 =====
function updateToggle(id, on) {
    const btn = document.getElementById(id);
    if (btn) btn.className = 'toggle-btn ' + (on ? 'on' : 'off');
}

async function toggleDevice(device, btn) {
    const isOn = btn.classList.contains('on');
    const newState = isOn ? 'off' : 'on';
    try {
        await fetch('/api/device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: device, state: newState })
        });
        updateToggle(btn.id, !isOn);
    } catch (e) {
        console.error('设备控制失败:', e);
    }
}

// ===== RGB 灯带 =====
async function setRgb(color) {
    try {
        await fetch('/api/device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'rgb', state: 'on', color: color })
        });
    } catch (e) {
        console.error('RGB 控制失败:', e);
    }
}

// ===== 初始化 =====
loadStats();
loadHistory();
loadFaceImages();

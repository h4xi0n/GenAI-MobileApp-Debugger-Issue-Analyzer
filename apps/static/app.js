/**
 * GenAI Mobile App Debugger & Issue Analyzer - Dashboard Frontend Logic
 * Author: Arun Krishnan
 */

document.addEventListener('DOMContentLoaded', () => {
    // State
    const state = {
        activeTab: 'tab-logcat',
        logLevelFilter: 'ALL',
        logSearchQuery: '',
        autoScroll: true,
        lastFrameTimestamp: null,
        frameCount: 0,
        fps: 0.0,
        latestReport: null,
        prompts: null,
        liveStreamActive: true,
    };

    // DOM Elements
    const elements = {
        serverStatus: document.getElementById('server-status'),
        deviceDot: document.getElementById('device-dot'),
        deviceLabel: document.getElementById('device-label'),
        deviceSelect: document.getElementById('device-select'),
        btnToggleStream: document.getElementById('btn-toggle-stream'),
        btnDiagnose: document.getElementById('btn-diagnose'),
        btnDiagnoseTab: document.getElementById('btn-trigger-diagnose-tab'),
        btnKeyframe: document.getElementById('btn-keyframe'),
        screenCanvas: document.getElementById('screen-canvas'),
        screenPlaceholder: document.getElementById('screen-placeholder'),
        screenStateBadge: document.getElementById('screen-state-badge'),
        fpsCounter: document.getElementById('fps-counter'),
        resCounter: document.getElementById('resolution-counter'),
        tabBtns: document.querySelectorAll('.tab-btn'),
        tabPanes: document.querySelectorAll('.tab-pane'),
        logcatTerminal: document.getElementById('logcat-terminal'),
        logSearchInput: document.getElementById('logcat-search'),
        autoscrollToggle: document.getElementById('autoscroll-toggle'),
        btnClearLogs: document.getElementById('btn-clear-logs'),
        levelBtns: document.querySelectorAll('.level-btn'),
        // Diagnostics
        diagPlaceholder: document.getElementById('diagnostics-placeholder'),
        reportContent: document.getElementById('report-content'),
        repId: document.getElementById('rep-id'),
        repTime: document.getElementById('rep-time'),
        repConfFill: document.getElementById('rep-conf-fill'),
        repConfVal: document.getElementById('rep-conf-val'),
        repSummary: document.getElementById('rep-summary'),
        repRootCause: document.getElementById('rep-root-cause'),
        repAffected: document.getElementById('rep-affected'),
        repFix: document.getElementById('rep-fix'),
        // Patch
        diffContent: document.getElementById('diff-content'),
        cursorPreview: document.getElementById('cursor-prompt-preview'),
        btnCopyCursor: document.getElementById('btn-copy-cursor'),
        btnCopyAntigravity: document.getElementById('btn-copy-antigravity'),
        // Controls
        pkgInput: document.getElementById('target-package-input'),
        btnRestartApp: document.getElementById('btn-restart-app'),
        btnClearAppData: document.getElementById('btn-clear-app-data'),
        btnClearLogcatCmd: document.getElementById('btn-clear-logcat-cmd'),
        btnSimNpe: document.getElementById('btn-sim-npe'),
        btnSimAnr: document.getElementById('btn-sim-anr'),
        btnSimLeak: document.getElementById('btn-sim-leak'),
        toastContainer: document.getElementById('toast-container'),
    };

    const canvasCtx = elements.screenCanvas.getContext('2d');

    // Toast Utility
    function showToast(message, duration = 3000) {
        const toast = document.createElement('div');
        toast.className = 'toast';
        toast.textContent = message;
        elements.toastContainer.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 200);
        }, duration);
    }

    // Tabs Navigation
    elements.tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabId = btn.getAttribute('data-tab');
            elements.tabBtns.forEach(b => b.classList.remove('active'));
            elements.tabPanes.forEach(p => p.classList.remove('active'));

            btn.classList.add('active');
            const targetPane = document.getElementById(tabId);
            if (targetPane) targetPane.classList.add('active');
            state.activeTab = tabId;
        });
    });

    // -------------------------------------------------------------------------
    // WebSocket Connections
    // -------------------------------------------------------------------------
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;

    // 1. Screen Stream WebSocket
    let streamWs = null;
    let fallbackFrameTimer = null;

    /**
     * Connects to the /ws/stream WebSocket endpoint with automatic reconnection.
     * Manages incoming frame rendering and maintains a watchdog timer that requests
     * fallback keyframes if no frames are received within 1500ms while streaming is active.
     */
    function connectStreamWs() {
        streamWs = new WebSocket(`${protocol}//${host}/ws/stream`);
        streamWs.onopen = () => {
            console.log('Screen stream WebSocket connected');
            // Request first frame immediately upon connection
            streamWs.send(JSON.stringify({ type: 'request_frame' }));

            // Watchdog fallback: auto-request frame if liveStreamActive is true and no frame arrived in 1.5s
            if (fallbackFrameTimer) clearInterval(fallbackFrameTimer);
            fallbackFrameTimer = setInterval(() => {
                if (state.liveStreamActive && streamWs && streamWs.readyState === WebSocket.OPEN) {
                    const now = performance.now();
                    if (!state.lastFrameTimestamp || (now - state.lastFrameTimestamp > 1500)) {
                        try {
                            streamWs.send(JSON.stringify({ type: 'request_frame' }));
                        } catch (e) {
                            console.warn('Fallback frame request failed:', e);
                        }
                    }
                }
            }, 1500);
        };

        streamWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'frame' && data.payload) {
                    renderFrame(data.payload);
                }
            } catch (err) {
                console.error('Error handling frame ws msg:', err);
            }
        };

        streamWs.onclose = () => {
            console.warn('Stream WebSocket closed, reconnecting in 2s...');
            if (fallbackFrameTimer) {
                clearInterval(fallbackFrameTimer);
                fallbackFrameTimer = null;
            }
            setTimeout(connectStreamWs, 2000);
        };
    }

    /**
     * Decodes and paints a ScreenFrame payload onto the screen canvas.
     * Updates resolution indicators, visual crash badges, and computes
     * rolling average FPS telemetry.
     *
     * @param {Object} frame - Serialized ScreenFrame model from server.
     */
    function renderFrame(frame) {
        const img = new Image();
        img.onload = () => {
            elements.screenCanvas.width = frame.width || img.width;
            elements.screenCanvas.height = frame.height || img.height;
            canvasCtx.drawImage(img, 0, 0);

            // Hide placeholder
            elements.screenPlaceholder.style.display = 'none';

            // Update telemetry
            elements.resCounter.textContent = `${frame.width} x ${frame.height}`;

            // State badge
            if (frame.screen_state) {
                elements.screenStateBadge.textContent = frame.screen_state;
                if (frame.screen_state !== 'NORMAL') {
                    elements.screenStateBadge.className = 'badge crash';
                } else {
                    elements.screenStateBadge.className = 'badge';
                }
            }

            // Calculate FPS using exponential moving average
            const now = performance.now();
            if (state.lastFrameTimestamp) {
                const delta = now - state.lastFrameTimestamp;
                const instantFps = 1000 / delta;
                state.fps = state.fps * 0.8 + instantFps * 0.2;
                elements.fpsCounter.textContent = `${state.fps.toFixed(1)} FPS`;
            }
            state.lastFrameTimestamp = now;
        };
        img.src = `data:${frame.mime_type || 'image/webp'};base64,${frame.image_bytes_base64}`;
    }

    // 2. Logcat WebSocket
    let logcatWs = null;
    function connectLogcatWs() {
        logcatWs = new WebSocket(`${protocol}//${host}/ws/logcat`);
        logcatWs.onopen = () => {
            console.log('Logcat WebSocket connected');
        };

        logcatWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'logcat' && data.payload) {
                    appendLogEntry(data.payload);
                }
            } catch (err) {
                console.error('Error handling logcat ws msg:', err);
            }
        };

        logcatWs.onclose = () => {
            setTimeout(connectLogcatWs, 2000);
        };
    }

    function appendLogEntry(entry) {
        const line = document.createElement('div');
        line.className = `log-line level-${entry.level} ${entry.is_stacktrace ? 'is-stack' : ''}`;
        line.dataset.level = entry.level;
        line.dataset.tag = entry.tag.toLowerCase();
        line.dataset.msg = entry.message.toLowerCase();

        line.innerHTML = `
            <span class="log-time">${entry.timestamp}</span>
            <span class="log-pid">${entry.pid}/${entry.tid}</span>
            <span class="log-tag">${entry.tag}:</span>
            <span class="log-msg">${escapeHtml(entry.message)}</span>
        `;

        // Check active filters
        applyFilterToLine(line);

        elements.logcatTerminal.appendChild(line);

        // Keep buffer bounded in DOM
        if (elements.logcatTerminal.children.length > 500) {
            elements.logcatTerminal.removeChild(elements.logcatTerminal.firstChild);
        }

        if (state.autoScroll) {
            elements.logcatTerminal.scrollTop = elements.logcatTerminal.scrollHeight;
        }
    }

    function escapeHtml(text) {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function applyFilterToLine(line) {
        const level = line.dataset.level;
        const tag = line.dataset.tag;
        const msg = line.dataset.msg;

        const levelMatches = (state.logLevelFilter === 'ALL' || state.logLevelFilter === level);
        const searchMatches = (!state.logSearchQuery || tag.includes(state.logSearchQuery) || msg.includes(state.logSearchQuery));

        line.style.display = (levelMatches && searchMatches) ? 'flex' : 'none';
    }

    function refilterAllLogs() {
        const lines = elements.logcatTerminal.querySelectorAll('.log-line');
        lines.forEach(line => applyFilterToLine(line));
    }

    // 3. Control WebSocket
    let controlWs = null;
    function connectControlWs() {
        controlWs = new WebSocket(`${protocol}//${host}/ws/control`);
        controlWs.onopen = () => console.log('Control WebSocket connected');
        controlWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'diagnostic_report' && data.payload) {
                    renderDiagnosticReport(data.payload);
                } else if (data.type === 'command_ack') {
                    showToast(`Command executed: ${data.command_type}`);
                }
            } catch (err) {
                console.error('Error handling control ws msg:', err);
            }
        };
        controlWs.onclose = () => setTimeout(connectControlWs, 2000);
    }

    function sendControlCommand(type, payload = {}) {
        const cmd = {
            command_id: 'cmd-' + Math.random().toString(36).substring(2, 9),
            type: type,
            payload: payload,
            timestamp: new Date().toISOString()
        };
        if (controlWs && controlWs.readyState === WebSocket.OPEN) {
            controlWs.send(JSON.stringify(cmd));
        } else {
            // Fallback to REST
            fetch('/api/device/command', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(cmd)
            }).then(r => r.json()).then(res => {
                showToast(`Command response: ${res.status}`);
            }).catch(e => console.error(e));
        }
    }

    // -------------------------------------------------------------------------
    // Event Listeners
    // -------------------------------------------------------------------------

    // Logcat level filters
    elements.levelBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            elements.levelBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            state.logLevelFilter = btn.getAttribute('data-level');
            refilterAllLogs();
        });
    });

    // Logcat search
    elements.logSearchInput.addEventListener('input', (e) => {
        state.logSearchQuery = e.target.value.toLowerCase().trim();
        refilterAllLogs();
    });

    // Auto-scroll toggle
    elements.autoscrollToggle.addEventListener('change', (e) => {
        state.autoScroll = e.target.checked;
    });

    // Clear logs
    elements.btnClearLogs.addEventListener('click', () => {
        elements.logcatTerminal.innerHTML = '';
    });

    // Snapshot keyframe button: requests immediate capture via WebSocket or REST fallback
    elements.btnKeyframe.addEventListener('click', () => {
        showToast('Requesting fresh screen frame...');
        if (streamWs && streamWs.readyState === WebSocket.OPEN) {
            streamWs.send(JSON.stringify({ type: 'request_frame' }));
        } else {
            fetch('/api/frames/latest')
                .then(r => r.json())
                .then(frame => renderFrame(frame))
                .catch(err => console.error(err));
        }
    });

    // Toggle Live Stream button: toggles continuous streaming and synchronizes state
    if (elements.btnToggleStream) {
        elements.btnToggleStream.addEventListener('click', () => {
            state.liveStreamActive = !state.liveStreamActive;
            if (state.liveStreamActive) {
                elements.btnToggleStream.textContent = '🔴 Live Stream';
                elements.btnToggleStream.className = 'btn btn-sm btn-primary';
                showToast('Live screen streaming resumed');
            } else {
                elements.btnToggleStream.textContent = '⚪ Stream Paused';
                elements.btnToggleStream.className = 'btn btn-sm btn-secondary';
                showToast('Screen streaming paused');
            }
            // Notify server via control WebSocket (or REST fallback) and stream WebSocket
            sendControlCommand('TOGGLE_STREAM', { active: state.liveStreamActive });
            if (streamWs && streamWs.readyState === WebSocket.OPEN) {
                try {
                    streamWs.send(JSON.stringify({
                        type: 'toggle_stream',
                        payload: { active: state.liveStreamActive }
                    }));
                } catch (e) {
                    console.error('Failed to send toggle_stream via websocket:', e);
                }
            }
        });
    }

    // Trigger Diagnose
    async function triggerDiagnosis() {
        showToast('Running Multimodal GenAI Diagnosis...');
        try {
            const resp = await fetch('/api/diagnose', { method: 'POST' });
            if (!resp.ok) throw new Error('Diagnosis API returned error');
            const report = await resp.json();
            renderDiagnosticReport(report);

            // Fetch patch
            const patchResp = await fetch('/api/patch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(report)
            });
            if (patchResp.ok) {
                const patchData = await patchResp.json();
                renderPatchPrompts(patchData);
            }

            // Switch to diagnostics tab
            const diagTabBtn = document.querySelector('[data-tab="tab-diagnostics"]');
            if (diagTabBtn) diagTabBtn.click();
        } catch (err) {
            console.error('Diagnosis failed:', err);
            showToast('Failed to run diagnosis: ' + err.message);
        }
    }

    elements.btnDiagnose.addEventListener('click', triggerDiagnosis);
    if (elements.btnDiagnoseTab) {
        elements.btnDiagnoseTab.addEventListener('click', triggerDiagnosis);
    }

    function renderDiagnosticReport(report) {
        state.latestReport = report;
        elements.diagPlaceholder.style.display = 'none';
        elements.reportContent.style.display = 'flex';

        elements.repId.textContent = report.report_id;
        elements.repTime.textContent = report.timestamp.replace('T', ' ').substring(0, 19);
        const confPercent = Math.round(report.confidence_score * 100);
        elements.repConfFill.style.width = `${confPercent}%`;
        elements.repConfVal.textContent = `${confPercent}%`;

        elements.repSummary.textContent = report.error_summary;
        elements.repRootCause.textContent = report.root_cause_analysis;

        elements.repAffected.innerHTML = '';
        (report.affected_components || []).forEach(comp => {
            const li = document.createElement('li');
            li.innerHTML = `<code>${escapeHtml(comp)}</code>`;
            elements.repAffected.appendChild(li);
        });

        elements.repFix.textContent = report.suggested_fix_summary;
    }

    function renderPatchPrompts(prompts) {
        state.prompts = prompts;
        if (prompts.cursor && prompts.cursor.proposed_diff) {
            elements.diffContent.textContent = prompts.cursor.proposed_diff;
        }
        if (prompts.cursor && prompts.cursor.formatted_prompt) {
            elements.cursorPreview.textContent = prompts.cursor.formatted_prompt;
        }
    }

    // Copy prompt buttons
    elements.btnCopyCursor.addEventListener('click', () => {
        if (state.prompts && state.prompts.cursor) {
            navigator.clipboard.writeText(state.prompts.cursor.formatted_prompt);
            showToast('📋 Cursor Composer prompt copied to clipboard!');
        } else {
            showToast('Run diagnosis first to generate prompt.');
        }
    });

    elements.btnCopyAntigravity.addEventListener('click', () => {
        if (state.prompts && state.prompts.antigravity) {
            navigator.clipboard.writeText(state.prompts.antigravity.formatted_prompt);
            showToast('🪐 Antigravity subagent handoff copied to clipboard!');
        } else {
            showToast('Run diagnosis first to generate handoff.');
        }
    });

    // Device Controls
    elements.btnRestartApp.addEventListener('click', () => {
        const pkg = elements.pkgInput.value.trim();
        sendControlCommand('RESTART_PACKAGE', { package: pkg });
    });

    elements.btnClearAppData.addEventListener('click', () => {
        const pkg = elements.pkgInput.value.trim();
        sendControlCommand('CLEAR_PACKAGE_DATA', { package: pkg });
    });

    elements.btnClearLogcatCmd.addEventListener('click', () => {
        sendControlCommand('CLEAR_LOGCAT');
        elements.logcatTerminal.innerHTML = '';
    });

    // Fault injection simulation triggers
    elements.btnSimNpe.addEventListener('click', () => {
        showToast('Simulating NullPointerException in target app...');
        triggerDiagnosis();
    });

    elements.btnSimAnr.addEventListener('click', () => {
        showToast('Simulating ANR Freeze dialog...');
        triggerDiagnosis();
    });

    elements.btnSimLeak.addEventListener('click', () => {
        showToast('Simulating Activity memory leak...');
        triggerDiagnosis();
    });

    // Device selection dropdown listener: updates active target ADB device and requests keyframe
    if (elements.deviceSelect) {
        elements.deviceSelect.addEventListener('change', async (e) => {
            const selectedSerial = e.target.value;
            if (!selectedSerial) return;
            try {
                const resp = await fetch('/api/device/select', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ serial: selectedSerial })
                });
                if (resp.ok) {
                    showToast(`Active device set to: ${selectedSerial}`);
                    if (streamWs && streamWs.readyState === WebSocket.OPEN) {
                        streamWs.send(JSON.stringify({ type: 'request_frame' }));
                    }
                }
            } catch (err) {
                console.error('Device selection error:', err);
                showToast(`Failed to switch device: ${err.message}`);
            }
        });
    }

    // Device Status Polling
    async function checkDeviceStatus() {
        try {
            const resp = await fetch('/api/devices');
            if (resp.ok) {
                const devices = await resp.json();
                if (devices.length > 0) {
                    elements.deviceDot.className = 'status-dot online';
                    elements.deviceLabel.textContent = `${devices.length} Device(s)`;

                    if (elements.deviceSelect) {
                        const currentVal = elements.deviceSelect.value;
                        elements.deviceSelect.innerHTML = '';
                        devices.forEach(dev => {
                            const opt = document.createElement('option');
                            opt.value = dev.serial;
                            opt.textContent = `${dev.model} (${dev.serial})`;
                            if (dev.serial === currentVal) {
                                opt.selected = true;
                            }
                            elements.deviceSelect.appendChild(opt);
                        });
                        elements.deviceSelect.style.display = 'inline-block';
                    }
                } else {
                    elements.deviceDot.className = 'status-dot offline';
                    elements.deviceLabel.textContent = 'No Devices Connected';
                    if (elements.deviceSelect) {
                        elements.deviceSelect.innerHTML = '<option value="">No Devices</option>';
                    }
                }
            }
        } catch (e) {
            elements.deviceDot.className = 'status-dot offline';
            elements.deviceLabel.textContent = 'ADB Offline';
        }
    }

    // Pre-populate GenAI triage view and diff view so tabs are never empty
    async function prePopulateTriageAndDiff() {
        try {
            const resp = await fetch('/api/diagnose', { method: 'POST' });
            if (resp.ok) {
                const report = await resp.json();
                renderDiagnosticReport(report);

                const patchResp = await fetch('/api/patch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(report)
                });
                if (patchResp.ok) {
                    const patchData = await patchResp.json();
                    renderPatchPrompts(patchData);
                }
            }
        } catch (e) {
            console.warn('Initial triage pre-population failed:', e);
        }
    }

    // Initialize WebSockets, poll status, and pre-populate views
    connectStreamWs();
    connectLogcatWs();
    connectControlWs();
    checkDeviceStatus();
    prePopulateTriageAndDiff();
    setInterval(checkDeviceStatus, 10000);
});

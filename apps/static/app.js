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
        llmDot: document.getElementById('llm-dot'),
        llmLabel: document.getElementById('llm-label'),
        llmSelect: document.getElementById('llm-select'),
        tokenLabel: document.getElementById('token-label'),
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
        repProviderBadge: document.getElementById('rep-provider-badge'),
        repLatencyBadge: document.getElementById('rep-latency-badge'),
        repTokensBadge: document.getElementById('rep-tokens-badge'),
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

    /**
     * Displays a temporary notification toast in the UI.
     * @param {string} message - Toast message text.
     * @param {number} [duration=3000] - Display duration in milliseconds.
     */
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

    /**
     * Establishes a WebSocket connection to /ws/logcat for streaming log events.
     * Automatically attempts reconnection every 2 seconds upon disconnection.
     */
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

    /**
     * Renders a structured LogEntry into the logcat terminal DOM container.
     * Applies level styling, searches/filters, and auto-scrolls if enabled.
     * @param {Object} entry - LogEntry payload from server.
     */
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

    /**
     * Sanitizes raw text string by escaping HTML special characters.
     * @param {string} text - Unescaped raw string.
     * @returns {string} Sanitized HTML-safe string.
     */
    function escapeHtml(text) {
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /**
     * Evaluates active log level and search query filters against a terminal log line.
     * Sets display style to 'flex' if matching or 'none' if excluded.
     * @param {HTMLElement} line - Terminal log line DOM element.
     */
    function applyFilterToLine(line) {
        const level = line.dataset.level;
        const tag = line.dataset.tag;
        const msg = line.dataset.msg;

        const levelMatches = (state.logLevelFilter === 'ALL' || state.logLevelFilter === level);
        const searchMatches = (!state.logSearchQuery || tag.includes(state.logSearchQuery) || msg.includes(state.logSearchQuery));

        line.style.display = (levelMatches && searchMatches) ? 'flex' : 'none';
    }

    /**
     * Re-evaluates visibility for all rendered log lines against current filters.
     */
    function refilterAllLogs() {
        const lines = elements.logcatTerminal.querySelectorAll('.log-line');
        lines.forEach(line => applyFilterToLine(line));
    }

    // 3. Control WebSocket
    let controlWs = null;

    /**
     * Establishes a bidirectional full-duplex control WebSocket channel (/ws/control).
     * Handles incoming diagnostic reports, LLM status updates, and command acknowledgments.
     */
    function connectControlWs() {
        controlWs = new WebSocket(`${protocol}//${host}/ws/control`);
        controlWs.onopen = () => console.log('Control WebSocket connected');
        controlWs.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'diagnostic_report' && data.payload) {
                    renderDiagnosticReport(data.payload, data.usage);
                } else if (data.type === 'llm_status') {
                    fetchLlmStatus();
                } else if (data.type === 'command_ack') {
                    showToast(`Command executed: ${data.command_type}`);
                }
            } catch (err) {
                console.error('Error handling control ws msg:', err);
            }
        };
        controlWs.onclose = () => setTimeout(connectControlWs, 2000);
    }

    /**
     * Dispatches a structured DeviceCommand to the server via WebSocket with REST fallback.
     * @param {string} type - Command action type (e.g. 'RESTART_PACKAGE', 'TOGGLE_STREAM').
     * @param {Object} [payload={}] - Optional command parameters.
     */
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

    /**
     * Triggers end-to-end multimodal GenAI diagnosis via /api/diagnose and fetches patch prompts.
     */
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

    /**
     * Populates the diagnostics UI tab with findings from a DiagnosticReport.
     * Updates confidence meters, affected components list, and LLM telemetry badges.
     * @param {Object} report - Validated DiagnosticReport model.
     * @param {Object|null} [usage=null] - Optional LLMUsageMetadata telemetry.
     */
    function renderDiagnosticReport(report, usage = null) {
        state.latestReport = report;
        elements.diagPlaceholder.style.display = 'none';
        elements.reportContent.style.display = 'flex';

        elements.repId.textContent = report.report_id;
        elements.repTime.textContent = report.timestamp.replace('T', ' ').substring(0, 19);
        const confPercent = Math.round(report.confidence_score * 100);
        elements.repConfFill.style.width = `${confPercent}%`;
        elements.repConfVal.textContent = `${confPercent}%`;

        // Update LLM usage badges if present
        const repUsage = usage || report.usage;
        if (repUsage) {
            if (elements.repProviderBadge) {
                elements.repProviderBadge.textContent = formatProviderDisplay(repUsage.provider, repUsage.model_name);
            }
            if (elements.repLatencyBadge) {
                elements.repLatencyBadge.textContent = `Latency: ${(repUsage.latency_ms / 1000).toFixed(2)}s`;
            }
            if (elements.repTokensBadge) {
                elements.repTokensBadge.textContent = `Tokens: ${repUsage.prompt_tokens} in / ${repUsage.completion_tokens} out (${repUsage.total_tokens} total)`;
            }
            fetchLlmStatus();
        }

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

    /**
     * Renders IDE patch prompts and unified diffs in the Patch Preview tab.
     * @param {Object} prompts - Dictionary containing 'cursor' and 'antigravity' prompts.
     */
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
    /**
     * Queries connected ADB device list from /api/devices and updates status indicators.
     */
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

    /**
     * Formats a user-friendly badge label for the active LLM provider and model.
     * @param {string} provider - Provider name identifier.
     * @param {string} model - Model identifier.
     * @returns {string} Formatted badge string with emoji icon.
     */
    function formatProviderDisplay(provider, model) {
        if (!provider) return '⚡ VLM';
        const p = provider.toLowerCase();
        if (p === 'gemini') return `⚡ Gemini (${model || '2.0 Flash'})`;
        if (p === 'claude' || p === 'anthropic') return `⚡ Claude (${model || '3.5 Sonnet'})`;
        if (p === 'openai') return `⚡ GPT (${model || '4o'})`;
        return `⚡ Mock VLM (${model || 'Offline'})`;
    }

    /**
     * Fetches current LLM provider, active model, and token counters from /api/llm/status.
     */
    async function fetchLlmStatus() {
        try {
            const resp = await fetch('/api/llm/status');
            if (!resp.ok) return;
            const data = await resp.json();
            updateLlmUi(data);
        } catch (err) {
            console.error('Failed to fetch LLM status:', err);
        }
    }

    /**
     * Updates header status pills and provider selection dropdown with LLM telemetry.
     * @param {Object} data - LLM status payload from /api/llm/status.
     */
    function updateLlmUi(data) {
        if (!data) return;
        if (elements.llmLabel) {
            elements.llmLabel.textContent = formatProviderDisplay(data.active_provider, data.active_model);
        }
        if (elements.tokenLabel && data.cumulative_tokens) {
            const { prompt_tokens = 0, completion_tokens = 0, total_tokens = 0 } = data.cumulative_tokens;
            elements.tokenLabel.textContent = `Tokens: ${prompt_tokens.toLocaleString()} in / ${completion_tokens.toLocaleString()} out (${total_tokens.toLocaleString()} total)`;
        }
        if (elements.llmSelect && data.available_providers) {
            const currentVal = elements.llmSelect.value;
            elements.llmSelect.innerHTML = '';
            data.available_providers.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p.provider;
                opt.textContent = p.name + (p.available ? '' : ' (No Key)');
                if (p.provider === data.active_provider) {
                    opt.selected = true;
                }
                elements.llmSelect.appendChild(opt);
            });
            if (!currentVal && data.active_provider) {
                elements.llmSelect.value = data.active_provider;
            }
        }
    }

    if (elements.llmSelect) {
        elements.llmSelect.addEventListener('change', async (e) => {
            const provider = e.target.value;
            try {
                const resp = await fetch('/api/llm/select', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider: provider })
                });
                if (resp.ok) {
                    const result = await resp.json();
                    showToast(`Active LLM switched to ${result.active_provider}`);
                    fetchLlmStatus();
                }
            } catch (err) {
                showToast(`Failed to switch LLM provider: ${err.message}`);
            }
        });
    }

    /**
     * Pre-populates the diagnostics and patch views with initial triage data on load.
     */
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
    fetchLlmStatus();
    prePopulateTriageAndDiff();
    setInterval(checkDeviceStatus, 10000);
    setInterval(fetchLlmStatus, 15000);
});
